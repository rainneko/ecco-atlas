#!/usr/bin/env python3
"""Fill the database crop store for human-annotated ornaments (DESIGN §6).

    python -m etl.fetch_crops                 # all annotated crops not stored yet
    python -m etl.fetch_crops --src HP-ann --limit 2000 --workers 4
    python -m etl.fetch_crops --redo          # re-crop everything (e.g. new padding)

Each page image is fetched once and every annotated box on it is cropped from
that one download. Safe to stop and restart: it only picks up what is missing.
Uses IMAGE_BASE and DATABASE_URL like the web app.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db, imaging  # noqa: E402


def pending(srcs, redo, limit):
    cond = "o.ann_src = ANY(%(s)s)" if srcs else "o.ann_src IS NOT NULL"
    if not redo:
        cond += " AND NOT EXISTS (SELECT 1 FROM crop_store c WHERE c.oid = o.oid)"
    rows = db.q(f"""SELECT o.oid, o.image_id, o.x1, o.y1, o.x2, o.y2, o.ann_src
                    FROM ornament o WHERE {cond}
                    ORDER BY o.image_id LIMIT %(lim)s""",
                {"s": srcs or [], "lim": limit or 10**9})
    pages = defaultdict(list)
    for r in rows:
        pages[r["image_id"]].append(r)
    return pages


def do_page(image_id, orns):
    page = imaging.fetch_page(image_id)
    if page is None:
        return 0, len(orns)
    ok = 0
    for o in orns:
        if imaging.store_annotated(o, page) is not None:
            ok += 1
    return ok, len(orns) - ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--src", action="append", choices=[s for s, v in config.SOURCES.items()
                                                        if v["family"] == "ann"])
    ap.add_argument("--limit", type=int, default=0, help="at most this many ornaments")
    ap.add_argument("--workers", type=int, default=4, help="parallel page downloads")
    ap.add_argument("--redo", action="store_true", help="re-crop ornaments already stored")
    a = ap.parse_args()

    pages = pending(a.src, a.redo, a.limit)
    n_orn = sum(len(v) for v in pages.values())
    print(f"{n_orn:,} crops to store from {len(pages):,} pages via {config.IMAGE_BASE}", flush=True)
    if not pages:
        return
    t0, done, ok, bad = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = {ex.submit(do_page, iid, orns): iid for iid, orns in pages.items()}
        for f in as_completed(futs):
            g, b = f.result()
            ok, bad, done = ok + g, bad + b, done + 1
            if done % 200 == 0 or done == len(pages):
                rate = done / max(1e-6, time.time() - t0)
                print(f"  pages {done:,}/{len(pages):,}  stored {ok:,}  failed {bad:,}  "
                      f"({rate:.1f} pages/s)", flush=True)
    s = db.crop_store_stats()
    print(f"done in {time.time() - t0:.0f}s. Store now holds {s['stored']:,} of "
          f"{s['annotated']:,} annotated crops ({s['size']}).")
    if bad and ok == 0:
        print("Every fetch failed: is IMAGE_BASE reachable from here? (The image server has "
              "returned 403 to Rahti before; see DESIGN §2.)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
