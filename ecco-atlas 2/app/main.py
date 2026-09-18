from __future__ import annotations

import csv
import io
import logging
import math
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, derived, imaging, lending, workbench
from . import normalize as N

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ecco")
STATIC = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("cache dir %s not writable; crops will be refetched", config.CACHE_DIR)
    log.info("started; image_base=%s cache=%s (%d items)", config.IMAGE_BASE,
             config.CACHE_DIR, config.CACHE_MAX_ITEMS)
    yield
    db.close_pool()


app = FastAPI(title=config.SITE_TITLE, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
T = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))
signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="admin")


# ================================================================= template helpers
def crop_url(oid, w=None):
    return f"/img/crop/{oid}.jpg" + (f"?w={w}" if w else "")


def class_url(src, level, value):
    return f"/class/{src}/{level}/{quote(str(value), safe='/')}"


def class_img(src, level, value):
    return f"/img/class/{src}/{level}/{quote(str(value), safe='/')}.jpg"


def most_specific(o):
    """(src, level, value) of the finest human class of an ornament, or None."""
    if not o.get("ann_src") or not o.get("class_path"):
        return None
    levels = config.SOURCES[o["ann_src"]]["levels"]
    if o.get("variant") and "variant" in levels:
        return o["ann_src"], "variant", o["class_path"]
    if o.get("subclass") and "subclass" in levels:
        return o["ann_src"], "subclass", o["subclass"]
    return o["ann_src"], "superclass", o["superclass"]


def cluster_of(o):
    if o.get("hc_cluster") and not o.get("cluster_rejected") and o.get("pred_src"):
        return o["pred_src"], "cluster", o["hc_cluster"]
    return None


def orn_class_url(o):
    t = most_specific(o) or cluster_of(o)
    return class_url(*t) if t else None


def pct(v, digits=0):
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def logos():
    d = STATIC / "logos"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".webp"})


T.env.globals.update(
    cfg=config, crop_url=crop_url, class_url=class_url, class_img=class_img,
    plate_url=N.plate_url, plate_label=N.plate_label, class_label=N.class_label,
    humanize=N.humanize, orn_class_url=orn_class_url, viewer_url=imaging.viewer_url,
    KIND_COLOR=config.KIND_COLOR, KIND_LABEL=config.KIND_LABEL, SOURCES=config.SOURCES,
    VERDICTS=lending.VERDICTS, pct=pct)


# ================================================================= request helpers
def admin_user(request: Request) -> str | None:
    tok = request.cookies.get(config.SESSION_COOKIE)
    if not tok:
        return None
    try:
        return signer.loads(tok, max_age=config.SESSION_MAX_AGE).get("u") or "admin"
    except (BadSignature, AttributeError):
        return None


def need_admin(request: Request) -> str:
    u = admin_user(request)
    if not u:
        raise HTTPException(403, "admin only")
    return u


def page(request: Request, tpl: str, status_code=200, **ctx):
    u = admin_user(request)
    ctx.setdefault("admin", u)
    if u:
        try:
            ctx.setdefault("n_open", db.open_report_count())
        except Exception:  # noqa: BLE001
            ctx.setdefault("n_open", 0)
    ctx.setdefault("logos", logos())
    return T.TemplateResponse(request, tpl, ctx, status_code=status_code)


def selected_sources(request: Request):
    """Source checkboxes: absent -> every source that has data."""
    avail = list(db.sources_available())
    qp = request.query_params
    if "srcset" not in qp and "src" not in qp:
        return avail, avail, ""
    sel = [s for s in qp.getlist("src") if s in avail]
    qs = urlencode([("srcset", "1")] + [("src", s) for s in sel])
    return sel, avail, qs


def pager(page_no, pages, width=3):
    if pages <= 1:
        return []
    keep = {1, pages} | set(range(max(1, page_no - width), min(pages, page_no + width) + 1))
    out, last = [], 0
    for i in sorted(keep):
        if i - last > 1:
            out.append(None)
        out.append(i)
        last = i
    return out


