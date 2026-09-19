#!/usr/bin/env python3
"""Link machine clusters that are probably the same design (DESIGN §12.6).

    python -m etl.link_clusters [--src HP-pred] [--min-sim 0.85] [--top 10]

Takes the centroids written by etl.load_embeddings, computes the cosine
similarity between every pair of clusters of one source in blocks, and
stores every pair at or above --min-sim (at most --top per cluster) in
`cluster_link`. Prints the distribution of each cluster's best match so the
threshold can be chosen from the data. Rejections made by admins (the
`rejected` flag) survive reruns.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402


def link_source(conn, src, min_sim, top, block=2000):
    cur = conn.cursor()
    cur.execute("SELECT value, n, vec FROM cluster_centroid WHERE src = %s ORDER BY value", (src,))
    rows = cur.fetchall()
    if len(rows) < 2:
        print(f"[{src}] {len(rows)} centroids — nothing to link")
        return 0
    values = [r[0] for r in rows]
    ns = [r[1] for r in rows]
    M = np.stack([np.frombuffer(bytes(r[2]), dtype=np.float16).astype(np.float32) for r in rows])
    M /= np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-8)
    t = time.time()
    best = np.full(len(rows), -1.0, dtype=np.float32)
    pairs = []
    for i0 in range(0, len(rows), block):
        S = M[i0:i0 + block] @ M.T                      # (block, N)
        for k in range(S.shape[0]):
            S[k, i0 + k] = -1.0                          # not itself
        best[i0:i0 + block] = S.max(axis=1)
        if top:
            idx = np.argpartition(-S, min(top, S.shape[1] - 1), axis=1)[:, :top]
        else:
            idx = None
        for k in range(S.shape[0]):
            i = i0 + k
            js = idx[k] if idx is not None else np.nonzero(S[k] >= min_sim)[0]
            for j in js:
                if j > i and S[k, j] >= min_sim:
                    pairs.append((src, values[i], values[j], float(S[k, j]), ns[i], ns[j]))
                elif j < i and S[k, j] >= min_sim and top:
                    pairs.append((src, values[j], values[i], float(S[k, j]), ns[j], ns[i]))
    pairs = list({(p[1], p[2]): p for p in pairs}.values())
    cur.execute("SELECT a, b FROM cluster_link WHERE src = %s AND rejected", (src,))
    rejected = set(cur.fetchall())
    cur.execute("DELETE FROM cluster_link WHERE src = %s AND NOT rejected", (src,))
    psycopg2.extras.execute_values(cur, """
        INSERT INTO cluster_link (src, a, b, sim, n_a, n_b) VALUES %s
        ON CONFLICT (src, a, b) DO UPDATE SET sim = EXCLUDED.sim, n_a = EXCLUDED.n_a,
            n_b = EXCLUDED.n_b, computed_at = now()""",
        [p for p in pairs if (p[1], p[2]) not in rejected], page_size=5000)
    conn.commit()
    bins = [0, .5, .7, .8, .85, .9, .95, 1.01]
    hist, _ = np.histogram(best, bins=bins)
    print(f"[{src}] {len(rows):,} centroids, {len(pairs):,} links at sim >= {min_sim} "
          f"({time.time() - t:.1f}s). Best match per cluster:")
    for lo, hi, n in zip(bins[:-1], bins[1:], hist):
        print(f"    [{lo:.2f}, {min(hi, 1):.2f})  {n:,}")
    return len(pairs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--src", action="append",
                    choices=[s for s, v in config.SOURCES.items() if v["family"] == "pred"])
    ap.add_argument("--min-sim", type=float, default=config.LINK_MIN_SIM)
    ap.add_argument("--top", type=int, default=10, help="max links per cluster (0 = all)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    conn = psycopg2.connect(a.dsn)
    srcs = a.src or [s for s, v in config.SOURCES.items() if v["family"] == "pred"]
    total = sum(link_source(conn, s, a.min_sim, a.top) for s in srcs)
    print(f"stored {total:,} links")


if __name__ == "__main__":
    main()
