#!/usr/bin/env python3
"""Load ECCO ornament CSVs into Postgres (schema v2, DESIGN.md §3).

    python -m etl.load \
        --src HP-pred data/HP.csv   --src DI-pred data/DI.csv \
        --src FT-pred data/FT.csv   --src WE-pred data/WETPPD.csv \
        --src HP-ann  data/HP_concat_annotation.csv \
        --src DI-ann  data/DI_annotation.csv

Whatever order you give, sources load as: predictions (DI, FT, HP), then
annotations (which attach to the prediction rows), then WE predictions (which
are de-duplicated against everything else). Re-running upserts on id+boxes.

Flags
    --replace        clear the given sources' labels first (a new model run
                     replaces its clusters instead of merging into them)
    --reset          drop and recreate the schema (needed once for v1 -> v2);
                     refuses if reports or label changes exist, unless
                     --force-reset
    --names FILE     formal HP names: the 'Subclass_id: ...' text list or a CSV
    --books FILE     extra book metadata (book_id or ESTCID, year, publishers,
                     printers, fullTitle) to fill gaps; never overwrites
    --we-iou 0.9     IoU above which a WE box duplicates another box
    --ann-iou 0.9    IoU above which an annotation box is the same detection
    --no-replay      do not re-apply admin corrections after loading
    --no-derived     skip rebuilding the summary views
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, derived  # noqa: E402
from app import normalize as N  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_NAMES = HERE / "data" / "hp_superclass_names.csv"

# Column names differ in every file; every lookup goes through this table.
ALIASES = {
    "id": ["id"],
    "boxes": ["boxes", "box", "bbox"],
    "title": ["fullTitle", "full_title", "title"],
    "estc": ["ESTCID", "estc_id", "ESTC"],
    "year": ["year"],
    "publishers": ["publishers", "publisher"],
    "printers": ["printers", "printer"],
    "total_pages": ["totalPages", "total_pages"],
    "superclass": ["superclass", "super_class"],
    "subclass": ["subclass", "subclass?", "sub_class"],
    "round": ["source", "annotation_round"],
}
ORN_COLS = ["oid", "image_id", "box_key", "x1", "y1", "x2", "y2", "kind", "book_id", "page"]


def log(msg):
    print(msg, flush=True)


# =============================================================== reading
def read_source(path: Path, src: str) -> pd.DataFrame:
    spec = config.SOURCES[src]
    header = list(pd.read_csv(path, nrows=0).columns)
    want = {c for names in ALIASES.values() for c in names}
    hc_cols = [c for c in header if str(c).upper().startswith("HC")]
    cols = [c for c in header if c in want] + hc_cols[:1]
    df = pd.read_csv(path, dtype=str, usecols=cols, keep_default_na=True, low_memory=False)

    def pick(field):
        for c in ALIASES[field]:
            if c in df.columns:
                return df[c]
        return pd.Series([None] * len(df), index=df.index, dtype=object)

    out = pd.DataFrame(index=df.index)
    out["image_id"] = pick("id").map(lambda v: N.image_id_of(v) if isinstance(v, str) else None)
    boxes = pick("boxes").map(N.parse_box)
    bad_id = ~out["image_id"].fillna("").str.fullmatch(r"\d{15}")
    bad_box = boxes.isna()
    if bad_id.any() or bad_box.any():
        log(f"  ! dropping {int(bad_id.sum())} rows with a bad id and "
            f"{int((bad_box & ~bad_id).sum())} with an unparseable box")
    keep = ~bad_id & ~bad_box
    out, boxes, df = out[keep].copy(), boxes[keep], df[keep].copy()
    out[["x1", "y1", "x2", "y2"]] = pd.DataFrame(boxes.tolist(), index=out.index)
    out["box_key"] = boxes.map(N.box_key)
    out["oid"] = [N.make_oid(i, b) for i, b in zip(out["image_id"], boxes)]
    out["kind"] = spec["kind"]
    out["book_id"] = out["image_id"].str[:10]
    out["page"] = out["image_id"].map(N.page_of)
    out["title"] = pick("title")
    out["estc"] = pick("estc")
    out["year"] = pd.to_numeric(pick("year"), errors="coerce")
    out["publishers"] = pick("publishers")
    out["printers"] = pick("printers")
    out["total_pages"] = pd.to_numeric(pick("total_pages"), errors="coerce")

    if spec["family"] == "pred":
        if not hc_cols:
            sys.exit(f"{path}: no HC* cluster column found for {src}")
        out["hc_column"] = hc_cols[0]
        out["hc_cluster"] = df[hc_cols[0]].map(N.norm_cluster)
        log(f"  cluster column {hc_cols[0]}: {out['hc_cluster'].nunique()} clusters, "
            f"{int(out['hc_cluster'].isna().sum())} rows unclustered")
    else:
        sup_raw = pick("superclass").fillna("").str.strip()
        sub_raw = pick("subclass").fillna("").str.strip()
        rows = []
        for sup, sub in zip(sup_raw, sub_raw):
            if "variant" in spec["levels"]:
                if not sup and sub:
                    try:
                        sup = N.parse_label(src, sub)[0]
                    except ValueError:
                        pass
                s, b, v = N.split_class(sub, sup)
            else:                                 # DI/FT: superclass only, raw subclass kept
                s, b, v = sup, (sub or None), ""
            rows.append((s or None, b or None, v or "", N.class_path_for(src, s, b, v)))
        out[["superclass", "subclass", "variant", "class_path"]] = pd.DataFrame(
            rows, index=out.index)
        rnd = pick("round")
        out["ann_round"] = rnd.where(rnd.notna(), path.stem)
        nolab = out["class_path"].isna()
        if nolab.any():
            log(f"  ! {int(nolab.sum())} annotation rows without a class were dropped")
            out = out[~nolab]

    n0 = len(out)
    out = out.drop_duplicates("oid", keep="first")
    if len(out) < n0:
        log(f"  within-file duplicates (id+boxes) removed: {n0 - len(out)}")
    return out


# =============================================================== helpers
def copy_frame(cur, table: str, df: pd.DataFrame, cols: list[str]):
    buf = io.StringIO()
    df[cols].to_csv(buf, index=False, header=False, na_rep="\\N", quoting=csv.QUOTE_MINIMAL)
    buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH "
                    f"(FORMAT csv, NULL '\\N')", buf)


def upsert_books(cur, df: pd.DataFrame):
    """Metadata fills gaps only: the first non-empty value for a book wins."""
    g = df.groupby("book_id", sort=False)
    books = pd.DataFrame({
        "estc": g["estc"].first(), "title": g["title"].first(), "year": g["year"].min(),
        "total_pages": g["total_pages"].max(), "max_page": g["page"].max(),
        "publishers": g["publishers"].first(), "printers": g["printers"].first()})
    rows = []
    for bid, r in books.iterrows():
        tp = r.total_pages if pd.notna(r.total_pages) else r.max_page
        rows.append((bid, None if pd.isna(r.estc) else r.estc,
                     None if pd.isna(r.title) else r.title,
                     N.norm_title(r.title) if pd.notna(r.title) else None,
                     None if pd.isna(r.year) else int(r.year),
                     None if pd.isna(tp) else int(tp),
                     N.is_tonson(r.publishers, r.printers),
                     None if pd.isna(r.publishers) else str(r.publishers),
                     None if pd.isna(r.printers) else str(r.printers)))
    psycopg2.extras.execute_values(cur, """
        INSERT INTO book (book_id, estc_id, full_title, title_norm, year, total_pages,
                          is_tonson, publishers_raw, printers_raw)
        VALUES %s ON CONFLICT (book_id) DO UPDATE SET
            estc_id = COALESCE(book.estc_id, EXCLUDED.estc_id),
            full_title = COALESCE(book.full_title, EXCLUDED.full_title),
            title_norm = COALESCE(book.title_norm, EXCLUDED.title_norm),
            year = COALESCE(book.year, EXCLUDED.year),
            total_pages = GREATEST(book.total_pages, EXCLUDED.total_pages),
            is_tonson = book.is_tonson OR EXCLUDED.is_tonson,
            publishers_raw = COALESCE(book.publishers_raw, EXCLUDED.publishers_raw),
            printers_raw = COALESCE(book.printers_raw, EXCLUDED.printers_raw)
    """, rows, page_size=2000)
    return len(rows)


def rebuild_agents(conn):
    """Re-derive book_agent from the imprint strings of every book. Agent ids
    are stable for names that already exist."""
    cur = conn.cursor()
    cur.execute("SELECT book_id, publishers_raw, printers_raw FROM book")
    links, forms = set(), {}
    for bid, pubs, prns in cur.fetchall():
        for role, cell in (("publisher", pubs), ("printer", prns)):
            for key, disp in N.norm_agents(cell):
                links.add((bid, key, role))
                forms.setdefault(key, Counter())[disp] += 1
    psycopg2.extras.execute_values(cur, """
        INSERT INTO agent (name, display_name) VALUES %s
        ON CONFLICT (name) DO UPDATE SET display_name = EXCLUDED.display_name
    """, [(k, c.most_common(1)[0][0]) for k, c in forms.items()], page_size=2000)
    cur.execute("SELECT name, agent_id FROM agent")
    amap = dict(cur.fetchall())
    cur.execute("DELETE FROM book_agent")
    psycopg2.extras.execute_values(
        cur, "INSERT INTO book_agent (book_id, agent_id, role) VALUES %s ON CONFLICT DO NOTHING",
        [(b, amap[k], r) for b, k, r in links if k in amap], page_size=5000)
    cur.execute("""UPDATE book b SET has_imprint =
                       EXISTS (SELECT 1 FROM book_agent ba WHERE ba.book_id = b.book_id),
                   is_tonson = (coalesce(publishers_raw, '') || ' ' || coalesce(printers_raw, ''))
                               ~* 'tonson|watts'""")
    conn.commit()
    log(f"agents: {len(forms):,}   book-agent links: {len(links):,}")


def pred_update_sql(src):
    return f"""
        INSERT INTO ornament ({', '.join(ORN_COLS)}, pred_src, hc_column, hc_cluster)
        SELECT {', '.join(ORN_COLS)}, '{src}', hc_column, hc_cluster FROM _in
        ON CONFLICT (oid) DO UPDATE SET
            pred_src = EXCLUDED.pred_src, hc_column = EXCLUDED.hc_column,
            hc_cluster = EXCLUDED.hc_cluster
        WHERE ornament.pred_src IS NULL OR ornament.pred_src = EXCLUDED.pred_src"""


def temp_table(cur, extra_cols: str):
    cur.execute("DROP TABLE IF EXISTS _in")
    cur.execute(f"""CREATE TEMP TABLE _in (
        oid TEXT PRIMARY KEY, image_id TEXT, box_key TEXT,
        x1 FLOAT8, y1 FLOAT8, x2 FLOAT8, y2 FLOAT8, kind TEXT, book_id TEXT, page INT
        {extra_cols})""")


# =============================================================== sources
def load_pred(conn, df, src, replace):
    cur = conn.cursor()
    upsert_books(cur, df)
    if replace:
        cur.execute("""UPDATE ornament SET pred_src = NULL, hc_column = NULL, hc_cluster = NULL,
                       cluster_rejected = FALSE WHERE pred_src = %s""", (src,))
        log(f"  --replace: cleared {cur.rowcount:,} existing {src} labels")
    temp_table(cur, ", hc_column TEXT, hc_cluster TEXT")
    copy_frame(cur, "_in", df, ORN_COLS + ["hc_column", "hc_cluster"])
    cur.execute("""SELECT o.pred_src, count(*) FROM _in t JOIN ornament o USING (oid)
                   WHERE o.pred_src IS NOT NULL AND o.pred_src <> %s GROUP BY 1""", (src,))
    for other, n in cur.fetchall():
        log(f"  ! {n:,} keys already belong to {other}; kept there (first loaded wins)")
    cur.execute(pred_update_sql(src))
    log(f"  ornaments upserted: {cur.rowcount:,}")
    conn.commit()


def iou_sql(a, b):
    return (f"(greatest(0, least({a}.x2,{b}.x2) - greatest({a}.x1,{b}.x1)) * "
            f"greatest(0, least({a}.y2,{b}.y2) - greatest({a}.y1,{b}.y1))) / "
            f"nullif(({a}.x2-{a}.x1)*({a}.y2-{a}.y1) + ({b}.x2-{b}.x1)*({b}.y2-{b}.y1) - "
            f"greatest(0, least({a}.x2,{b}.x2) - greatest({a}.x1,{b}.x1)) * "
            f"greatest(0, least({a}.y2,{b}.y2) - greatest({a}.y1,{b}.y1)), 0)")


def load_we(conn, df, src, replace, thresh):
    """WETPPD overlaps heavily with the other files (DESIGN §3.4)."""
    cur = conn.cursor()
    if replace:
        cur.execute("""UPDATE ornament SET pred_src = NULL, hc_column = NULL, hc_cluster = NULL,
                       cluster_rejected = FALSE WHERE pred_src = %s""", (src,))
    temp_table(cur, ", hc_column TEXT, hc_cluster TEXT")
    copy_frame(cur, "_in", df, ORN_COLS + ["hc_column", "hc_cluster"])
    # step 2: exact key already held by another source
    cur.execute("""DELETE FROM _in t USING ornament o WHERE o.oid = t.oid
                   AND ((o.pred_src IS NOT NULL AND o.pred_src <> %s)
                        OR (o.pred_src IS NULL AND o.ann_src IS NOT NULL))""", (src,))
    log(f"  exact duplicates of other sources removed: {cur.rowcount:,}")
    # step 3: IoU against every other box on the same page
    cur.execute(f"""
        CREATE TEMP TABLE _iou AS
        SELECT t.oid, max({iou_sql('t', 'o')}) AS iou
        FROM _in t JOIN ornament o ON o.image_id = t.image_id AND o.oid <> t.oid
        WHERE NOT (o.pred_src IS NOT DISTINCT FROM %s AND o.ann_src IS NULL)
        GROUP BY t.oid""", (src,))
    bins = [0, .5, .8, .9, .95, .99, 1.0001]
    cur.execute("SELECT iou FROM _iou WHERE iou > 0")
    ious = np.array([r[0] for r in cur.fetchall()], dtype=float)
    if len(ious):
        hist, _ = np.histogram(ious, bins=bins)
        log("  IoU of WE boxes vs other boxes on the same page (IoU > 0):")
        for lo, hi, n in zip(bins[:-1], bins[1:], hist):
            log(f"    [{lo:.2f}, {min(hi, 1):.2f})  {n:,}")
    cur.execute("DELETE FROM _in t USING _iou i WHERE i.oid = t.oid AND i.iou >= %s", (thresh,))
    log(f"  removed as IoU >= {thresh} duplicates: {cur.rowcount:,}")
    cur.execute("DROP TABLE _iou")
    cur.execute("SELECT count(*) FROM _in")
    keep = cur.fetchone()[0]
    cur.execute("SELECT oid FROM _in")
    books = df[df["oid"].isin({r[0] for r in cur.fetchall()})]
    upsert_books(cur, books)
    cur.execute(pred_update_sql(src))
    log(f"  ornaments upserted: {cur.rowcount:,}")
    conn.commit()


def load_ann(conn, df, src, replace, thresh):
    """Attach human labels to existing detections (exact key, then IoU)."""
    cur = conn.cursor()
    upsert_books(cur, df)
    if replace:
        cur.execute("""UPDATE ornament SET ann_src = NULL, superclass = NULL, subclass = NULL,
                       variant = '', class_path = NULL, ann_round = NULL
                       WHERE ann_src = %s""", (src,))
        log(f"  --replace: cleared {cur.rowcount:,} existing {src} labels")
    temp_table(cur, ", superclass TEXT, subclass TEXT, variant TEXT, class_path TEXT, "
                    "ann_round TEXT, target TEXT")
    copy_frame(cur, "_in", df, ORN_COLS + ["superclass", "subclass", "variant",
                                           "class_path", "ann_round"])
    cur.execute("UPDATE _in t SET target = t.oid FROM ornament o WHERE o.oid = t.oid")
    n_exact = cur.rowcount
    # IoU fallback: same page, same kind preferred, else a WE detection
    cur.execute(f"""
        SELECT t.oid, o.oid, {iou_sql('t', 'o')} AS iou, (o.kind = t.kind) AS same
        FROM _in t JOIN ornament o ON o.image_id = t.image_id
        WHERE t.target IS NULL AND (o.kind = t.kind OR o.kind = 'WE')""")
    pairs = [p for p in cur.fetchall() if p[2] is not None and p[2] >= thresh]
    pairs.sort(key=lambda p: (not p[3], -p[2]))
    used, assign = set(), {}
    cur.execute("SELECT target FROM _in WHERE target IS NOT NULL")
    used.update(r[0] for r in cur.fetchall())
    for t_oid, o_oid, _, _ in pairs:
        if t_oid not in assign and o_oid not in used:
            assign[t_oid] = o_oid
            used.add(o_oid)
    if assign:
        psycopg2.extras.execute_values(
            cur, "UPDATE _in t SET target = v.o FROM (VALUES %s) v(t, o) WHERE t.oid = v.t",
            list(assign.items()), page_size=5000)
        psycopg2.extras.execute_values(
            cur, """INSERT INTO ornament_alias (alias_oid, oid) VALUES %s
                    ON CONFLICT (alias_oid) DO UPDATE SET oid = EXCLUDED.oid""",
            list(assign.items()), page_size=5000)
    # unmatched -> new detections with a human label only
    cur.execute(f"""INSERT INTO ornament ({', '.join(ORN_COLS)})
                    SELECT {', '.join(ORN_COLS)} FROM _in WHERE target IS NULL
                    ON CONFLICT (oid) DO NOTHING""")
    n_new = cur.rowcount
    cur.execute("UPDATE _in SET target = oid WHERE target IS NULL")
    cur.execute("""SELECT o.ann_src, count(*) FROM _in t JOIN ornament o ON o.oid = t.target
                   WHERE o.ann_src IS NOT NULL AND o.ann_src <> %s GROUP BY 1""", (src,))
    for other, n in cur.fetchall():
        log(f"  ! {n:,} detections already labelled by {other} are relabelled by {src}")
    cur.execute("""UPDATE ornament o SET ann_src = %s, superclass = t.superclass,
                       subclass = t.subclass, variant = coalesce(t.variant, ''),
                       class_path = t.class_path, ann_round = t.ann_round,
                       kind = CASE WHEN o.kind = 'WE' THEN t.kind ELSE o.kind END
                   FROM _in t WHERE o.oid = t.target""", (src,))
    log(f"  attached by exact key: {n_exact:,}   by IoU >= {thresh}: {len(assign):,}   "
        f"new detections: {n_new:,}")
    conn.commit()


# =============================================================== after load
def replay(conn):
    """Re-apply the latest human change per ornament (DESIGN §3.8)."""
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT ON (oid) oid, new_ann_src, new_superclass, new_subclass,
                          new_variant, new_cluster_rejected
                   FROM label_change ORDER BY oid, changed_at DESC, change_id DESC""")
    rows = []
    for oid, s, sup, sub, var, rej in cur.fetchall():
        path = N.class_path_for(s, sup, sub, var or "") if s in config.SOURCES else None
        rows.append((oid, s if path else None, sup if path else None, sub if path else None,
                     (var or "") if path else "", path, bool(rej)))
    if rows:
        psycopg2.extras.execute_values(cur, """
            UPDATE ornament o SET ann_src = v.s, superclass = v.sup, subclass = v.sub,
                   variant = v.var, class_path = v.path, cluster_rejected = v.rej
            FROM (VALUES %s) v(oid, s, sup, sub, var, path, rej) WHERE o.oid = v.oid
        """, rows, page_size=2000)
    conn.commit()
    log(f"admin corrections re-applied: {len(rows):,}")


