from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, db, imaging
from . import normalize as N

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ecco")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    imaging.start_cache_janitor()
    log.info("started; cache=%s image_base=%s", config.CACHE_DIR, config.IMAGE_BASE)
    yield
    db.close_pool()


app = FastAPI(title=config.SITE_TITLE, lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")
T = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))
signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="admin")


# ---------------------------------------------------------------- helpers
def crop_url(oid, w=None):
    return f"/img/crop/{oid}.jpg" + (f"?w={w}" if w else "")


T.env.globals.update(cfg=config, crop_url=crop_url, KIND_COLOR=config.KIND_COLOR,
                     KIND_LABEL=config.KIND_LABEL, viewer_url=imaging.viewer_url)


def is_admin(request: Request) -> bool:
    tok = request.cookies.get(config.SESSION_COOKIE)
    if not tok:
        return False
    try:
        signer.loads(tok, max_age=config.SESSION_MAX_AGE)
        return True
    except BadSignature:
        return False


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="admin only")
    return True


def page(request: Request, name: str, **ctx):
    ctx.setdefault("admin", is_admin(request))
    return T.TemplateResponse(request, name, ctx)


# ================================================================= pages
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return page(request, "index.html", stats=db.site_stats(), kinds=db.kind_counts())


@app.get("/books", response_class=HTMLResponse)
def books(request: Request, q: str = "", kind: str = "", tonson: int = 0,
          page_no: int = Query(1, alias="p")):
    limit, offset = 30, (max(page_no, 1) - 1) * 30
    rows, total = db.search_books(q, limit, offset, kind or None, bool(tonson))
    strips = {r["book_id"]: db.book_strip(r["book_id"]) for r in rows}
    return page(request, "books.html", rows=rows, total=total, q=q, kind=kind,
                tonson=tonson, page_no=page_no, strips=strips, pages=(total + 29) // 30)


@app.get("/book/{book_id}", response_class=HTMLResponse)
def book_detail(request: Request, book_id: str):
    b = db.get_book(book_id)
    if not b:
        raise HTTPException(404, "book not found")
    return page(request, "book.html", b=b, agents=db.book_agents(book_id),
                strip=db.book_strip(book_id), orns=db.book_ornaments(book_id),
                similar=db.similar_books(book_id, 10))


@app.get("/ornament/{oid}", response_class=HTMLResponse)
def ornament_detail(request: Request, oid: str):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404, "ornament not found")
    return page(request, "ornament.html", o=o, similar=db.similar_ornaments(oid, 3),
                agents=db.book_agents(o["book_id"]) if o["book_id"] else [])


@app.get("/classes", response_class=HTMLResponse)
def classes(request: Request, level: str = "subclass", q: str = ""):
    if level not in db.LEVEL_COL:
        raise HTTPException(400, "bad level")
    return page(request, "classes.html", level=level, q=q,
                rows=db.browse_classes(level, q))


@app.get("/class/{level}/{value:path}", response_class=HTMLResponse)
def class_detail(request: Request, level: str, value: str,
                 page_no: int = Query(1, alias="p")):
    if level not in db.LEVEL_COL:
        raise HTTPException(400, "bad level")
    limit, offset = 120, (max(page_no, 1) - 1) * 120
    rows, total = db.class_members(level, value, limit, offset)
    if not total:
        raise HTTPException(404, "class not found")
    return page(request, "class.html", level=level, value=value, rows=rows, total=total,
                page_no=page_no, pages=(total + limit - 1) // limit,
                pubs=db.class_agent_mix(level, value, "publisher"),
                prns=db.class_agent_mix(level, value, "printer"),
                siblings=db.class_siblings(level, value))


@app.get("/agents", response_class=HTMLResponse)
def agents(request: Request, q: str = ""):
    return page(request, "agents.html", rows=db.search_agents(q), q=q)


@app.get("/agent/{name}", response_class=HTMLResponse)
def agent_detail(request: Request, name: str):
    a = db.get_agent(name)
    if not a:
        raise HTTPException(404, "agent not found")
    return page(request, "agent.html", a=a, stats=db.agent_stats(name),
                plates=db.agent_plates(name), similar=db.similar_agents(name, 10))