def strip_points(rows, total_pages):
    tp = total_pages or max((r["page"] for r in rows), default=1) or 1
    pts = {k: [] for k in config.KINDS}
    for r in rows:
        pts.setdefault(r["kind"], []).append(
            dict(pct=min(100.0, max(0.0, 100.0 * r["page"] / tp)), oid=r["oid"],
                 page=r["page"], n=r["n"], label=r["label"] or ""))
    return pts, tp


# ================================================================= front page
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    avail = db.sources_available()
    cc = db.class_counts()
    rows = []
    for s, spec in config.SOURCES.items():
        if s not in avail:
            continue
        plural = {"superclass": "superclasses", "subclass": "subclasses",
                  "variant": "variants", "cluster": "clusters"}
        n_cls = [dict(level=lv, n=cc.get((s, lv), 0),
                      text=f"{cc.get((s, lv), 0):,} {lv if cc.get((s, lv), 0) == 1 else plural[lv]}")
                 for lv in spec["levels"] if cc.get((s, lv), 0)]
        rows.append(dict(src=s, spec=spec, n=avail[s]["n"], n_books=avail[s]["n_books"],
                         classes=n_cls))
    return page(request, "index.html", sources=rows, summary=db.site_summary(),
                kinds=db.kind_counts(), hero=(STATIC / config.HERO_IMAGE).exists())


# ================================================================= books
@app.get("/books", response_class=HTMLResponse)
def books(request: Request, q: str = "", kind: str = "", tonson: int = 0,
          page_no: int = Query(1, alias="p", ge=1)):
    limit = 30
    rows, total = db.search_books(q, limit, (page_no - 1) * limit,
                                  kind if kind in config.KINDS else None, bool(tonson))
    st = db.strips([r["book_id"] for r in rows])
    strips = {r["book_id"]: strip_points(st[r["book_id"]], r["total_pages"]) for r in rows}
    pages = math.ceil(total / limit)
    base = "/books?" + urlencode({"q": q, "kind": kind, "tonson": tonson})
    return page(request, "books.html", rows=rows, total=total, q=q, kind=kind, tonson=tonson,
                page_no=page_no, pager=pager(page_no, pages), pages=pages, base=base,
                strips=strips)


@app.get("/book/{book_id}", response_class=HTMLResponse)
def book_detail(request: Request, book_id: str):
    b = db.get_book(book_id)
    if not b:
        raise HTTPException(404, "book not found")
    sel, avail, sq = selected_sources(request)
    pts, tp = strip_points(db.strips([book_id])[book_id], b["total_pages"])
    return page(request, "book.html", b=b, agents=db.book_agents(book_id), points=pts, tp=tp,
                orns=db.book_ornaments(book_id),
                similar=db.similar_books(book_id, sel, 10) if sel else [],
                sel=sel, avail=avail, sq=sq)


# ================================================================= ornaments
@app.get("/ornament/{oid}", response_class=HTMLResponse)
def ornament_detail(request: Request, oid: str, sim: str = ""):
    o = db.get_ornament(oid)
    if not o:
        target = db.resolve_alias(oid)
        if target:
            return RedirectResponse(f"/ornament/{target}", status_code=301)
        raise HTTPException(404, "ornament not found")
    has_ann = bool(o["ann_src"] and o["class_path"])
    has_cl = bool(o["hc_cluster"] and not o["cluster_rejected"])
    sim = sim if sim in ("hier", "cluster", "embed") else ("hier" if has_ann else "cluster")
    names = {}
    if has_ann:
        pairs = [("superclass", o["superclass"])] + (
            [("subclass", o["subclass"])] if o["subclass"] else [])
        names = db.names_for(o["ann_src"], pairs)
    tsrc = o["ann_src"] or config.ann_source_for_kind(o["kind"])
    return page(request, "ornament.html", o=o, sim=sim, has_ann=has_ann, has_cl=has_cl,
                similar=db.similar_ornaments(o, sim, 3),
                embed_ok=db.embeddings_available(),
                names=names, spec_cls=most_specific(o), cl=cluster_of(o),
                agents=db.book_agents(o["book_id"]) if o["book_id"] else [],
                suggestions=db.class_suggestions(tsrc, o["superclass"]) if tsrc else [],
                tsrc=tsrc, reported=request.query_params.get("reported"),
                report_err=request.query_params.get("err"))