def load_names(conn, path: Path):
    """Formal names. Accepts the CSV shipped in etl/data or the original
    'Subclass_id: ... / Group: ... / Subgroup: ...' text list."""
    text = path.read_text(encoding="utf-8")
    sup_rows, sub_rows = {}, {}
    if text.lstrip().lower().startswith("superclass,"):
        for r in csv.DictReader(io.StringIO(text)):
            sup_rows[r["superclass"].strip()] = (r["group"].strip(),
                                                 r.get("previous_annotation_name", "").strip())
    else:
        seen_sub = {}
        for block in re.split(r"\n\s*\n(?=\s*Subclass_id:)", text):
            f = dict((m.group(1).strip().lower(), m.group(2).strip())
                     for m in re.finditer(r"^\s*([A-Za-z_]+):\s*(.*)$", block, re.M))
            sid = f.get("subclass_id", "")
            if not re.fullmatch(r"C\d{3}_\d{2}", sid):
                if sid:
                    log(f"  names: skipped entry '{sid}'")
                continue
            sup = sid.split("_")[0]
            sup_rows.setdefault(sup, (f.get("group", ""), f.get("previous_annotation_name", "")))
            sg = f.get("subgroup", "")
            sub_rows[sid] = sg
            if sg in seen_sub:
                log(f"  names: {sid} and {seen_sub[sg]} share the subgroup name '{sg}'")
            seen_sub.setdefault(sg, sid)
    rows = [("HP-ann", "superclass", k, g or None, p or None) for k, (g, p) in sup_rows.items()]
    rows += [("HP-ann", "subclass", k, g or None, None) for k, g in sub_rows.items()]
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur, """
        INSERT INTO class_name (src, level, value, name, alt_name) VALUES %s
        ON CONFLICT (src, level, value) DO UPDATE SET name = EXCLUDED.name,
            alt_name = COALESCE(EXCLUDED.alt_name, class_name.alt_name)""", rows)
    conn.commit()
    log(f"names: {len(sup_rows)} superclasses, {len(sub_rows)} subclasses ({path.name})")


