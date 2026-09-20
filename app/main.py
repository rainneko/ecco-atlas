from __future__ import annotations

import csv
import io
import logging
import math
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import compare, config, db, derived, embedding, imaging, lending, places, workbench
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
    embedding.engine.warmup()          # loads the model in the background (§9.1)
    yield
    db.close_pool()


from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

app = FastAPI(title=config.SITE_TITLE, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
T = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))
signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="admin")
if config.SECRET_KEY == "dev-only-change-me":
    logging.getLogger("ecco").warning("SECRET_KEY is the development default; set it on the Deployment")
if not (config.ADMIN_PASSWORD or ":" in config.ADMIN_USERS):
    logging.getLogger("ecco").warning("no ADMIN_PASSWORD or ADMIN_USERS set: admin login is disabled")


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


def _asset_version():
    """Changes whenever a file under static/ changes, so browsers never keep
    an old stylesheet or script after a deploy."""
    import hashlib
    h = hashlib.sha1()
    root = Path(__file__).parent / "static"
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix in (".css", ".js", ".json"):
            h.update(f.name.encode()); h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()
T.env.globals.update(
    asset_v=ASSET_V,
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


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return page(request, "about.html", summary=db.site_summary())


# ================================================================= books
@app.get("/books", response_class=HTMLResponse)
def books(request: Request, q: str = "", kind: str = "", tonson: int = 0, place: str = "",
          sort: str = "", dir: str = "", page_no: int = Query(1, alias="p", ge=1)):
    limit = 30
    sort = sort if sort in db.BOOK_SORTS else ""
    desc = (dir == "desc") if dir else sort == "n"
    rows, total, fixed = db.search_books(q, limit, (page_no - 1) * limit,
                                         kind if kind in config.KINDS else None, bool(tonson),
                                         sort, desc, place or None)
    ids = [r["book_id"] for r in rows]
    st = db.strips(ids)
    strips = {r["book_id"]: strip_points(st[r["book_id"]], r["total_pages"]) for r in rows}
    pages = math.ceil(total / limit)
    filt = {"q": q, "kind": kind, "tonson": tonson, "place": place}
    sort_base = "/books?" + urlencode(filt)
    base = sort_base + f"&sort={sort}&dir={'desc' if desc else 'asc'}"
    return page(request, "books.html", rows=rows, total=total, q=q, kind=kind, tonson=tonson,
                place=place, places=db.places(), fixed=fixed, sort=sort, desc=desc,
                sort_base=sort_base, page_no=page_no, pager=pager(page_no, pages), pages=pages,
                base=base, strips=strips, agents=db.book_agents_many(ids))


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
                pairs=db.book_pairs_for(book_id), sel=sel, avail=avail, sq=sq)


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


def pie_slices(items, total, r=70, cx=80, cy=80, max_slices=8):
    """SVG path for each slice (server-drawn pie, §12.2). The tail beyond
    `max_slices` is merged into one "other places" slice."""
    import math as _m
    items = list(items)
    if len(items) > max_slices:
        rest = items[max_slices - 1:]
        items = items[:max_slices - 1] + [dict(key=None, place="other places",
                                               n=sum(i["n"] for i in rest), other=True)]
    out, a0 = [], -_m.pi / 2
    for i, it in enumerate(items):
        frac = it["n"] / max(total, 1)
        a1 = a0 + frac * 2 * _m.pi
        if frac >= 0.9999:
            d = f"M{cx},{cy - r} A{r},{r} 0 1,1 {cx - 0.01},{cy - r} Z"
        else:
            x0, y0 = cx + r * _m.cos(a0), cy + r * _m.sin(a0)
            x1, y1 = cx + r * _m.cos(a1), cy + r * _m.sin(a1)
            large = 1 if frac > 0.5 else 0
            d = f"M{cx},{cy} L{x0:.2f},{y0:.2f} A{r},{r} 0 {large},1 {x1:.2f},{y1:.2f} Z"
        out.append(dict(it, d=d, i=i % 8, frac=frac))
        a0 = a1
    return out


