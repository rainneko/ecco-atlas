#!/usr/bin/env python3
"""Load ornament embeddings (DESIGN §9.3).

    python -m etl.load_embeddings --model moco_vit_s emb_DI.npz emb_FT.npz emb_HP.npz emb_TP.npz
    python -m etl.load_embeddings --model moco_vit_s --replace emb_HP.npz     # swap one type

Each npz (from tools/export_embeddings.py) holds `id`, `boxes`, `emb`, `meta`.
Rows are matched to ornaments by (image id, box) and then by IoU >= 0.9;
vectors are stored as float16. After the last file the centroids of every
machine cluster and human plate, and the 300-per-type samples, are rebuilt.

A CSV with columns id, boxes, e0..e{D-1} (or a single `embedding` column with
a JSON list) is accepted for small tests.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402
from app import normalize as N  # noqa: E402


def read_file(path: Path):
    if path.suffix == ".npz":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"])) if "meta" in z else {}
        return list(z["id"]), np.asarray(z["boxes"], dtype=np.float64), \
            np.asarray(z["emb"], dtype=np.float32), meta
    df = pd.read_csv(path)
    if "embedding" in df.columns:
        E = np.array([json.loads(s) for s in df["embedding"]], dtype=np.float32)
    else:
        cols = [c for c in df.columns if c.startswith("e") and c[1:].isdigit()]
        E = df[sorted(cols, key=lambda c: int(c[1:]))].to_numpy(dtype=np.float32)
    boxes = np.array([N.parse_box(b) or (np.nan,) * 4 for b in df["boxes"]], dtype=np.float64)
    return list(df["id"].astype(str)), boxes, E, {}


def iou_sql(a, b):
    return (f"(greatest(0, least({a}.x2,{b}.x2) - greatest({a}.x1,{b}.x1)) * "
            f"greatest(0, least({a}.y2,{b}.y2) - greatest({a}.y1,{b}.y1))) / "
            f"nullif(({a}.x2-{a}.x1)*({a}.y2-{a}.y1) + ({b}.x2-{b}.x1)*({b}.y2-{b}.y1) - "
            f"greatest(0, least({a}.x2,{b}.x2) - greatest({a}.x1,{b}.x1)) * "
            f"greatest(0, least({a}.y2,{b}.y2) - greatest({a}.y1,{b}.y1)), 0)")


def load_one(conn, path: Path, thresh: float):
    ids, boxes, E, meta = read_file(path)
    kind = meta.get("kind") or path.stem.split("_")[-1].upper()
    if kind not in config.KINDS:
        sys.exit(f"{path}: cannot tell the ornament type (meta.kind or emb_<KIND>.npz)")
    n = len(ids)
    norms = np.linalg.norm(E, axis=1, keepdims=True)
    E = E / np.maximum(norms, 1e-8)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS _emb")
    cur.execute("""CREATE TEMP TABLE _emb (idx INT PRIMARY KEY, oid TEXT, image_id TEXT,
                   x1 FLOAT8, y1 FLOAT8, x2 FLOAT8, y2 FLOAT8, target TEXT)""")
    buf = io.StringIO()
    bad = 0
    for i, (rid, b) in enumerate(zip(ids, boxes)):
        try:
            image_id = N.image_id_of(str(rid))
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        if not (len(image_id) == 15 and image_id.isdigit()) or np.isnan(b).any():
            bad += 1
            continue
        box = tuple(float(v) for v in b)
        buf.write(f"{i}\t{N.make_oid(image_id, box)}\t{image_id}\t{box[0]}\t{box[1]}\t"
                  f"{box[2]}\t{box[3]}\t\\N\n")
    buf.seek(0)
    cur.copy_expert("COPY _emb FROM STDIN WITH (FORMAT text, NULL '\\N')", buf)
    cur.execute("UPDATE _emb t SET target = t.oid FROM ornament o WHERE o.oid = t.oid")
    n_exact = cur.rowcount
    cur.execute(f"""
        SELECT t.idx, o.oid, {iou_sql('t', 'o')} AS iou
        FROM _emb t JOIN ornament o ON o.image_id = t.image_id AND o.kind = %s
        WHERE t.target IS NULL""", (kind,))
    pairs = [p for p in cur.fetchall() if p[2] is not None and p[2] >= thresh]
    pairs.sort(key=lambda p: -p[2])
    cur.execute("SELECT target FROM _emb WHERE target IS NOT NULL")
    used = {r[0] for r in cur.fetchall()}
    assign = {}
    for idx, oid, _ in pairs:
        if idx not in assign and oid not in used:
            assign[idx] = oid
            used.add(oid)
    if assign:
        psycopg2.extras.execute_values(
            cur, "UPDATE _emb t SET target = v.o FROM (VALUES %s) v(i, o) WHERE t.idx = v.i",
            list(assign.items()), page_size=5000)
    cur.execute("SELECT idx, target FROM _emb WHERE target IS NOT NULL")
    matched = cur.fetchall()
    # vectors: COPY as hex bytea
    cur.execute("DELETE FROM ornament_embedding e USING ornament o "
                "WHERE o.oid = e.oid AND o.kind = %s", (kind,))
    buf = io.StringIO()
    for idx, oid in matched:
        buf.write(f"{oid}\t\\\\x{E[idx].astype(np.float16).tobytes().hex()}\n")
    buf.seek(0)
    cur.copy_expert("COPY ornament_embedding (oid, vec) FROM STDIN WITH (FORMAT text)", buf)
    conn.commit()
    print(f"[{kind}] {path.name}: {n:,} rows, {E.shape[1]}-d; matched exactly {n_exact:,}, "
          f"by IoU {len(assign):,}; unmatched {n - n_exact - len(assign) - bad:,}; "
          f"bad rows {bad:,}")
    return kind, E.shape[1], meta, len(matched)


def rebuild_derived(conn, dim):
    """Centroids of clusters and human plates; type samples."""
    cur = conn.cursor()
    t = time.time()
    cur.execute("DELETE FROM cluster_centroid")
    cur.execute("DELETE FROM kind_sample")
    groups = {
        "pred": """SELECT o.pred_src, o.hc_cluster, o.kind, e.vec FROM ornament o
                   JOIN ornament_embedding e USING (oid)
                   WHERE o.pred_src IS NOT NULL AND o.hc_cluster IS NOT NULL
                     AND NOT o.cluster_rejected ORDER BY o.pred_src, o.hc_cluster""",
        "ann": """SELECT o.ann_src, o.class_path, o.kind, e.vec FROM ornament o
                  JOIN ornament_embedding e USING (oid)
                  WHERE o.ann_src IS NOT NULL AND o.class_path IS NOT NULL
                  ORDER BY o.ann_src, o.class_path""",
    }
    n_cent = 0
    for fam, sql in groups.items():
        cur.execute(sql)
        cur_key, acc, cnt, kind, rows = None, None, 0, None, []

        def flush():
            nonlocal rows
            if cur_key is not None and cnt:
                v = acc / cnt
                v = v / max(np.linalg.norm(v), 1e-8)
                rows.append((cur_key[0], cur_key[1], kind, cnt,
                             psycopg2.Binary(v.astype(np.float16).tobytes())))
            if len(rows) >= 2000:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO cluster_centroid (src, value, kind, n, vec) VALUES %s", rows)
                rows = []

        for src, value, k, vec in cur.fetchall():
            key = (src, value)
            if key != cur_key:
                flush()
                cur_key, acc, cnt, kind = key, np.zeros(dim, np.float64), 0, k
            acc += np.frombuffer(bytes(vec), dtype=np.float16).astype(np.float64)
            cnt += 1
        flush()
        if rows:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO cluster_centroid (src, value, kind, n, vec) VALUES %s", rows)
        cur.execute("SELECT count(*) FROM cluster_centroid")
        n_cent = cur.fetchone()[0]
    for k in config.KINDS:
        cur.execute("""INSERT INTO kind_sample (oid, kind)
                       SELECT o.oid, o.kind FROM ornament o JOIN ornament_embedding e USING (oid)
                       WHERE o.kind = %s ORDER BY random() LIMIT %s""", (k, config.KIND_SAMPLE_N))
    conn.commit()
    print(f"centroids: {n_cent:,}; type samples: {config.KIND_SAMPLE_N} per type; "
          f"{time.time() - t:.1f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--model", required=True, help="model name (must match model_store)")
    ap.add_argument("--replace", action="store_true", help="(default behaviour per type)")
    ap.add_argument("--iou", type=float, default=0.9)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    conn = psycopg2.connect(a.dsn)
    cur = conn.cursor()
    cur.execute("SELECT name, dim FROM embedding_model ORDER BY loaded_at DESC LIMIT 1")
    prev = cur.fetchone()
    sizes, dim, total = {}, None, 0
    for f in a.files:
        if not f.exists():
            sys.exit(f"file not found: {f}")
        kind, d, meta, n = load_one(conn, f, a.iou)
        if dim is not None and d != dim:
            sys.exit(f"{f}: {d}-d vectors but earlier files are {dim}-d")
        dim = d
        total += n
        sizes[kind] = dict(size=meta.get("input_size", list(config.EMBED_INPUT[kind])),
                           crop=meta.get("crop", config.EMBED_CROP), model=meta.get("model"))
    if prev and prev[0] != a.model:
        cur.execute("DELETE FROM ornament_embedding e USING ornament o WHERE o.oid = e.oid "
                    "AND o.kind <> ALL(%s)", (list(sizes),))
        print(f"model changed ({prev[0]} -> {a.model}): vectors of types not in this run "
              f"were removed ({cur.rowcount:,})")
    cur.execute("SELECT count(*) FROM ornament_embedding")
    n_all = cur.fetchone()[0]
    cur.execute("SELECT input_sizes FROM embedding_model WHERE name = %s", (a.model,))
    r = cur.fetchone()
    merged = dict(r[0] or {}) if r else {}
    merged.update(sizes)
    cur.execute("DELETE FROM embedding_model")
    cur.execute("INSERT INTO embedding_model (name, dim, input_sizes, n) VALUES (%s, %s, %s, %s)",
                (a.model, dim, json.dumps(merged), n_all))
    conn.commit()
    rebuild_derived(conn, dim)
    cur.execute("SELECT dim FROM model_store WHERE name = %s", (a.model,))
    r = cur.fetchone()
    if r is None:
        print(f"! no model named {a.model!r} in model_store yet (python -m etl.load_model)")
    elif r[0] != dim:
        print(f"! model {a.model!r} outputs {r[0]}-d vectors, these files are {dim}-d")
    print(f"embedding_model: {a.model}, {dim}-d, {n_all:,} vectors, sizes {merged}")


if __name__ == "__main__":
    main()