def load_book_meta(conn, path: Path):
    df = pd.read_csv(path, dtype=str)
    cur = conn.cursor()
    n = 0
    for _, r in df.iterrows():
        vals = dict(title=r.get("fullTitle"), year=pd.to_numeric(r.get("year"), errors="coerce"),
                    pubs=r.get("publishers"), prns=r.get("printers"))
        vals = {k: (None if (v is None or (isinstance(v, float) and np.isnan(v))) else v)
                for k, v in vals.items()}
        if vals["year"] is not None:
            vals["year"] = int(vals["year"])
        if isinstance(r.get("book_id"), str) and r["book_id"].strip():
            where, key = "book_id = %(k)s", r["book_id"].strip().zfill(10)
        elif isinstance(r.get("ESTCID"), str):
            where, key = "estc_id = %(k)s", r["ESTCID"].strip()
        else:
            continue
        cur.execute(f"""UPDATE book SET full_title = COALESCE(full_title, %(title)s),
                            title_norm = COALESCE(title_norm, %(tn)s),
                            year = COALESCE(year, %(year)s),
                            publishers_raw = COALESCE(publishers_raw, %(pubs)s),
                            printers_raw = COALESCE(printers_raw, %(prns)s)
                        WHERE {where}""",
                    dict(vals, tn=N.norm_title(vals["title"]) if vals["title"] else None, k=key))
        n += cur.rowcount
    conn.commit()
    log(f"book metadata: {n:,} books updated from {path.name}")