def place_composition(rows):
    """Coverage by place over the class's books with a known place (§12.2)."""
    seen, per = {}, {}
    for r in rows:
        if r["book_id"] in seen:
            continue
        seen[r["book_id"]] = r.get("place_key")
    known = {b: k for b, k in seen.items() if k}
    for b, k in known.items():
        per.setdefault(k, dict(key=k, place=None, n=0))["n"] += 1
    for r in rows:
        if r.get("place_key") in per and not per[r["place_key"]]["place"]:
            per[r["place_key"]]["place"] = r["place"]
    items = sorted(per.values(), key=lambda v: (-v["n"], v["key"]))
    for v in items:
        v["coverage"] = v["n"] / max(1, len(known))
    return dict(items=items, n_known=len(known), n_books=len(seen),
                slices=pie_slices(items, len(known)) if known else [])


@app.get("/class/{a}/{b}", response_class=HTMLResponse)
def legacy_class(a: str, b: str):
    if a in ("superclass", "subclass"):
        return RedirectResponse(class_url("HP-ann", a, b), status_code=301)
    raise HTTPException(404)


@app.get("/class/{src}/{level}/{value:path}", response_class=HTMLResponse)
def class_detail(request: Request, src: str, level: str, value: str, order: str = "year",
                 place: str = "", agent: str = "", book: str = "",
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
    filt = None
    book = book if book.isalnum() else ""
    if place or agent or book:
        members, n_filt, n_filt_books = db.class_members_filtered(
            src, level, value, place or None, agent or None, None, limit, (page_no - 1) * limit,
            book or None)
        label = compare.describe(compare.Side(src, level, value, place=place, agent=agent, book=book))
        filt = dict(label=label.split(" · ", 1)[1] if " · " in label else label, n=n_filt,
                    n_books=n_filt_books, clear=class_url(src, level, value) + "#images")
        order = "year"
    else:
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
    place_mix = place_composition(rows)
    pparams = places.Params.from_query(request.query_params)
    contrast, group, links, prows = None, None, [], []
    if level in ("superclass", "subclass"):
        prows = db.design_place_rows(src, level, value)
    elif level == "cluster":
        links = db.cluster_links(src, value)
        group = db.cluster_group(src, value)
        if len(group) > 1:
            prows = db.cluster_group_place_rows(src, group)
    n_designs = len({r["design"] for r in prows})
    if n_designs >= 2:
        contrast = places.analyse(prows, pparams)          # None when no book has a place
    contrast_ex = {}
    if contrast:
        lv = {"superclass": "subclass", "subclass": "variant", "cluster": "cluster"}[level]
        for d in contrast["designs"]:
            d["level"], d["url_value"] = lv, d["design"]
            d["exemplar"] = db.class_exemplar(src, lv, d["design"])
        contrast_ex = {d["design"]: d for d in contrast["designs"]}
        for pr in contrast["pairs"]:
            A, B = contrast_ex[pr["a"]], contrast_ex[pr["b"]]
            pr["compare"] = compare.url(compare.Side(src, A["level"], A["url_value"]),
                                        compare.Side(src, B["level"], B["url_value"]))
    params = lending.Params.from_query(request.query_params)
    lend = lending.analyse_rows(rows, params) if row["plate_key"] else None
    ag = db.agents_by_id({e["borrower"] for e in lend.events} | set(lend.owners)) if lend else {}
    pages = math.ceil((filt["n"] if filt else row["n"]) / limit)
    curl = class_url(src, level, value)
    main_key = place_mix["items"][0]["key"] if place_mix["items"] else None
    main_name = place_mix["items"][0]["place"] if place_mix["items"] else None
    place_menus = {it["key"]: compare.place_menu(curl, src, level, value, it["key"], it["place"],
                                                 main_key, main_name)
                   for it in place_mix["items"]}
    house_menus = {m["agent_id"]: compare.house_menu(curl, src, level, value, m["name"], m["display"])
                   for role in ("publisher", "printer") for m in comp[role]}
    fq = urlencode({k: v for k, v in (("place", place), ("agent", agent), ("book", book)) if v})
    return page(request, "class.html", src=src, level=level, value=value, spec=spec, row=row,
                filt=filt, place_menus=place_menus, house_menus=house_menus,
                compare_url=compare.url(compare.Side(src, level, value)),
                has_coords=db.class_has_coords(src, level, value),
                class_key=f"{src}:{level}:{value}",
                n_designs=n_designs,
                members=members, order=order, sup=sup, sub=sub, names=names,
                comp=comp, n_known=n_known, places=place_mix, lend=lend, lend_agents=ag, params=params,
                contrast=contrast, contrast_ex=contrast_ex, pparams=pparams, group=group, links=links,
                owners=set(lend.owners) if lend and lend.eligible else set(),
                borrowers=set(lend.borrowers) if lend else set(),
                struct=db.class_structure(src, level, value, sup, sub),
                xref=db.class_crossref(src, level, value),
                page_no=page_no, pager=pager(page_no, pages),
                base=class_url(src, level, value) + f"?order={order}" + (f"&{fq}" if fq else ""),
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
    rows, total, fixed = db.search_books(q, min(max(limit, 1), 100))
    return {"total": total, "results": rows, "corrected_query": fixed}


# ================================================================= admin
@app.get("/admin/login", response_class=HTMLResponse)
def login_configured():
    return bool(config.ADMIN_PASSWORD) or ":" in config.ADMIN_USERS


def admin_login_form(request: Request):
    return page(request, "admin_login.html", err=None, name="", login_configured=login_configured())


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
                    name=name, status_code=401, login_configured=login_configured())
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
                wb_err=request.query_params.get("wb_err"), emb=embedding.engine.status(),
                models=db.model_rows(), links=db.link_stats(), pairs=db.pair_stats())


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




