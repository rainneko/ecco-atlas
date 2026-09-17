#!/usr/bin/env python3
"""Load ECCO annotation CSVs into Postgres.

    python -m etl.load --kind HP data/HP_concat_hp_tonson.csv \
                       --kind DI data/init_3rd_DI202602.csv \
                       --kind FT data/FT_annotations.csv

Idempotent: re-running upserts on the (image_id, box) primary key, so you can
re-load a corrected CSV without wiping the database. Accepted corrections made
through the admin UI are re-applied afterwards from the label_change table,
unless you pass --no-replay.

Column names differ between the annotation files; every lookup goes through
`pick()` so a new file only needs its aliases added there.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import normalize as N  # noqa: E402

ALIASES = {
    "id":         ["id", "image_id", "filename"],
    "boxes":      ["boxes", "box", "bbox"],
    "book_id":    ["book_id", "documentID", "docId"],
    "estc":       ["ESTCID", "estc_id", "ESTC"],
    "title":      ["fullTitle", "full_title", "title"],
    "year":       ["year", "dateStart"],
    "publishers": ["publishers", "publisher"],
    "printers":   ["printers", "printer"],
    "superclass": ["superclass", "super_class"],
    "subclass":   ["subclass", "sub_class", "labels_subclass"],
    "total_pages": ["totalPages", "total_pages"],
    "source":     ["source", "annotation_round"],
}


def pick(df: pd.DataFrame, field: str):
    for c in ALIASES[field]:
        if c in df.columns:
            return df[c]
    return pd.Series([None] * len(df), index=df.index)


def hc_column(df: pd.DataFrame):
    """HC0.12 / HC0.04 ... — machine cluster column, name differs per file."""
    cols = [c for c in df.columns if c.upper().startswith("HC")]
    return (df[cols[0]], cols[0]) if cols else (pd.Series([None] * len(df), index=df.index), None)


def load_csv(path: Path, kind: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"id": str, "book_id": str, "ESTCID": str}, low_memory=False)
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed")], errors="ignore")
    hc, hc_col = hc_column(df)

    out = pd.DataFrame(index=df.index)
    out["image_id"] = pick(df, "id").map(lambda v: N.image_id_of(v) if pd.notna(v) else None)
    out["box"] = pick(df, "boxes").map(N.parse_box)
    out["kind"] = kind
    out["superclass"] = pick(df, "superclass").fillna("").astype(str).str.strip()
    out["subclass"] = pick(df, "subclass").fillna("").astype(str).str.strip()
    out["estc"] = pick(df, "estc")
    out["title"] = pick(df, "title")
    out["year"] = pd.to_numeric(pick(df, "year"), errors="coerce")
    out["publishers"] = pick(df, "publishers")
    out["printers"] = pick(df, "printers")
    out["total_pages"] = pd.to_numeric(pick(df, "total_pages"), errors="coerce")
    out["hc_cluster"] = hc.astype(str) if hc_col else None
    out["hc_column"] = hc_col
    out["label_src"] = pick(df, "source").fillna(path.name)

    bad_id = out["image_id"].isna() | ~out["image_id"].str.fullmatch(r"\d{15}", na=False)
    bad_box = out["box"].isna()
    if bad_id.any() or bad_box.any():
        print(f"  ! dropping {int(bad_id.sum())} rows with bad id and "
              f"{int((bad_box & ~bad_id).sum())} with unparseable boxes")
    out = out[~bad_id & ~bad_box].copy()

    out["book_id"] = out["image_id"].map(N.book_id_of)
    out["page"] = out["image_id"].map(N.page_of)
    out["box_key"] = out["box"].map(N.box_key)
    out["oid"] = [N.make_oid(i, b) for i, b in zip(out["image_id"], out["box"])]
    lvl = [N.split_class(s, p) for s, p in zip(out["subclass"], out["superclass"])]
    out["superclass"] = [a for a, _, _ in lvl]
    out["subclass"] = [b for _, b, _ in lvl]
    out["variant"] = [c for _, _, c in lvl]
    out["class_path"] = [N.class_path(*t) for t in lvl]

    n0 = len(out)
    out = out.drop_duplicates("oid", keep="first")
    if len(out) < n0:
        print(f"  ! {n0 - len(out)} duplicate (id+boxes) rows collapsed")
    return out


def upsert(conn, frames: list[pd.DataFrame], replay: bool = True):
    all_df = pd.concat(frames, ignore_index=True)
    cur = conn.cursor()

    # ---- books: one row per book_id, richest record wins -------------------
    books = (all_df.sort_values("title", na_position="last")
                   .groupby("book_id", as_index=False)
                   .agg(estc=("estc", "first"), title=("title", "first"), year=("year", "min"),
                        total_pages=("total_pages", "max"),
                        publishers=("publishers", "first"), printers=("printers", "first"),
                        max_page=("page", "max")))
    books["total_pages"] = books["total_pages"].fillna(books["max_page"])
    rows = [(r.book_id, r.estc, r.title, N.norm_title(r.title),
             None if pd.isna(r.year) else int(r.year),
             None if pd.isna(r.total_pages) else int(r.total_pages),
             N.is_tonson(r.publishers, r.printers),
             None if pd.isna(r.publishers) else str(r.publishers),
             None if pd.isna(r.printers) else str(r.printers))
            for r in books.itertuples()]
    psycopg2.extras.execute_values(cur, """
        INSERT INTO book (book_id, estc_id, full_title, title_norm, year, total_pages,
                          is_tonson, publishers_raw, printers_raw)
        VALUES %s ON CONFLICT (book_id) DO UPDATE SET
            estc_id = COALESCE(EXCLUDED.estc_id, book.estc_id),
            full_title = COALESCE(EXCLUDED.full_title, book.full_title),
            title_norm = COALESCE(EXCLUDED.title_norm, book.title_norm),
            year = COALESCE(EXCLUDED.year, book.year),
            total_pages = GREATEST(COALESCE(EXCLUDED.total_pages, 0),
                                   COALESCE(book.total_pages, 0)),
            is_tonson = book.is_tonson OR EXCLUDED.is_tonson,
            publishers_raw = COALESCE(EXCLUDED.publishers_raw, book.publishers_raw),
            printers_raw = COALESCE(EXCLUDED.printers_raw, book.printers_raw)
    """, rows, page_size=1000)
    print(f"  books upserted: {len(rows)}")

    # ---- agents -----------------------------------------------------------
    pairs, links = {}, set()
    for r in books.itertuples():
        for role, cell in (("publisher", r.publishers), ("printer", r.printers)):
            for key, disp in N.norm_agents(cell):
                pairs.setdefault(key, disp)
                links.add((r.book_id, key, role))
    psycopg2.extras.execute_values(cur, """
        INSERT INTO agent (name, display_name) VALUES %s
        ON CONFLICT (name) DO UPDATE SET display_name =
            COALESCE(agent.display_name, EXCLUDED.display_name)
    """, list(pairs.items()), page_size=1000)
    cur.execute("SELECT name, agent_id FROM agent")
    amap = dict(cur.fetchall())
    psycopg2.extras.execute_values(cur, """
        INSERT INTO book_agent (book_id, agent_id, role) VALUES %s ON CONFLICT DO NOTHING
    """, [(b, amap[k], role) for b, k, role in links if k in amap], page_size=2000)
    print(f"  agents: {len(pairs)}   book-agent links: {len(links)}")

    # ---- ornaments --------------------------------------------------------
    orn = [(r.oid, r.image_id, r.box_key, r.box[0], r.box[1], r.box[2], r.box[3],
            r.kind, r.book_id, None if pd.isna(r.page) else int(r.page),
            r.superclass or None, r.subclass or None, r.variant or "",
            r.class_path or None, str(r.label_src),
            None if r.hc_cluster in (None, "nan") else r.hc_cluster, r.hc_column)
           for r in all_df.itertuples()]
    psycopg2.extras.execute_values(cur, """
        INSERT INTO ornament (oid, image_id, box_key, x1, y1, x2, y2, kind, book_id, page,
                              superclass, subclass, variant, class_path, label_src,
                              hc_cluster, hc_column)
        VALUES %s ON CONFLICT (oid) DO UPDATE SET
            kind = EXCLUDED.kind, superclass = EXCLUDED.superclass,
            subclass = EXCLUDED.subclass, variant = EXCLUDED.variant,
            class_path = EXCLUDED.class_path, label_src = EXCLUDED.label_src,
            hc_cluster = COALESCE(EXCLUDED.hc_cluster, ornament.hc_cluster),
            hc_column = COALESCE(EXCLUDED.hc_column, ornament.hc_column)
    """, orn, page_size=1000)
    print(f"  ornaments upserted: {len(orn)}")

    # ---- re-apply accepted admin corrections -------------------------------
    if replay:
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (oid) oid, new_superclass, new_subclass, new_variant
                FROM label_change ORDER BY oid, changed_at DESC)
            UPDATE ornament o SET superclass = l.new_superclass, subclass = l.new_subclass,
                   variant = COALESCE(l.new_variant, ''),
                   class_path = concat_ws('/', NULLIF(l.new_superclass,''),
                                               NULLIF(l.new_subclass,''),
                                               NULLIF(l.new_variant,''))
            FROM latest l WHERE o.oid = l.oid
        """)
        print(f"  admin corrections re-applied: {cur.rowcount}")

    conn.commit()
    cur.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", action="append", nargs=2, metavar=("KIND", "CSV"), required=True,
                    help="e.g. --kind HP data/HP.csv  (repeatable)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--schema", default=str(Path(__file__).with_name("schema.sql")))
    ap.add_argument("--no-replay", action="store_true")
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")

    conn = psycopg2.connect(a.dsn)
    with conn.cursor() as c:
        c.execute(Path(a.schema).read_text())
    conn.commit()
    print("schema ok")

    frames = []
    for kind, path in a.kind:
        print(f"[{kind}] {path}")
        frames.append(load_csv(Path(path), kind.upper()))
        print(f"  parsed rows: {len(frames[-1])}")
    upsert(conn, frames, replay=not a.no_replay)

    with conn.cursor() as c:
        c.execute("ANALYZE")
        for t in ("book", "agent", "ornament"):
            c.execute(f"SELECT count(*) FROM {t}")
            print(f"{t:10s} {c.fetchone()[0]:>8,}")
        c.execute("SELECT kind, count(*) FROM ornament GROUP BY kind ORDER BY 1")
        print("by kind:", dict(c.fetchall()))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