def schema_state(cur):
    cur.execute("SELECT to_regclass('ornament') IS NOT NULL")
    if not cur.fetchone()[0]:
        return "empty"
    cur.execute("""SELECT count(*) FROM information_schema.columns
                   WHERE table_name = 'ornament' AND column_name = 'pred_src'""")
    return "v2" if cur.fetchone()[0] else "v1"


def reset(conn, force):
    cur = conn.cursor()
    keep = 0
    for t in ("report", "label_change"):
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (t,))
        n = 0
        if cur.fetchone()[0]:
            cur.execute(f"SELECT count(*) FROM {t}")
            n = cur.fetchone()[0]
        if n:
            log(f"  {t}: {n} rows")
        keep += n
    if keep and not force:
        sys.exit("refusing to --reset: reports/label changes exist. Export them "
                 "(pg_dump -t report -t label_change) and re-run with --force-reset.")
    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
    for (mv,) in cur.fetchall():
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {mv} CASCADE")
    for t in ("v_ornament_full", "v_subclass_df"):
        cur.execute(f"DROP VIEW IF EXISTS {t} CASCADE")
    for t in ("lending_review", "label_change", "report", "crop_store", "class_name",
              "ornament_alias", "ornament", "book_agent", "agent", "book", "upload", "meta"):
        cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    conn.commit()
    log("schema dropped")


