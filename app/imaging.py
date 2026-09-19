"""Page fetching, cropping and the two crop stores (DESIGN §6).

* Annotated ornaments: crop kept in Postgres (crop_store), written through on
  first view or in bulk by `python -m etl.fetch_crops`.
* Everything else: an LRU queue of CACHE_MAX_ITEMS files on local disk. A hit
  moves the file to the back of the queue (its mtime); when the queue is too
  long the oldest files are deleted.

Nothing here raises into a page: a failed fetch yields a placeholder.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time

import httpx
from PIL import Image, ImageOps

from . import config, db

log = logging.getLogger("ecco.imaging")
_lock = threading.Lock()
_client: httpx.Client | None = None
_failed: dict[str, float] = {}           # image_id -> time of last failure
FAIL_TTL = 60

PLACEHOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
    '<rect width="100%" height="100%" fill="#efece6"/>'
    '<text x="50%" y="50%" font-family="system-ui,sans-serif" font-size="12" fill="#9a958c"'
    ' text-anchor="middle" dominant-baseline="middle">{msg}</text></svg>')


def client() -> httpx.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                    headers={"User-Agent": "ecco-ornament-atlas/2.0"})
    return _client


def page_url(image_id: str) -> str:
    return config.IMAGE_BASE.rstrip("/") + "/" + image_id


def viewer_url(book_id, page) -> str:
    try:
        return config.PAGE_VIEWER.format(book_id=book_id, page=int(page))
    except (TypeError, ValueError, KeyError):
        return page_url(f"{book_id}{int(page or 0):04d}0")


def snap(w) -> int:
    w = int(w or config.WIDTHS[0])
    for b in config.WIDTHS:
        if w <= b:
            return b
    return config.WIDTHS[-1]


# ---------------------------------------------------------------- fetching
def fetch_page(image_id: str) -> Image.Image | None:
    t = _failed.get(image_id)
    if t and time.time() - t < FAIL_TTL:
        return None
    try:
        r = client().get(page_url(image_id))
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return ImageOps.exif_transpose(im)
    except Exception as e:  # noqa: BLE001 — any failure becomes a placeholder
        _failed[image_id] = time.time()
        if len(_failed) > 5000:
            _failed.clear()
        log.warning("page fetch failed %s: %s", image_id, e)
        return None


def crop_image(page: Image.Image, box, pad=None) -> Image.Image:
    pad = config.CROP_PAD if pad is None else pad
    x1, y1, x2, y2 = (float(v) for v in box)
    dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
    bx = (max(0, int(x1 - dx)), max(0, int(y1 - dy)),
          min(page.width, int(x2 + dx)), min(page.height, int(y2 + dy)))
    if bx[2] <= bx[0] or bx[3] <= bx[1]:
        bx = (0, 0, page.width, page.height)
    return page.crop(bx)


def encode(im: Image.Image, width: int | None = None, quality=86) -> bytes:
    if width and im.width > width:
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def downscale(jpeg: bytes, width: int) -> bytes:
    im = Image.open(io.BytesIO(jpeg))
    if im.width <= width:
        return jpeg
    return encode(im, width)


# ---------------------------------------------------------------- LRU disk queue
def _lru_dir():
    d = config.CACHE_DIR / "lru"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def lru_get(key: str) -> bytes | None:
    d = _lru_dir()
    if d is None:
        return None
    fp = d / f"{key}.jpg"
    try:
        data = fp.read_bytes()
        os.utime(fp, None)                       # move to the back of the queue
        return data
    except OSError:
        return None


def lru_put(key: str, data: bytes):
    d = _lru_dir()
    if d is None:
        return
    try:
        tmp = d / f".{key}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / f"{key}.jpg")
        lru_prune(d)
    except OSError as e:
        log.debug("cache write skipped: %s", e)


def lru_prune(d=None, cap=None):
    d = d or _lru_dir()
    cap = cap or config.CACHE_MAX_ITEMS
    if d is None:
        return 0
    try:
        entries = [(e.stat().st_mtime, e.path) for e in os.scandir(d)
                   if e.name.endswith(".jpg")]
    except OSError:
        return 0
    extra = len(entries) - cap
    if extra <= 0:
        return 0
    entries.sort()
    for _, p in entries[:extra]:
        try:
            os.unlink(p)
        except OSError:
            pass
    return extra


def lru_count() -> int:
    d = _lru_dir()
    try:
        return sum(1 for e in os.scandir(d) if e.name.endswith(".jpg")) if d else 0
    except OSError:
        return 0


# ---------------------------------------------------------------- public API
def store_annotated(o, page: Image.Image | None = None) -> tuple[bytes, bytes] | None:
    """Crop an annotated ornament and keep it in Postgres. Returns (full, thumb)."""
    page = page or fetch_page(o["image_id"])
    if page is None:
        return None
    im = crop_image(page, (o["x1"], o["y1"], o["x2"], o["y2"]))
    full = encode(im, config.STORE_FULL_W, quality=88)
    thumb = encode(im, config.WIDTHS[0])
    try:
        db.put_crop_store(o["oid"], full, thumb, min(im.width, config.STORE_FULL_W))
    except Exception as e:  # noqa: BLE001 — storing is best effort
        log.warning("crop_store write failed %s: %s", o["oid"], e)
    return full, thumb


def get_crop(o, width=None) -> bytes | None:
    """JPEG bytes for ornament `o` (needs oid, image_id, x1..y2, ann_src)."""
    w = snap(width)
    small = w <= config.WIDTHS[0]
    if o.get("ann_src"):
        try:
            data = db.get_crop_store(o["oid"], thumb=small)
        except Exception:  # noqa: BLE001
            data = None
        if data is None:
            pair = store_annotated(o)
            if pair is None:
                return None
            data = pair[1] if small else pair[0]
        return data if small else downscale(data, w)
    key = f"{o['oid']}_{w}"
    data = lru_get(key)
    if data is not None:
        return data
    page = fetch_page(o["image_id"])
    if page is None:
        return None
    data = encode(crop_image(page, (o["x1"], o["y1"], o["x2"], o["y2"])), w)
    lru_put(key, data)
    return data


def crop_exact(o) -> bytes | None:
    """The box exactly as the embedding export cuts it: no padding, full width."""
    page = fetch_page(o["image_id"])
    if page is None:
        return None
    return encode(crop_image(page, (o["x1"], o["y1"], o["x2"], o["y2"]), pad=0),
                  config.STORE_FULL_W, quality=92)


def placeholder_svg(msg="image unavailable", w=300, h=120) -> str:
    return PLACEHOLDER.format(w=w, h=h, msg=msg)