# ================================================================= compare (§12.7)
@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    qp = request.query_params
    a, b = compare.parse_side(qp, "a"), compare.parse_side(qp, "b")
    err = []
    for p, side in (("a", a), ("b", b)):
        if side is None and (qp.get(p) or qp.get(f"{p}_q")):
            err.append(f"No class matches “{qp.get(f'{p}_q') or qp.get(p)}”.")
    ra = compare.build(a) if a else None
    rb = compare.build(b) if b else None
    same = bool(a and b and a.key == b.key)
    rows, counts = compare.align(ra, rb) if (ra and rb and same) else ([], None)
    facets = {p: db.class_facets(s.src, s.level, s.value) if s else None
              for p, s in (("a", a), ("b", b))}
    labels = {p: compare.describe(s) if s else "" for p, s in (("a", a), ("b", b))}
    return page(request, "compare.html", a=a, b=b, ra=ra, rb=rb, same=same, rows=rows,
                counts=counts, facets=facets, labels=labels, err=err,
                cls_label=lambda s: N.class_label(s.src, s.level, s.value) if s else "",
                filt_url=lambda s: (class_url(s.src, s.level, s.value) + "?" + urlencode(
                    {k: v for k, v in (("place", s.place), ("agent", s.agent), ("book", s.book))
                     if v}) + "#images"))


@app.get("/api/classes/suggest")
def api_classes_suggest(q: str = ""):
    return [dict(key=f"{r['src']}:{r['level']}:{r['value']}",
                 label=N.class_label(r["src"], r["level"], r["value"]),
                 name=N.humanize(r["name"]) if r.get("name") else "",
                 source=config.SOURCES[r["src"]]["label"], level=r["level"], n=r["n"])
            for r in db.suggest_classes(q, 15)]