@app.post("/ornament/{oid}/report")
def submit_report(oid: str, reporter: str = Form(""), suggestion: str = Form(""),
                  note: str = Form("")):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    if not suggestion.strip() and not note.strip():
        return RedirectResponse(f"/ornament/{oid}?err=1#report", status_code=303)
    db.add_report(oid, reporter.strip()[:80], o["label"] or "(unlabelled)",
                  o["ann_src"] or o["pred_src"], suggestion.strip()[:120], note.strip()[:2000])
    return RedirectResponse(f"/ornament/{oid}?reported=1#report", status_code=303)


# ================================================================= classes
def tax_options():
    cc = db.class_counts()
    out = []
    for s, spec in config.SOURCES.items():
        for lv in spec["levels"]:
            if cc.get((s, lv)):
                out.append(dict(src=s, level=lv, n=cc[(s, lv)], family=spec["family"],
                                label=(f"{spec['kind']} · {lv}" if spec["family"] == "ann"
                                       else f"{spec['kind']} clusters")))
    return out


@app.get("/classes", response_class=HTMLResponse)
def classes(request: Request, tax: str = "", q: str = "", sort: str = "n", dir: str = "",
            page_no: int = Query(1, alias="p", ge=1)):
    opts = tax_options()
    keys = [f"{o['src']}:{o['level']}" for o in opts]
    if tax not in keys:
        tax = "HP-ann:subclass" if "HP-ann:subclass" in keys else (keys[0] if keys else "")
    if not tax:
        return page(request, "classes.html", opts=[], rows=[], tax="", q=q, total=0,
                    sort=sort, desc=True, pager=[], page_no=1, base="/classes?", hc=None)
    src, level = tax.split(":")
    sort = sort if sort in db.SORTS else "n"
    desc = (dir != "asc") if dir else sort not in ("name", "first")
    limit = 100
    rows, total = db.browse_classes(src, level, q, sort, desc, limit, (page_no - 1) * limit)
    hc = None
    if level == "cluster" and rows:
        r = db.q("SELECT hc_column FROM ornament WHERE pred_src = %s AND hc_column IS NOT NULL "
                 "LIMIT 1", (src,), one=True)
        hc = r["hc_column"] if r else None
    base = "/classes?" + urlencode({"tax": tax, "q": q})
    return page(request, "classes.html", opts=opts, rows=rows, tax=tax, src=src, level=level,
                q=q, total=total, sort=sort, desc=desc, page_no=page_no,
                pager=pager(page_no, math.ceil(total / limit)),
                base=base + f"&sort={sort}&dir={'desc' if desc else 'asc'}", sort_base=base,
                hc=hc)


def composition(rows):
    by_role = {"publisher": {}, "printer": {}}
    known = set()
    for r in rows:
        if r["agent_id"] is None:
            continue
        known.add(r["book_id"])
        d = by_role[r["role"]].setdefault(r["agent_id"], dict(
            agent_id=r["agent_id"], name=r["name"], display=r["display_name"] or r["name"], n=0))
        d["n"] += 1
    out = {}
    for role, d in by_role.items():
        tot = sum(v["n"] for v in d.values()) or 1
        items = sorted(d.values(), key=lambda v: (-v["n"], v["name"]))
        for v in items:
            v["coverage"] = v["n"] / max(1, len(known))
            v["credit"] = v["n"] / tot
        out[role] = items
    return out, len(known)


@app.get("/class/{a}/{b}", response_class=HTMLResponse)
def legacy_class(a: str, b: str):
    if a in ("superclass", "subclass"):
        return RedirectResponse(class_url("HP-ann", a, b), status_code=301)
    raise HTTPException(404)


