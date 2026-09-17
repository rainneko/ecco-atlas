"""Page fetching and ornament cropping.

The team's image server serves whole pages; every ornament thumbnail on this
site is a crop of one. Doing the crop server-side (rather than shipping the
full page to the browser and cropping with CSS) matters a lot here: a gallery
page shows 100+ ornaments, and full ECCO page scans are several MB each.

Crops are cached on disk. The cache is a nice-to-have, not a dependency: if the
directory is missing or read-only, every request simply refetches.
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading
import time

import httpx
from PIL import Image, ImageOps

from . import config

log = logging.getLogger("ecco.imaging")
_lock = threading.Lock()
_client: httpx.Client | None = None

PLACEHOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
    '<rect width="100%" height="100%" fill="#f0eee9"/>'
    '<text x="50%" y="50%" font-family="system-ui" font-size="11" fill="#9a958c"'
    ' text-anchor="middle" dominant-baseline="middle">{msg}</text></svg>')


def client() -> httpx.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                    headers={"User-Agent": "ecco-ornament-atlas/1.0"})
    return _client


def page_url(image_id: str) -> str:
    return config.IMAGE_BASE.rstrip("/") + "/" + image_id


def viewer_url(book_id: str, page: int) -> str:
    try:
        return config.PAGE_VIEWER.format(book_id=book_id, page=int(page))
    except Exception:
        return page_url(f"{book_id}{int(page):04d}0")


def _cache_path(key: str):
    d = config.CACHE_DIR / key[:2]
    return d / f"{key}.jpg"


def cache_key(image_id, box, width, pad) -> str:
    raw = f"{image_id}|{'|'.join(f'{float(v):.2f}' for v in box)}|{width}|{pad}"
    return hashlib.sha1(raw.encode()).hexdigest()


def fetch_page(image_id: str) -> Image.Image:
    r = client().get(page_url(image_id))
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    im.load()
    return ImageOps.exif_transpose(im)


def crop_bytes(image_id: str, box, width: int | None = None,
               pad: float | None = None) -> bytes | None:
    """Return JPEG bytes of the cropped ornament, or None if the page can't be
    fetched. Never raises: a broken image must not break a gallery page."""
    width = width or config.THUMB_W
    pad = config.CROP_PAD if pad is None else pad
    key = cache_key(image_id, box, width, pad)
    fp = _cache_path(key)
    try:
        if fp.exists():
            fp.touch(exist_ok=True)      # crude LRU marker
            return fp.read_bytes()
    except OSError:
        pass

    try:
        im = fetch_page(image_id)
    except Exception as e:                # noqa: BLE001 - deliberately broad
        log.warning("fetch failed %s: %s", image_id, e)
        return None

    x1, y1, x2, y2 = (float(v) for v in box)
    dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
    box_px = (max(0, int(x1 - dx)), max(0, int(y1 - dy)),
              min(im.width, int(x2 + dx)), min(im.height, int(y2 + dy)))
    if box_px[2] <= box_px[0] or box_px[3] <= box_px[1]:
        box_px = (0, 0, im.width, im.height)
    im = im.crop(box_px)
    if im.width > width:
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=86, optimize=True)
    data = buf.getvalue()

    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(data)
    except OSError as e:
        log.debug("cache write skipped: %s", e)
    return data


def placeholder_svg(msg="image unavailable", w=260, h=120) -> str:
    return PLACEHOLDER.format(w=w, h=h, msg=msg)


def prune_cache(max_mb: int | None = None):
    """Delete oldest crops when the cache exceeds its budget. Called from a
    background thread on a timer; safe to fail."""
    max_mb = max_mb or config.CACHE_MAX_MB
    try:
        files = [(p.stat().st_mtime, p.stat().st_size, p)
                 for p in config.CACHE_DIR.rglob("*.jpg")]
    except OSError:
        return
    total = sum(s for _, s, _ in files)
    if total <= max_mb * 1024 * 1024:
        return
    files.sort()
    for _, size, p in files:
        try:
            p.unlink()
            total -= size
        except OSError:
            pass
        if total <= max_mb * 0.8 * 1024 * 1024:
            break
    log.info("cache pruned to %.0f MB", total / 1e6)


def start_cache_janitor(interval=3600):
    def loop():
        while True:
            time.sleep(interval)
            prune_cache()
    threading.Thread(target=loop, daemon=True, name="cache-janitor").start()