@app.get("/api/class/geo")
def api_class_geo(c: str = ""):
    """Everything the map needs in one compact payload (DESIGN §12.9)."""
    k = compare.parse_key(c)
    if not k:
        raise HTTPException(404)
    src, level, value = k
    books, agents = db.class_geo(src, level, value)
    by_book = {}
    for a in agents:
        by_book.setdefault(a["book_id"], []).append((a["name"], a["display"]))
    # places in pie order: by number of books with a known place, then key
    counts = {}
    for b in books:
        if b["place_key"]:
            counts[b["place_key"]] = counts.get(b["place_key"], 0) + 1
    order = sorted(counts, key=lambda k_: (-counts[k_], k_))
    info = {}
    for b in books:
        if b["place_key"] and b["lat"] is not None and b["place_key"] not in info:
            info[b["place_key"]] = (b["place"], float(b["lat"]), float(b["lon"]))
    places = [[k_, info[k_][0], round(info[k_][1], 4), round(info[k_][2], 4)]
              for k_ in order if k_ in info]
    pidx = {p_[0]: i for i, p_ in enumerate(places)}
    colour = {k_: i % 8 for i, k_ in enumerate(order)}            # the pie's colours
    houses, hidx, rows = [], {}, []
    n_noyear = n_noplace = 0
    for b in books:
        if b["year"] is None:
            n_noyear += 1
            continue
        if b["place_key"] not in pidx:
            n_noplace += 1
            continue
        hs = []
        for name, disp in by_book.get(b["book_id"], []):
            if name not in hidx:
                hidx[name] = len(houses)
                houses.append([name, disp])
            hs.append(hidx[name])
        rows.append([int(b["year"]), pidx[b["place_key"]], hs, 1 if b["false_imprint"] else 0])
    rows.sort(key=lambda r: r[0])
    curl = class_url(src, level, value)
    main_key = order[0] if order else None
    menus = {p_[0]: compare.place_menu(curl, src, level, value, p_[0], p_[1], main_key,
                                       info.get(main_key, (main_key,))[0] if main_key else None)
             for p_ in places}
    return {"places": places, "colours": [colour[p_[0]] for p_ in places], "houses": houses,
            "books": rows, "n_books": len(books), "n_noyear": n_noyear, "n_noplace": n_noplace,
            "menus": menus}


@app.get("/api/class/facets")
def api_class_facets(c: str = ""):
    k = compare.parse_key(c)
    if not k:
        raise HTTPException(404)
    f = db.class_facets(*k)
    return {"places": f["places"], "agents": f["agents"]}


# ================================================================= reprints (§12.4)
@app.get("/reprints", response_class=HTMLResponse)
def reprints(request: Request, all: int = 0, page_no: int = Query(1, alias="p", ge=1)):
    limit = 50
    rows, total = db.book_pairs(not all, limit, (page_no - 1) * limit)
    shared = db.shared_plates(rows)
    menus = {(a, b, s["plate"]): compare.plate_pair_menu(s["plate"], a, b)
             for (a, b), plates in shared.items() for s in plates}
    return page(request, "reprints.html", rows=rows, total=total, stats=db.pair_stats(),
                all=all, shared=shared, menus=menus, page_no=page_no,
                pager=pager(page_no, math.ceil(total / limit)), base=f"/reprints?all={all}")