# ================================================================= images
@app.get("/img/crop/{oid}.jpg")
def img_crop(oid: str, w: int = Query(None, ge=32, le=2000)):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    data = imaging.crop_bytes(o["image_id"], (o["x1"], o["y1"], o["x2"], o["y2"]), w)
    if data is None:
        return Response(imaging.placeholder_svg(), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=60"})
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/img/class/{level}/{value:path}.jpg")
def img_class(level: str, value: str, w: int = Query(None, ge=32, le=2000)):
    """Exemplar thumbnail for a whole class — the largest box we have for it."""
    if level not in db.LEVEL_COL:
        raise HTTPException(400)
    row = db.class_exemplar(level, value)
    if not row:
        raise HTTPException(404)
    data = imaging.crop_bytes(row["image_id"], (row["x1"], row["y1"], row["x2"], row["y2"]), w)
    if data is None:
        return Response(imaging.placeholder_svg(), media_type="image/svg+xml")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/img/page/{oid}")
def img_page(oid: str):
    """Redirect to the full page on the image server (we don't proxy whole pages)."""
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    return RedirectResponse(imaging.page_url(o["image_id"]))


# ================================================================= JSON API
@app.get("/api/book/{book_id}/strip")
def api_strip(book_id: str):
    b = db.get_book(book_id)
    if not b:
        raise HTTPException(404)
    rows = db.book_strip(book_id)
    total = b["total_pages"] or (max((r["page"] for r in rows), default=1))
    out = {k: [] for k in config.KINDS}
    for r in rows:
        out.setdefault(r["kind"], []).append(
            {"page": r["page"], "n": r["n"], "oid": r["first_oid"], "oids": r["oids"]})
    return {"book_id": book_id, "total_pages": total,
            "rows": [{"kind": k, "color": config.KIND_COLOR.get(k, "#888"),
                      "label": config.KIND_LABEL.get(k, k), "points": out.get(k, [])}
                     for k in config.KINDS]}


@app.get("/api/ornament/{oid}")
def api_ornament(oid: str):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    o = dict(o)
    o["crop_url"] = crop_url(oid)
    o["page_url"] = imaging.page_url(o["image_id"])
    o["viewer_url"] = imaging.viewer_url(o["book_id"], o["page"]) if o["book_id"] else None
    return o


@app.get("/api/search/books")
def api_search_books(q: str = "", limit: int = 20):
    rows, total = db.search_books(q, min(limit, 100))
    return {"total": total, "results": rows}


# ================================================================= reports
@app.post("/ornament/{oid}/report")
def submit_report(oid: str, request: Request, reporter: str = Form(""),
                  sug_superclass: str = Form(""), sug_subclass: str = Form(""),
                  sug_variant: str = Form(""), note: str = Form("")):
    o = db.get_ornament(oid)
    if not o:
        raise HTTPException(404)
    if not any([sug_superclass.strip(), sug_subclass.strip(), sug_variant.strip(),
                note.strip()]):
        raise HTTPException(400, "say what you think is wrong")
    db.add_report(oid, reporter.strip(),
                  (o["superclass"], o["subclass"], o["variant"]),
                  (sug_superclass.strip() or None, sug_subclass.strip() or None,
                   sug_variant.strip() or None),
                  note.strip())
    return RedirectResponse(f"/ornament/{oid}?reported=1", status_code=303)


# ================================================================= admin
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    return page(request, "admin_login.html", err=None)


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form("")):
    if not config.ADMIN_PASSWORD or password != config.ADMIN_PASSWORD:
        return page(request, "admin_login.html", err="wrong password")
    r = RedirectResponse("/admin", status_code=303)
    r.set_cookie(config.SESSION_COOKIE, signer.dumps("ok"), httponly=True, samesite="lax",
                 secure=request.url.scheme == "https", max_age=config.SESSION_MAX_AGE)
    return r


@app.get("/admin/logout")
def admin_logout():
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(config.SESSION_COOKIE)
    return r


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, status: str = "open"):
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return page(request, "admin.html", reports=db.list_reports(status), status=status)


@app.post("/admin/report/{report_id}")
def admin_resolve(report_id: int, request: Request, action: str = Form(...),
                  _=Depends(require_admin)):
    db.resolve_report(report_id, action == "accept", who="admin")
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
def healthz():
    try:
        db.q("SELECT 1 AS ok", one=True)
        return {"ok": True}
    except Exception as e:                # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return T.TemplateResponse(request, "404.html", {"admin": is_admin(request)},
                              status_code=404)