def summary(conn):
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM ornament")
    log(f"\nornaments {cur.fetchone()[0]:,}")
    cur.execute("""SELECT coalesce(ann_src, '-') AS a, coalesce(pred_src, '-') AS p, count(*)
                   FROM ornament GROUP BY 1, 2 ORDER BY 1, 2""")
    for a, p, n in cur.fetchall():
        log(f"  human {a:8s} machine {p:8s} {n:>9,}")
    cur.execute("""SELECT o.kind, count(DISTINCT o.book_id),
                          count(DISTINCT o.book_id) FILTER (WHERE b.year IS NULL),
                          count(DISTINCT o.book_id) FILTER (WHERE NOT b.has_imprint)
                   FROM ornament o JOIN book b USING (book_id) GROUP BY 1 ORDER BY 1""")
    log("books by ornament kind: total / without year / without imprint")
    for k, n, ny, ni in cur.fetchall():
        log(f"  {k}  {n:>7,} / {ny:>7,} / {ni:>7,}")


FAMILY_ORDER = {"pred": 0, "ann": 1}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--src", action="append", nargs=2, metavar=("SOURCE", "CSV"), default=[],
                    help="e.g. --src HP-ann data/HP_concat_annotation.csv (repeatable)")
    ap.add_argument("--kind", action="append", nargs=2, metavar=("KIND", "CSV"), default=[],
                    help=argparse.SUPPRESS)          # v1 spelling, mapped to <KIND>-ann
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--force-reset", action="store_true")
    ap.add_argument("--names", type=Path)
    ap.add_argument("--books", type=Path)
    ap.add_argument("--we-iou", type=float, default=0.9)
    ap.add_argument("--ann-iou", type=float, default=0.9)
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--no-derived", action="store_true")
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    jobs = [(s, Path(p)) for s, p in a.src] + [(f"{k.upper()}-ann", Path(p)) for k, p in a.kind]
    for s, p in jobs:
        if s not in config.SOURCES:
            sys.exit(f"unknown source {s}; one of {', '.join(config.SOURCES)}")
        if not p.exists():
            sys.exit(f"file not found: {p}")

    conn = psycopg2.connect(a.dsn)
    cur = conn.cursor()
    state = schema_state(cur)
    if a.reset or a.force_reset:
        reset(conn, a.force_reset)
    elif state == "v1":
        sys.exit("This database has the v1 schema. Re-run once with --reset (DESIGN §10).")
    cur.execute((HERE / "schema.sql").read_text())
    conn.commit()
    log("schema ok")

    t0 = time.time()
    order = sorted(jobs, key=lambda j: (j[0] == "WE-pred",
                                        FAMILY_ORDER[config.SOURCES[j[0]]["family"]],
                                        list(config.SOURCES).index(j[0])))
    for src, path in order:
        t = time.time()
        log(f"[{src}] {path}")
        df = read_source(path, src)
        log(f"  parsed rows: {len(df):,}")
        if src == "WE-pred":
            load_we(conn, df, src, a.replace, a.we_iou)
        elif config.SOURCES[src]["family"] == "pred":
            load_pred(conn, df, src, a.replace)
        else:
            load_ann(conn, df, src, a.replace, a.ann_iou)
        del df
        log(f"  {time.time() - t:.1f}s")

    load_names(conn, DEFAULT_NAMES)
    if a.names:
        load_names(conn, a.names)
    if a.books:
        load_book_meta(conn, a.books)
    if jobs or a.books:
        rebuild_agents(conn)
    if jobs and a.replace:
        cur.execute("""DELETE FROM ornament o WHERE ann_src IS NULL AND pred_src IS NULL
                       AND NOT EXISTS (SELECT 1 FROM report r WHERE r.oid = o.oid)""")
        log(f"orphan ornaments removed: {cur.rowcount:,}")
        conn.commit()
    if not a.no_replay:
        replay(conn)
    cur.execute("ANALYZE")
    conn.commit()
    if not a.no_derived:
        log(f"summary views rebuilt in {derived.rebuild(conn):.1f}s")
    summary(conn)
    log(f"done in {time.time() - t0:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