@app.get("/class/{src}/{level}/{value:path}", response_class=HTMLResponse)
def class_detail(request: Request, src: str, level: str, value: str, order: str = "year",
                 page_no: int = Query(1, alias="p", ge=1)):
    if src in ("superclass", "subclass", "variant"):      # v1 URL
        return RedirectResponse(class_url("HP-ann", src, f"{level}/{value}"
                                          if src == "variant" else value), status_code=301)
    spec = config.SOURCES.get(src)
    if not spec or level not in spec["levels"]:
        raise HTTPException(404, "unknown class level")
    row = db.class_row(src, level, value)
    if not row:
        raise HTTPException(404, "class not found")
    limit = 120
    order = order if order in ("year", "group") and level in db.GROUP_COL else "year"
    members = db.class_members(src, level, value, order, limit, (page_no - 1) * limit)
    sup = sub = None
    if spec["family"] == "ann":
        if level == "superclass":
            sup = value
        elif level == "subclass":
            sup = members[0]["superclass"] if members else None
            sub = value
        else:
            sup, sub, _ = workbench.split_path(src, value)
    names = db.names_for(src, [p for p in (("superclass", sup), ("subclass", sub)) if p[1]])
    rows = db.class_book_agents(src, level, value)
    comp, n_known = composition(rows)
    params = lending.Params.from_query(request.query_params)
    lend = lending.analyse_rows(rows, params) if row["plate_key"] else None
    ag = db.agents_by_id({e["borrower"] for e in lend.events} | set(lend.owners)) if lend else {}
    pages = math.ceil(row["n"] / limit)
    return page(request, "class.html", src=src, level=level, value=value, spec=spec, row=row,
                members=members, order=order, sup=sup, sub=sub, names=names,
                comp=comp, n_known=n_known, lend=lend, lend_agents=ag, params=params,
                owners=set(lend.owners) if lend and lend.eligible else set(),
                borrowers=set(lend.borrowers) if lend else set(),
                struct=db.class_structure(src, level, value, sup, sub),
                xref=db.class_crossref(src, level, value),
                page_no=page_no, pager=pager(page_no, pages),
                base=class_url(src, level, value) + f"?order={order}",
                title=N.class_label(src, level, value))


# ================================================================= agents
@app.get("/agents", response_class=HTMLResponse)
def agents(request: Request, q: str = "", sort: str = "books", dir: str = ""):
    sel, avail, sq = selected_sources(request)
    sort = sort if sort in db.AGENT_SORTS else "books"
    desc = (dir != "asc") if dir else sort != "name"
    return page(request, "agents.html", rows=db.agents_list(q, sel, sort, desc), q=q,
                sel=sel, avail=avail, sq=sq, sort=sort, desc=desc)


@app.get("/agent/{name}", response_class=HTMLResponse)
def agent_detail(request: Request, name: str, view: str = "used", joint: int = 0):
    a = db.get_agent(name)
    if not a:
        raise HTTPException(404, "house not found")
    sel, avail, sq = selected_sources(request)
    view = view if view in ("used", "owned") else "used"
    used = db.agent_plates(a["agent_id"], sel)
    mb = config.OWNER_MIN_BOOKS

    def owns(r, tie):
        return r["n_known"] >= mb and (r["share"] >= r["other_max"] if tie
                                       else r["share"] > r["other_max"])
    owned = [r for r in used if owns(r, bool(joint))]
    plates = owned if view == "owned" else used
    n_used, n_owned = len(used), sum(1 for r in used if owns(r, False))
    ev = lending.find_events(lending.Params(), sel, agent_id=a["agent_id"]) if sel else []
    lent = sum(1 for e in ev if a["agent_id"] in e["owners"])
    borrowed = sum(1 for e in ev if e["borrower"] == a["agent_id"])
    return page(request, "agent.html", a=a, stats=db.agent_stats(a["agent_id"], sel),
                plates=plates, view=view, joint=joint, n_used=n_used, n_owned=n_owned,
                similar=db.similar_agents(a["agent_id"], sel) if sel else [],
                cohold=db.coholders(a["agent_id"], sel) if sel else [],
                lent=lent, borrowed=borrowed, sel=sel, avail=avail, sq=sq)


