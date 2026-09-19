"""Write emb_<KIND>.npz for the fixture database with the fake extractor, in
the layout of tools/export_embeddings.py, so the whole image-search pipeline
can be exercised without torch.

    IMAGE_BASE=http://127.0.0.1:8111/ python -m tests.make_embeddings /tmp/fx --dim 384
"""
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, imaging  # noqa: E402
from app.embedding import FakeExtractor  # noqa: E402


def main(out_dir, dim):
    conn = psycopg2.connect(config.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT oid, image_id, box_key, x1, y1, x2, y2, kind FROM ornament ORDER BY image_id")
    rows = cur.fetchall()
    ext = FakeExtractor(dim)
    by_kind = defaultdict(lambda: ([], [], []))
    pages, t0 = {}, time.time()
    for oid, image_id, box_key, x1, y1, x2, y2, kind in rows:
        if image_id not in pages:
            pages.clear()
            pages[image_id] = imaging.fetch_page(image_id)
        page = pages[image_id]
        if page is None:
            continue
        crop = imaging.crop_image(page, (x1, y1, x2, y2), pad=0)
        size = config.EMBED_INPUT[kind]
        ids, boxes, embs = by_kind[kind]
        ids.append(image_id + ".TIF")
        boxes.append([x1, y1, x2, y2])
        embs.append(ext.embed(crop, size, config.EMBED_CROP))
    for kind, (ids, boxes, embs) in by_kind.items():
        meta = dict(kind=kind, model="fake", dim=dim, input_size=list(config.EMBED_INPUT[kind]),
                    crop=config.EMBED_CROP, transform="fake")
        np.savez_compressed(os.path.join(out_dir, f"emb_{kind}.npz"),
                            id=np.array(ids, dtype=str), boxes=np.array(boxes, dtype=np.float32),
                            emb=np.stack(embs).astype(np.float32), meta=json.dumps(meta))
        print(f"emb_{kind}.npz: {len(ids)} × {dim}")
    print(f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[3]) if len(sys.argv) > 3 else 384)