@app.get("/reprints.csv")
def reprints_csv(all: int = 0):
    rows, _ = db.book_pairs(not all, 100000, 0)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["book_a", "title_a", "year_a", "place_a", "book_b", "title_b", "year_b", "place_b",
                "shared_plates", "jaccard", "different_place"])
    for r in rows:
        w.writerow([r["a"], r["title_a"], r["year_a"], r["pl_a"], r["b"], r["title_b"], r["year_b"],
                    r["pl_b"], r["shared"], r["jaccard"], r["diff_place"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=book_pairs.csv"})


@app.post("/admin/link")
def admin_link(request: Request, src: str = Form(...), a: str = Form(...), b: str = Form(...),
               action: str = Form(...), back: str = Form("/")):
    need_admin(request)
    db.set_link_rejected(src, a, b, action == "reject")
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


# ================================================================= image search (§9)
def _class_of(o):
    t = most_specific(o) or cluster_of(o)
    return dict(url=class_url(*t), label=o["label"]) if t else None


@app.get("/search/image", response_class=HTMLResponse)
def search_image(request: Request):
    return page(request, "search_image.html", status=embedding.engine.status())


@app.get("/api/image-search/status")
def image_search_status():
    return embedding.engine.status()


@app.post("/api/image-search")
async def image_search_upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > config.QUERY_MAX_BYTES:
        raise HTTPException(413, "image larger than 8 MB")
    try:
        tok, im = embedding.save_query_image(data)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "not an image we can read (JPEG, PNG, TIFF, WebP)")
    return {"token": tok, "w": im.width, "h": im.height}


@app.post("/search/image/from/{oid}")
def image_search_from_ornament(oid: str):
    """Start a search with an existing ornament's own crop."""
    o = db.get_ornament_min(oid)
    if not o:
        raise HTTPException(404)
    data = imaging.crop_exact(o)
    if not data:
        raise HTTPException(503, "the page image is not reachable at the moment")
    tok, _ = embedding.save_query_image(data)
    embedding.update_state(tok, source_oid=oid)
    return RedirectResponse(f"/search/image?token={tok}", status_code=303)


@app.post("/api/image-search/{token}/embed")
def image_search_embed(token: str):
    im = embedding.load_query_image(token)
    if im is None:
        raise HTTPException(404, "this query has expired")
    try:
        vecs = embedding.engine.embed_all(im)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    scores = embedding.engine.kind_scores(vecs)
    kind = max(scores, key=scores.get) if scores else None
    st = embedding.update_state(token, step="embedded", kind_scores=scores, kind=kind,
                                **{embedding.vec_key(k): v.tolist() for k, v in vecs.items()})
    return {"kind": kind, "kind_scores": scores, "model": embedding.engine.model_name,
            "expected_ms": embedding.engine.expected_ms("match")}


@app.post("/api/image-search/{token}/match")
async def image_search_match(token: str, request: Request):
    body = await request.json()
    kind = body.get("kind")
    st = embedding.read_state(token)
    if not st or embedding.vec_key(kind) not in st:
        raise HTTPException(400, "embed first, or unknown type")
    import numpy as np
    vec = np.asarray(st[embedding.vec_key(kind)], dtype=np.float32)
    res = embedding.engine.match(vec, kind)
    embedding.update_state(token, step="matched", kind=kind, result=res)
    return {"ok": True, "n_classes": len(res["classes"]), "url": f"/search/image/{token}"}


@app.get("/search/image/{token}", response_class=HTMLResponse)
def search_image_result(request: Request, token: str):
    st = embedding.read_state(token)
    if not st or not (embedding.query_dir() / f"{token}.jpg").exists():
        raise HTTPException(404, "this query has expired (results are kept for 24 hours)")
    res = st.get("result")
    oids = [c["best_oid"] for c in (res or {}).get("classes", []) if c.get("best_oid")]
    oids += [i["oid"] for i in (res or {}).get("images", [])]
    brief = db.ornaments_brief(oids)
    return page(request, "search_result.html", token=token, st=st, res=res, brief=brief,
                kind=st.get("kind"), scores=st.get("kind_scores") or {},
                status=embedding.engine.status(), class_of=_class_of,
                created=st.get("created"), ttl=config.QUERY_TTL_HOURS)


@app.get("/img/query/{token}.jpg")
def img_query(token: str):
    p = embedding.query_dir() / f"{token}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return Response(p.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})

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