@app.get("/agents/pair/{a}/{b}", response_class=HTMLResponse)
def agent_pair(request: Request, a: str, b: str, page_no: int = Query(1, alias="p", ge=1)):
    A, B = db.get_agent(a), db.get_agent(b)
    if not A or not B:
        raise HTTPException(404, "house not found")
    sel, avail, sq = selected_sources(request)
    shared = db.pair_shared_plates(A["agent_id"], B["agent_id"], sel) if sel else []
    limit = 120
    joint, n_joint, n_joint_books = (db.pair_joint(A["agent_id"], B["agent_id"], sel, limit,
                                                   (page_no - 1) * limit)
                                     if sel else ([], 0, 0))
    co = sum(r["co"] for r in shared if r["share_a"] >= config.COHOLD_MIN_SHARE
             and r["share_b"] >= config.COHOLD_MIN_SHARE
             and r["n_known"] >= config.OWNER_MIN_BOOKS)
    return page(request, "pair.html", A=A, B=B, shared=shared, joint=joint, n_joint=n_joint,
                n_joint_books=n_joint_books, co=co, sel=sel, avail=avail, sq=sq,
                page_no=page_no, pager=pager(page_no, math.ceil(n_joint / limit)),
                base=f"/agents/pair/{a}/{b}?{sq}")


# ================================================================= lending
def lending_context(request: Request):
    sel, avail, sq = selected_sources(request)
    p = lending.Params.from_query(request.query_params)
    agent = request.query_params.get("agent", "")
    plate = request.query_params.get("plate", "")
    A = db.get_agent(agent) if agent else None
    events = lending.find_events(p, sel, A["agent_id"] if A else None, plate or None) \
        if sel else []
    return sel, avail, sq, p, A, plate, events


@app.get("/lending", response_class=HTMLResponse)
def lending_page(request: Request, page_no: int = Query(1, alias="p", ge=1)):
    sel, avail, sq, p, A, plate, events = lending_context(request)
    ids = {e["borrower"] for e in events} | {o for e in events for o in e["owners"]}
    ag = db.agents_by_id(ids)
    reviews = db.lending_reviews({e["plate"] for e in events})
    counts = {k: sum(1 for e in events if e["verdict"] == k) for k in lending.VERDICTS}
    limit = 100
    shown = events[(page_no - 1) * limit: page_no * limit]
    qs = p.as_query() + (f"&{sq}" if sq else "") + (f"&agent={A['name']}" if A else "") + \
        (f"&plate={quote(plate)}" if plate else "")
    return page(request, "lending.html", events=shown, n_events=len(events),
                n_plates=len({e["plate"] for e in events}), counts=counts, ag=ag,
                reviews=reviews, p=p, A=A, plate=plate, sel=sel, avail=avail, sq=sq, qs=qs,
                page_no=page_no, pager=pager(page_no, math.ceil(len(events) / limit)),
                base="/lending?" + qs)


