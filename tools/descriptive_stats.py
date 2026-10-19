#!/usr/bin/env python3
"""Descriptive statistics of the ECCO Ornament Atlas (Tonson paper §2.1, DES report §2).

    python tools/descriptive_stats.py --out stats/
    python tools/descriptive_stats.py --out stats/ --ecco-docs 207000 \
        --ecco-pages ecco_pages.csv --formats estc_formats.csv --split-year 1750

Reads the atlas database (DATABASE_URL) and writes:

    stats/summary.json     every number, with null where the data is missing
    stats/by_kind.csv      ornaments and books per ornament type
    stats/by_decade.csv    books, ornaments and rates per decade
    stats/books.csv        one row per book: the "simple full dataset"
    stats/blocks.csv       one row per block (human variant) or machine cluster
    stats/paragraph.md     the §2.1 paragraph with the numbers filled in;
                           [[name]] marks a number that needs more data

Run it again after each data load; the paragraph updates itself.

Optional inputs (the atlas holds only books that have at least one ornament,
so some denominators must come from outside):
    --ecco-docs N          number of documents in ECCO (for "x % of ECCO")
    --ecco-pages FILE      CSV: book_id (10-digit ECCO id), pages — page counts
                           for ECCO documents; without it, totalPages from the
                           source CSVs is used where present
    --formats FILE         CSV: book_id or estc_id, format (folio, 4to, 8vo, 12mo …)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

KINDS = ["DI", "FT", "HP", "TP"]
KIND_LABEL = {"DI": "decorative initials", "FT": "factotums", "HP": "headpieces",
              "TP": "tailpieces and printers' devices"}


# ----------------------------------------------------------------- helpers
def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def quantiles(xs, ps=(0.25, 0.5, 0.75, 0.9, 0.99)):
    if not xs:
        return {str(p): None for p in ps}
    s = sorted(xs)
    out = {}
    for p in ps:
        k = (len(s) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        out[str(p)] = s[lo] + (s[hi] - s[lo]) * (k - lo)
    return out


def gini(xs):
    s = sorted(x for x in xs if x is not None)
    n, tot = len(s), sum(s)
    if n == 0 or tot == 0:
        return None
    return sum((2 * i - n - 1) * x for i, x in enumerate(s, 1)) / (n * tot)


def skewness(xs):
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / n)
    return None if sd == 0 else sum((x - m) ** 3 for x in xs) / (n * sd ** 3)


def rnd(v, d=2):
    return None if v is None else round(v, d)


def norm_estc(v):
    s = re.sub(r"\s+", "", str(v or "")).upper()
    return re.sub(r"^([A-Z])0+", r"\1", s) or None


def read_csv_map(path, key_cols, val_col):
    """{key: value} from a CSV; key is the first of key_cols that exists."""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in r.fieldnames or []}
        key = next((cols[k] for k in key_cols if k in cols), None)
        val = cols.get(val_col)
        if not key or not val:
            sys.exit(f"{path}: need a column {key_cols} and a column '{val_col}'; "
                     f"found {r.fieldnames}")
        for row in r:
            k, v = (row[key] or "").strip(), (row[val] or "").strip()
            if k and v:
                out[k] = v
    return out, key


def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) for c in cols])


class Fill(dict):
    """str.format_map helper: a missing or None value prints as [[name]]."""
    def __missing__(self, key):
        return f"[[{key}]]"


def fmt(v, kind="int"):
    if v is None:
        return None
    if kind == "int":
        return f"{int(round(v)):,}"
    if kind == "pct":
        return f"{100 * v:.1f}%"
    return f"{v:.2f}"


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("stats"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--ecco-docs", type=int, default=None)
    ap.add_argument("--ecco-pages", type=Path, default=None)
    ap.add_argument("--formats", type=Path, default=None)
    ap.add_argument("--split-year", type=int, default=1750,
                    help="year for the before/after comparison (default 1750)")
    ap.add_argument("--year-min", type=int, default=1700)
    ap.add_argument("--year-max", type=int, default=1800)
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    a.out.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(a.dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ---- books with their ornament counts per type
    books = q(cur, """
        SELECT b.book_id, b.estc_id, b.year, b.total_pages, b.has_imprint, b.is_tonson,
               b.place_key,
               count(o.oid) AS n_orn,
               count(DISTINCT o.page) AS pages_with_orn,
               count(o.oid) FILTER (WHERE o.kind = 'DI') AS n_di,
               count(o.oid) FILTER (WHERE o.kind = 'FT') AS n_ft,
               count(o.oid) FILTER (WHERE o.kind = 'HP') AS n_hp,
               count(o.oid) FILTER (WHERE o.kind = 'TP') AS n_tp,
               count(o.oid) FILTER (WHERE o.ann_src IS NOT NULL) AS n_human
        FROM book b JOIN ornament o USING (book_id)
        GROUP BY b.book_id""")

    pages_ext, pages_src = {}, "totalPages in the source CSVs"
    if a.ecco_pages:
        m, _ = read_csv_map(a.ecco_pages, ["book_id", "documentid", "docid", "ecco_id"], "pages")
        pages_ext = {k.zfill(10): int(float(v)) for k, v in m.items()}
        pages_src = a.ecco_pages.name
    formats, fmt_key = {}, None
    if a.formats:
        formats, fmt_key = read_csv_map(a.formats, ["book_id", "estc_id", "estcid", "estc"], "format")
        if "estc" in fmt_key.lower():
            formats = {norm_estc(k): v for k, v in formats.items()}
        else:
            formats = {k.zfill(10): v for k, v in formats.items()}

    for b in books:
        b["pages"] = pages_ext.get(b["book_id"]) or b["total_pages"]
        b["decade"] = (b["year"] // 10 * 10) if b["year"] else None
        b["orn_per_100_pages"] = (100 * b["n_orn"] / b["pages"]) if b["pages"] else None
        if formats:
            b["format"] = (formats.get(b["book_id"]) if not fmt_key or "estc" not in fmt_key.lower()
                           else formats.get(norm_estc(b["estc_id"])))
    write_csv(a.out / "books.csv", books,
              ["book_id", "estc_id", "year", "decade", "pages", "pages_with_orn", "n_orn",
               "n_di", "n_ft", "n_hp", "n_tp", "n_human", "orn_per_100_pages", "has_imprint",
               "is_tonson", "place_key"] + (["format"] if formats else []))

    n_books = len(books)
    n_orn = sum(b["n_orn"] for b in books)
    per_book = [b["n_orn"] for b in books]
    with_pages = [b for b in books if b["pages"]]
    dated = [b for b in books if b["year"]]

    # ---- by kind
    by_kind = []
    for k in KINDS:
        nk = sum(b[f"n_{k.lower()}"] for b in books)
        bk = sum(1 for b in books if b[f"n_{k.lower()}"])
        by_kind.append(dict(kind=k, label=KIND_LABEL[k], ornaments=nk, books=bk,
                            share_of_ornaments=rnd(nk / n_orn if n_orn else None, 4)))
    write_csv(a.out / "by_kind.csv", by_kind, ["kind", "label", "ornaments", "books",
                                               "share_of_ornaments"])

    # ---- by decade
    dec = defaultdict(list)
    for b in dated:
        dec[b["decade"]].append(b)
    by_decade = []
    for d in sorted(dec):
        bs = dec[d]
        o = sum(x["n_orn"] for x in bs)
        bp = [x for x in bs if x["pages"]]
        row = dict(decade=d, books=len(bs), ornaments=o,
                   ornaments_per_book=rnd(o / len(bs)),
                   median_per_book=statistics.median(x["n_orn"] for x in bs),
                   orn_per_100_pages=rnd(100 * sum(x["n_orn"] for x in bp) /
                                         sum(x["pages"] for x in bp), 3) if bp else None,
                   books_with_pages=len(bp))
        for k in KINDS:
            row[k] = sum(x[f"n_{k.lower()}"] for x in bs)
        by_decade.append(row)
    write_csv(a.out / "by_decade.csv", by_decade,
              ["decade", "books", "ornaments", "ornaments_per_book", "median_per_book",
               "orn_per_100_pages", "books_with_pages"] + KINDS)

    def rate(bs):
        bs = [x for x in bs if x["pages"]]
        if not bs:
            return None
        return sum(x["n_orn"] for x in bs) / sum(x["pages"] for x in bs)

    in_range = [b for b in dated if a.year_min <= b["year"] < a.year_max]
    before = [b for b in in_range if b["year"] < a.split_year]
    after = [b for b in in_range if b["year"] >= a.split_year]

    # ---- distribution over books
    s_desc = sorted(per_book, reverse=True)
    top10 = s_desc[:max(1, len(s_desc) // 10)]
    dist = dict(mean=rnd(statistics.mean(per_book)) if per_book else None,
                median=statistics.median(per_book) if per_book else None,
                quantiles=quantiles(per_book), max=max(per_book) if per_book else None,
                share_books_one=rnd(sum(1 for x in per_book if x == 1) / n_books, 4) if n_books else None,
                share_books_le5=rnd(sum(1 for x in per_book if x <= 5) / n_books, 4) if n_books else None,
                share_orn_top10pct_books=rnd(sum(top10) / n_orn, 4) if n_orn else None,
                gini=rnd(gini(per_book), 3), skewness=rnd(skewness(per_book), 2))

    # ---- by format (optional)
    by_format = {}
    if formats:
        fb = defaultdict(list)
        for b in books:
            if b.get("format"):
                fb[b["format"]].append(b)
        for f_, bs in sorted(fb.items(), key=lambda kv: -len(kv[1])):
            r = rate(bs)
            by_format[f_] = dict(books=len(bs), ornaments_per_book=rnd(statistics.mean(
                x["n_orn"] for x in bs)), orn_per_100_pages=rnd(100 * r, 3) if r else None)

    # ---- blocks: human variants (one block each) and machine clusters (one design group each)
    blocks = q(cur, """
        SELECT p.plate, p.src, p.kind, p.n_orn, p.n_books, p.y0, p.y1, p.n_agents,
               p.n_known, p.top_share,
               CASE WHEN p.src LIKE '%%-ann' THEN 'human' ELSE 'machine' END AS family
        FROM mv_plate p""")
    for r in blocks:
        r["lifespan"] = (r["y1"] - r["y0"]) if r["y0"] and r["y1"] else None
    write_csv(a.out / "blocks.csv", blocks, ["plate", "family", "src", "kind", "n_orn", "n_books",
                                             "y0", "y1", "lifespan", "n_agents", "n_known",
                                             "top_share"])

    def block_stats(rows):
        if not rows:
            return {}
        uses = [r["n_orn"] for r in rows]
        nb = [r["n_books"] for r in rows]
        life = [r["lifespan"] for r in rows if r["lifespan"] is not None]
        known = [r for r in rows if r["n_known"]]
        houses = [r["n_agents"] for r in known]
        return dict(count=len(rows), mean_uses=rnd(statistics.mean(uses)),
                    median_uses=statistics.median(uses), mean_books=rnd(statistics.mean(nb)),
                    median_books=statistics.median(nb), max_books=max(nb),
                    median_lifespan=statistics.median(life) if life else None,
                    mean_lifespan=rnd(statistics.mean(life)) if life else None,
                    mean_houses=rnd(statistics.mean(houses)) if houses else None,
                    median_houses=statistics.median(houses) if houses else None,
                    max_houses=max(houses) if houses else None,
                    share_one_house=rnd(sum(1 for h in houses if h == 1) / len(houses), 4)
                    if houses else None)

    hum = [r for r in blocks if r["family"] == "human"]
    mach = [r for r in blocks if r["family"] == "machine"]

    misc = q(cur, """SELECT (SELECT count(*) FROM agent) AS n_agents,
                            (SELECT min(year) FROM book b WHERE year > 1500
                               AND EXISTS (SELECT 1 FROM ornament o WHERE o.book_id = b.book_id)) AS y0,
                            (SELECT max(year) FROM book b WHERE year < 1900
                               AND EXISTS (SELECT 1 FROM ornament o WHERE o.book_id = b.book_id)) AS y1""")[0]

    r_before, r_after = rate(before), rate(after)
    summary = dict(
        _note="Atlas data: ornaments kept by detection and clustering; four types; "
              "illustrations are not in the atlas. Books = ECCO documents with >= 1 ornament.",
        n_ornaments=n_orn, n_books=n_books, n_agents=misc["n_agents"],
        year_min=misc["y0"], year_max=misc["y1"],
        n_books_dated=len(dated), n_books_with_imprint=sum(1 for b in books if b["has_imprint"]),
        n_books_with_pages=len(with_pages), pages_source=pages_src,
        n_human_labelled=sum(b["n_human"] for b in books),
        ecco_docs=a.ecco_docs,
        share_ecco_docs_with_ornament=rnd(n_books / a.ecco_docs, 4) if a.ecco_docs else None,
        by_kind={r["kind"]: r["ornaments"] for r in by_kind},
        per_book=dist,
        orn_per_page_all=rnd(rate(books), 4),
        split_year=a.split_year, year_range=[a.year_min, a.year_max],
        orn_per_page_before=rnd(r_before, 4), orn_per_page_after=rnd(r_after, 4),
        orn_per_book_before=rnd(statistics.mean(b["n_orn"] for b in before), 2) if before else None,
        orn_per_book_after=rnd(statistics.mean(b["n_orn"] for b in after), 2) if after else None,
        by_format=by_format or None,
        blocks_human=block_stats(hum), clusters_machine=block_stats(mach),
    )
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # ---- the paragraph
    bh = summary["blocks_human"] or {}
    v = dict(
        n_ornaments=fmt(n_orn), n_books=fmt(n_books),
        share_ecco=fmt(summary["share_ecco_docs_with_ornament"], "pct"),
        ecco_docs=fmt(a.ecco_docs),
        n_hp=fmt(summary["by_kind"]["HP"]), n_tp=fmt(summary["by_kind"]["TP"]),
        n_di=fmt(summary["by_kind"]["DI"]), n_ft=fmt(summary["by_kind"]["FT"]),
        per_100=fmt(100 * summary["orn_per_page_all"], "dec") if summary["orn_per_page_all"] else None,
        split=a.split_year,
        per_100_before=fmt(100 * r_before, "dec") if r_before else None,
        per_100_after=fmt(100 * r_after, "dec") if r_after else None,
        n_pages_books=fmt(len(with_pages)), pages_source=pages_src,
        median=dist["median"], mean=dist["mean"],
        share_one=fmt(dist["share_books_one"], "pct"),
        top10=fmt(dist["share_orn_top10pct_books"], "pct"), gini=dist["gini"],
        n_blocks=fmt(bh.get("count")), block_books=bh.get("mean_books"),
        block_median_books=bh.get("median_books"), block_life=bh.get("median_lifespan"),
        block_houses=bh.get("mean_houses"), block_one_house=fmt(bh.get("share_one_house"), "pct"),
        n_clusters=fmt((summary["clusters_machine"] or {}).get("count")),
        skew_word=(None if dist["skewness"] is None else
                   "strongly right-skewed" if dist["skewness"] > 1 else
                   "moderately right-skewed" if dist["skewness"] > 0.5 else "close to symmetric"),
    )
    v = Fill({k: x for k, x in v.items() if x is not None})     # None prints as [[name]]
    fmt_sentence = ("[[format sentence: run with --formats to compare folio, quarto, octavo "
                    "and duodecimo]]")
    if by_format:
        parts = [f"{f_} {d['orn_per_100_pages'] if d['orn_per_100_pages'] is not None else d['ornaments_per_book']}"
                 for f_, d in list(by_format.items())[:4]]
        fmt_sentence = ("Ornaments per 100 pages by format: " + ", ".join(parts) +
                        ". [[interpret: do larger formats carry more ornaments?]]")
    para = (
        "The atlas holds {n_ornaments} printers' ornaments from {n_books} ECCO documents; "
        "that is, {share_ecco} of the {ecco_docs} documents in ECCO contain at least one "
        "ornament of the four types we detect. "
        "There are {n_hp} headpieces, {n_tp} tailpieces and printers' devices, {n_di} decorative "
        "initials and {n_ft} factotums. Illustrations are not counted. "
        "On average a book carries {per_100} ornaments per 100 pages (page counts from "
        "{pages_source}, available for {n_pages_books} books). Before {split} the rate is "
        "{per_100_before} per 100 pages and from {split} onwards {per_100_after}, "
        "[[corroborating / contradicting]] findings in the literature. "
        "The distribution is {skew_word}: the median book has {median} ornaments and the mean "
        "{mean}; {share_one} of the books have a single ornament, and the 10% of books with the most "
        "ornaments hold {top10} of all ornaments (Gini {gini}). " + fmt_sentence + "\n\n"
        "The human annotation identifies {n_blocks} blocks (HP variants; DI and FT superclasses). "
        "A block appears on "
        "average in {block_books} books (median {block_median_books}) and is in use for a median of "
        "{block_life} years. A block is named on average with {block_houses} houses in its imprints; "
        "{block_one_house} of the blocks appear with a single house. The machine clusters add "
        "{n_clusters} further design groups, which are not all single blocks.\n"
    )
    out = para.format_map(v)
    missing = sorted(set(re.findall(r"\[\[([^\]]+)\]\]", out)))
    (a.out / "paragraph.md").write_text(
        "<!-- generated by tools/descriptive_stats.py; do not edit by hand, re-run instead -->\n\n"
        + out + ("\n<!-- still missing: " + "; ".join(missing) + " -->\n" if missing else ""))
    print(out)
    print(f"files written to {a.out}/")
    if missing:
        print("still missing:", "; ".join(missing))


if __name__ == "__main__":
    main()