@app.get("/lending.csv")
def lending_csv(request: Request):
    sel, avail, sq, p, A, plate, events = lending_context(request)
    ag = db.agents_by_id({e["borrower"] for e in events} | {o for e in events for o in e["owners"]})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["plate", "label", "owners", "owner_coverage", "borrower", "borrower_coverage",
                "book_id", "title", "year", "owner_first_year", "owner_last_year",
                "owner_median_year", "gap_years", "verdict", "books_with_imprint"])
    for e in events:
        w.writerow([e["plate"], N.plate_label(e["plate"]),
                    "+".join(ag[o]["name"] for o in e["owners"] if o in ag),
                    "+".join(f"{s:.3f}" for s in e["owner_shares"]),
                    ag.get(e["borrower"], {}).get("name"), f"{e['borrower_share']:.3f}",
                    e["book_id"], e["title"], e["year"], e["owner_span"][0], e["owner_span"][1],
                    e["owner_median"], e["gap"], e["verdict"], e["n_known"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=lending_events.csv"})


@app.post("/admin/lending/review")
def lending_review(request: Request, plate: str = Form(...), owner_ids: str = Form(...),
                   borrower_id: int = Form(...), book_id: str = Form(...),
                   status: str = Form(...), back: str = Form("/lending")):
    who = need_admin(request)
    if status not in ("confirmed", "rejected", "clear"):
        raise HTTPException(400)
    db.set_lending_review(plate, owner_ids, borrower_id, book_id, status, who)
    return RedirectResponse(back if back.startswith("/") else "/lending", status_code=303)


# ================================================================= images
def _img(data, ok_age=604800):
    if data is None:
        return Response(imaging.placeholder_svg(), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=60"})
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": f"public, max-age={ok_age}"})


@app.get("/img/crop/{oid}.jpg")
def img_crop(oid: str, w: int = Query(None, ge=32, le=2000)):
    o = db.get_ornament_min(oid)
    if not o:
        raise HTTPException(404)
    return _img(imaging.get_crop(o, w), ok_age=86400)


@app.get("/img/class/{src}/{level}/{value:path}.jpg")
def img_class(src: str, level: str, value: str, w: int = Query(None, ge=32, le=2000)):
    if src not in config.SOURCES:
        raise HTTPException(404)
    oid = db.class_exemplar(src, level, value)
    o = db.get_ornament_min(oid) if oid else None
    if not o:
        raise HTTPException(404)
    return _img(imaging.get_crop(o, w), ok_age=3600)


@app.get("/img/page/{oid}")
def img_page(oid: str):
    o = db.get_ornament_min(oid)
    if not o:
        raise HTTPException(404)
    return RedirectResponse(imaging.page_url(o["image_id"]))


# ================================================================= JSON API
@app.get("/api/book/{book_id}/strip")
def api_strip(book_id: str):
    b = db.get_book(book_id)
    if not b:
        raise HTTPException(404)
    pts, tp = strip_points(db.strips([book_id])[book_id], b["total_pages"])
    return {"book_id": book_id, "total_pages": tp,
            "rows": [{"kind": k, "color": config.KIND_COLOR.get(k), "points": pts.get(k, [])}
                     for k in config.KINDS]}


@app.get("/api/ornament/{oid}")
def api_ornament(oid: str):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    o = dict(o)
    o.update(crop_url=crop_url(oid), page_url=imaging.page_url(o["image_id"]),
             class_url=orn_class_url(o))
    return o


@app.get("/api/search/books")
def api_search_books(q: str = "", limit: int = 20):
    rows, total = db.search_books(q, min(max(limit, 1), 100))
    return {"total": total, "results": rows}


# ================================================================= admin
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    return page(request, "admin_login.html", err=None, name="")


def check_password(name: str, password: str) -> bool:
    users = dict(p.split(":", 1) for p in config.ADMIN_USERS.split(",") if ":" in p)
    if name in users:
        return password == users[name]
    return bool(config.ADMIN_PASSWORD) and password == config.ADMIN_PASSWORD


@app.post("/admin/login")
def admin_login(request: Request, name: str = Form(""), password: str = Form("")):
    name = name.strip()[:40]
    if not name or not check_password(name, password):
        return page(request, "admin_login.html", err="Name or password not accepted.",
                    name=name, status_code=401)
    r = RedirectResponse("/admin", status_code=303)
    r.set_cookie(config.SESSION_COOKIE, signer.dumps({"u": name}), httponly=True,
                 samesite="lax", secure=request.url.scheme == "https",
                 max_age=config.SESSION_MAX_AGE)
    return r


@app.get("/admin/logout")
def admin_logout():
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(config.SESSION_COOKIE)
    return r


def wb_options():
    return [o for o in tax_options() if o["level"] in ("superclass", "subclass", "cluster")]


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    if not admin_user(request):
        return RedirectResponse("/admin/login", status_code=303)
    return page(request, "admin.html", changes=db.recent_changes(30), meta=db.meta(),
                crops=db.crop_store_stats(), lru=imaging.lru_count(), wb=wb_options(),
                wb_err=request.query_params.get("wb_err"))


@app.get("/admin/workbench")
def admin_workbench_go(request: Request, tax: str = "", value: str = ""):
    need_admin(request)
    if ":" not in tax or not value.strip():
        return RedirectResponse("/admin?wb_err=1", status_code=303)
    src, level = tax.split(":", 1)
    return RedirectResponse(f"/admin/workbench/{src}/{level}/{quote(value.strip(), safe='/')}",
                            status_code=303)


@app.post("/admin/refresh")
def admin_refresh(request: Request):
    need_admin(request)
    derived.refresh_async()
    db.clear_cache()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/reports", response_class=HTMLResponse)
def admin_reports(request: Request, status: str = "open"):
    if not admin_user(request):
        return RedirectResponse("/admin/login", status_code=303)
    status = status if status in ("open", "accepted", "rejected", "all") else "open"
    reports = db.list_reports(status)
    for r in reports:
        cur = most_specific(r) or cluster_of(r)
        r["cur_cls"] = cur
        r["cur_samples"] = [s for s in db.class_samples(*cur, n=4) if s["oid"] != r["oid"]][:3] \
            if cur else []
        r["tsrc"] = r["ann_src"] or config.ann_source_for_kind(r["kind"])
        r["sug_cls"], r["sug_samples"], r["sug_err"] = None, [], None
        if r["suggestion"] and r["tsrc"]:
            try:
                sup, sub, var = N.parse_label(r["tsrc"], r["suggestion"], r["superclass"],
                                              r["subclass"])
                path = N.class_path_for(r["tsrc"], sup, sub, var)
                lv = "variant" if var else ("subclass" if sub else "superclass")
                val = path if lv == "variant" else (sub if lv == "subclass" else sup)
                r["sug_cls"] = (r["tsrc"], lv, val)
                r["sug_samples"] = db.class_samples(r["tsrc"], lv, val, 3)
            except ValueError as e:
                r["sug_err"] = str(e)
    return page(request, "admin_reports.html", reports=reports, status=status)


@app.post("/admin/report/{report_id}")
def admin_resolve(request: Request, report_id: int, action: str = Form(...),
                  label: str = Form("")):
    who = need_admin(request)
    r = db.get_report(report_id)
    if not r or r["status"] != "open":
        return RedirectResponse("/admin/reports", status_code=303)
    if action == "reject":
        db.close_report(report_id, "rejected", who)
        return RedirectResponse("/admin/reports", status_code=303)
    o = db.get_ornament(r["oid"])
    tsrc = o["ann_src"] or config.ann_source_for_kind(o["kind"])
    if not tsrc or not label.strip():
        return RedirectResponse(f"/admin/reports?err={report_id}", status_code=303)
    try:
        sup, sub, var = N.parse_label(tsrc, label, o["superclass"], o["subclass"])
    except ValueError:
        return RedirectResponse(f"/admin/reports?err={report_id}", status_code=303)
    db.apply_changes([dict(oid=o["oid"], action="relabel", ann_src=tsrc, superclass=sup,
                           subclass=sub, variant=var)], who, report_id=report_id)
    db.close_report(report_id, "accepted", who, N.class_path_for(tsrc, sup, sub, var))
    derived.refresh_async()
    db.clear_cache()
    return RedirectResponse("/admin/reports", status_code=303)


@app.get("/admin/workbench/{src}/{level}/{value:path}", response_class=HTMLResponse)
def admin_workbench(request: Request, src: str, level: str, value: str):
    if not admin_user(request):
        return RedirectResponse("/admin/login", status_code=303)
    spec = config.SOURCES.get(src)
    if not spec or level not in spec["levels"] or level == "variant":
        raise HTTPException(404, "the workbench opens superclasses, subclasses and clusters")
    wb = workbench.build(src, level, value)
    if not wb["members"]:
        raise HTTPException(404, "class not found")
    names = {}
    if spec["family"] == "ann" and level == "superclass":
        names = db.names_for(src, [("superclass", value)])
    return page(request, "workbench.html", src=src, level=level, value=value, spec=spec, wb=wb,
                names=names, title=N.class_label(src, level, value))


@app.post("/admin/workbench/save")
async def admin_workbench_save(request: Request):
    who = need_admin(request)
    body = await request.json()
    src, level, value = body.get("src"), body.get("level"), body.get("value")
    if src not in config.SOURCES or level not in config.SOURCES[src]["levels"]:
        return JSONResponse({"ok": False, "errors": ["unknown class"]}, status_code=400)
    n, errors = workbench.apply(src, level, value, body.get("moves") or {},
                                body.get("names") or {}, who)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    if n:
        derived.refresh_async()
        db.clear_cache()
    return {"ok": True, "saved": n}


# ================================================================= health & errors
@app.get("/healthz")
def healthz():
    try:
        db.q("SELECT 1 AS ok", one=True)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(("/api/", "/img/")) or request.method != "GET":
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return page(request, "error.html", status_code=exc.status_code, code=exc.status_code,
                detail=exc.detail)
