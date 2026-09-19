"""Database access. Every SQL statement of the web app lives here, so the
routes stay thin and the query plans can be audited in one file."""
from __future__ import annotations

import threading
import time
import uuid

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pgpool

from . import config
from . import normalize as N

_pool: pgpool.ThreadedConnectionPool | None = None
_sem: threading.BoundedSemaphore | None = None
_lock = threading.Lock()


def init_pool():
    global _pool, _sem
    with _lock:
        if _pool is None:
            _pool = pgpool.ThreadedConnectionPool(1, config.DB_POOL_MAX, config.DATABASE_URL)
            # ThreadedConnectionPool raises when exhausted; wait instead.
            _sem = threading.BoundedSemaphore(config.DB_POOL_MAX)


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


class conn_cursor:
    """with conn_cursor(commit=True) as cur: ...  (dict rows, commit/rollback)."""

    def __init__(self, commit=False):
        self.commit = commit

    def __enter__(self):
        init_pool()
        _sem.acquire()
        try:
            self.conn = _pool.getconn()
        except Exception:
            _sem.release()
            raise
        self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and self.commit:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.cur.close()
            _pool.putconn(self.conn)
            _sem.release()
        return False


def q(sql, params=None, one=False):
    with conn_cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone() if one else cur.fetchall()


def x(sql, params=None):
    with conn_cursor(commit=True) as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


_cache: dict = {}


def cached(ttl):
    def deco(fn):
        def wrap(*a):
            k = (fn.__name__, a)
            hit = _cache.get(k)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            v = fn(*a)
            _cache[k] = (time.time(), v)
            return v
        wrap.__name__ = fn.__name__
        return wrap
    return deco


def clear_cache():
    _cache.clear()


# ================================================================= site
@cached(60)
def sources_available():
    """{src: {n, n_books}} for sources that have data, in config order."""
    rows = {r["src"]: r for r in q("SELECT src, n, n_books FROM mv_source")}
    return {s: rows[s] for s in config.SOURCES if s in rows}


@cached(60)
def class_counts():
    return {(r["src"], r["level"]): r["n"]
            for r in q("SELECT src, level, count(*) AS n FROM mv_class GROUP BY 1, 2")}


@cached(60)
def site_summary():
    return q("""
        SELECT (SELECT count(*) FROM ornament) AS n_ornaments,
               (SELECT count(*) FROM book) AS n_books,
               (SELECT count(*) FROM agent) AS n_agents,
               (SELECT min(year) FROM book WHERE year > 1500) AS year_min,
               (SELECT max(year) FROM book WHERE year < 1900) AS year_max
    """, one=True)


@cached(60)
def kind_counts():
    return q("SELECT kind, count(*) AS n FROM ornament GROUP BY kind ORDER BY kind")


def meta():
    return {r["key"]: r["value"] for r in q("SELECT key, value FROM meta")}


def open_report_count():
    return q("SELECT count(*) AS n FROM report WHERE status = 'open'", one=True)["n"]


# ================================================================= books
BOOK_SORTS = {"title": "b.title_norm", "year": "b.year", "n": "n_orn"}


def _words(term):
    return [w for w in N.norm_title(term).split() if w]


def suggest_words(words):
    """Closest vocabulary word for each query word that no title contains
    (trigram similarity on ~50k short words; DESIGN §4.7)."""
    out = {}
    for w in words:
        r = q("""SELECT word FROM book_word WHERE word %% %(w)s
                 ORDER BY similarity(word, %(w)s) DESC, n DESC LIMIT 1""", {"w": w}, one=True)
        if r and r["word"] != w:
            out[w] = r["word"]
    return out


def search_books(term, limit=30, offset=0, kind=None, tonson_only=False, sort="",
                 desc=True, place=None, _retry=True):
    """Every query word must occur in the title (indexed LIKE); titles that
    contain the words as one phrase rank first. Count comes from the same
    query. Returns (rows, total, corrected_term_or_None)."""
    words = _words(term)
    phrase = " ".join(words)
    where, params = [], {"phrase": f"%{phrase}%", "limit": limit, "offset": offset}
    for i, w in enumerate(words):
        where.append(f"b.title_norm LIKE %(w{i})s")
        params[f"w{i}"] = f"%{w}%"
    if tonson_only:
        where.append("b.is_tonson")
    if kind:
        where.append("EXISTS (SELECT 1 FROM ornament o2 "
                     "WHERE o2.book_id = b.book_id AND o2.kind = %(kind)s)")
        params["kind"] = kind
    if place:
        where.append("b.place_key = %(place)s")
        params["place"] = place
    w_sql = ("WHERE " + " AND ".join(where)) if where else ""
    d = "DESC" if desc else "ASC"
    if sort in BOOK_SORTS:
        order = f"{BOOK_SORTS[sort]} {d} NULLS LAST, b.year NULLS LAST, b.book_id"
    elif words:
        order = "exact DESC, n_orn DESC, b.year NULLS LAST, b.book_id"
    else:
        order = "b.year NULLS LAST, n_orn DESC, b.book_id"
    rows = q(f"""
        SELECT b.book_id, b.full_title, b.year, b.estc_id, b.is_tonson, b.total_pages,
               b.publishers_raw, b.printers_raw, b.place, coalesce(m.n_orn, 0) AS n_orn,
               (b.title_norm LIKE %(phrase)s)::int AS exact,
               count(*) OVER () AS total
        FROM book b LEFT JOIN mv_book m USING (book_id)
        {w_sql}
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
    """, params)
    if rows:
        return rows, rows[0]["total"], None
    if words and _retry and offset == 0:
        fixes = suggest_words(words)
        if fixes:
            fixed = " ".join(fixes.get(w, w) for w in words)
            rows, total, _ = search_books(fixed, limit, 0, kind, tonson_only, sort, desc,
                                          place, _retry=False)
            if rows:
                return rows, total, fixed
    return [], 0, None


def book_agents_many(book_ids):
    """{book_id: [{name, display_name, role}, ...]} for a list page."""
    out = {b: [] for b in book_ids}
    if book_ids:
        for r in q("""SELECT ba.book_id, a.name, a.display_name, ba.role FROM book_agent ba
                      JOIN agent a USING (agent_id) WHERE ba.book_id = ANY(%s)
                      ORDER BY ba.role, a.name""", (list(book_ids),)):
            out[r["book_id"]].append(r)
    return out


def places():
    """Most frequent places with counts; empty when no place data is loaded."""
    return q("""SELECT place_key, min(place) AS place, count(*) AS n FROM book
                WHERE place_key IS NOT NULL GROUP BY place_key ORDER BY n DESC LIMIT 12""")


def get_book(book_id):
    return q("""SELECT b.*, coalesce(m.n_orn, 0) AS n_orn FROM book b
                LEFT JOIN mv_book m USING (book_id) WHERE book_id = %s""", (book_id,), one=True)


def book_agents(book_id):
    return q("""SELECT a.agent_id, a.name, a.display_name, ba.role FROM book_agent ba
                JOIN agent a USING (agent_id) WHERE ba.book_id = %s
                ORDER BY ba.role, a.name""", (book_id,))


def strips(book_ids):
    """Page-strip data for several books in one query:
    {book_id: [{kind, page, n, oid, label}, ...]}"""
    out = {b: [] for b in book_ids}
    if not book_ids:
        return out
    for r in q("""
        SELECT book_id, kind, page, count(*) AS n,
               (array_agg(oid ORDER BY oid))[1] AS oid,
               (array_agg(label ORDER BY oid))[1] AS label
        FROM ornament WHERE book_id = ANY(%s) AND page IS NOT NULL
        GROUP BY book_id, kind, page ORDER BY book_id, kind, page
    """, (list(book_ids),)):
        out[r["book_id"]].append(r)
    return out


def book_ornaments(book_id):
    return q("""SELECT oid, kind, page, label, ann_src, superclass, subclass, variant,
                       class_path, pred_src, hc_cluster, cluster_rejected
                FROM ornament WHERE book_id = %s ORDER BY page NULLS LAST, kind, oid""",
             (book_id,))


def similar_books(book_id, srcs, limit=10):
    """Cosine over idf-weighted plate sets (weight 1/ln(1 + books with the
    plate)); sharing a rare plate counts far more than sharing a common one."""
    return q("""
        WITH tgt AS (SELECT plate FROM mv_book_plate WHERE book_id = %(b)s AND src = ANY(%(s)s)),
        w AS (SELECT p.plate, 1.0 / ln(1 + p.n_books) AS wt FROM mv_plate p JOIN tgt USING (plate)),
        tn AS (SELECT sqrt(sum(wt * wt)) AS n FROM w),
        cand AS (SELECT bp.book_id, sum(w.wt * w.wt) AS dot, count(*) AS shared
                 FROM mv_book_plate bp JOIN w USING (plate)
                 WHERE bp.book_id <> %(b)s GROUP BY bp.book_id),
        cn AS (SELECT bp.book_id, sqrt(sum(power(1.0 / ln(1 + p.n_books), 2))) AS n
               FROM mv_book_plate bp JOIN mv_plate p USING (plate)
               WHERE bp.book_id IN (SELECT book_id FROM cand) AND bp.src = ANY(%(s)s)
               GROUP BY bp.book_id)
        SELECT b.book_id, b.full_title, b.year, b.is_tonson, b.place, c.shared,
               c.dot / NULLIF(cn.n * (SELECT n FROM tn), 0) AS score,
               coalesce(m.n_orn, 0) AS n_orn,
               (b.place_key IS NOT NULL AND t.place_key IS NOT NULL
                AND b.place_key <> t.place_key) AS diff_place
        FROM cand c JOIN cn USING (book_id) JOIN book b USING (book_id)
        LEFT JOIN mv_book m USING (book_id)
        CROSS JOIN (SELECT place_key FROM book WHERE book_id = %(b)s) t
        ORDER BY score DESC NULLS LAST, c.shared DESC, n_orn DESC
        LIMIT %(lim)s
    """, {"b": book_id, "s": list(srcs), "lim": limit})


# ================================================================= ornaments
def resolve_alias(oid):
    r = q("SELECT oid FROM ornament_alias WHERE alias_oid = %s", (oid,), one=True)
    return r["oid"] if r else None


def get_ornament(oid):
    return q("SELECT * FROM v_ornament_full WHERE oid = %s", (oid,), one=True)


def get_ornament_min(oid):
    """What the image routes need, nothing else."""
    return q("""SELECT oid, image_id, x1, y1, x2, y2, ann_src FROM ornament
                WHERE oid = %s""", (oid,), one=True)


def names_for(src, pairs):
    """{(level, value): {name, alt_name}} for a list of (level, value)."""
    if not pairs:
        return {}
    rows = q("""SELECT level, value, name, alt_name FROM class_name
                WHERE src = %s AND (level, value) IN %s""", (src, tuple(pairs)))
    return {(r["level"], r["value"]): r for r in rows}


def similar_ornaments(o, method, limit=3):
    """Phase 1: rule-based. Hierarchy for human-labelled ornaments, cluster
    otherwise. Embedding cosine (Phase 2) will replace the body of this
    function only."""
    S = config.SIM_SCORES
    if method == "hier" and o["ann_src"]:
        return q("""
            SELECT o.oid, o.label, o.kind, o.book_id, b.year, b.full_title,
                   CASE WHEN o.variant <> '' AND o.variant = %(var)s AND o.subclass = %(sub)s
                             THEN %(sv)s
                        WHEN o.subclass = %(sub)s THEN %(ss)s ELSE %(sp)s END AS score,
                   CASE WHEN o.variant <> '' AND o.variant = %(var)s AND o.subclass = %(sub)s
                             THEN 'same variant'
                        WHEN o.subclass = %(sub)s THEN 'same subclass'
                        ELSE 'same superclass' END AS reason
            FROM ornament o LEFT JOIN book b USING (book_id)
            WHERE o.ann_src = %(src)s AND o.superclass = %(sup)s AND o.oid <> %(oid)s
              AND o.class_path IS NOT NULL
            ORDER BY score DESC, b.year NULLS LAST, o.oid LIMIT %(lim)s
        """, dict(src=o["ann_src"], sup=o["superclass"], sub=o["subclass"] or "",
                  var=o["variant"] or "", oid=o["oid"], lim=limit, sv=S["variant"],
                  ss=S["subclass"], sp=S["superclass"]))
    if method == "cluster" and o["hc_cluster"] and not o["cluster_rejected"]:
        return q("""
            SELECT o.oid, o.label, o.kind, o.book_id, b.year, b.full_title,
                   %(sc)s AS score, 'same cluster' AS reason
            FROM ornament o LEFT JOIN book b USING (book_id)
            WHERE o.pred_src = %(src)s AND o.hc_cluster = %(c)s AND NOT o.cluster_rejected
              AND o.oid <> %(oid)s
            ORDER BY b.year NULLS LAST, o.oid LIMIT %(lim)s
        """, dict(src=o["pred_src"], c=o["hc_cluster"], oid=o["oid"], lim=limit,
                  sc=S["cluster"]))
    return []


def embeddings_available():
    r = q("SELECT to_regclass('ornament_embedding') IS NOT NULL AS ok", one=True)
    return bool(r and r["ok"])


# ================================================================= classes
def class_cond(src, level, alias="o"):
    """SQL condition selecting the members of (src, level, :v)."""
    a = alias
    if level == "cluster":
        return (f"{a}.pred_src = %(src)s AND {a}.hc_cluster = %(v)s "
                f"AND NOT {a}.cluster_rejected")
    col = {"superclass": "superclass", "subclass": "subclass", "variant": "class_path"}[level]
    return f"{a}.ann_src = %(src)s AND {a}.{col} = %(v)s AND {a}.class_path IS NOT NULL"


def class_row(src, level, value):
    r = q("""SELECT c.*, p.t_owned, p.top_agents, p.top_share, p.n_known AS plate_known
             FROM mv_class c LEFT JOIN mv_plate p ON p.plate = c.plate_key
             WHERE c.src = %s AND c.level = %s AND c.value = %s""",
          (src, level, value), one=True)
    if r:
        return r
    # not yet in the summary tables (e.g. created in the workbench a moment ago)
    return q(f"""SELECT %(src)s AS src, %(lvl)s AS level, %(v)s AS value,
                        min(o.kind) AS kind, count(*) AS n, count(DISTINCT o.book_id) AS n_books,
                        min(b.year) AS y0, max(b.year) AS y1,
                        (array_agg(o.oid))[1] AS exemplar, NULL AS plate_key,
                        NULL::bool AS t_owned, NULL AS top_agents
                 FROM ornament o LEFT JOIN book b USING (book_id)
                 WHERE {class_cond(src, level)} HAVING count(*) > 0""",
             {"src": src, "lvl": level, "v": value}, one=True)


GROUP_COL = {"superclass": "o.subclass", "subclass": "o.variant"}


def class_members(src, level, value, order="year", limit=120, offset=0):
    grp = GROUP_COL.get(level) if order == "group" else None
    order_sql = (f"{grp} NULLS LAST, " if grp else "") + "b.year NULLS LAST, o.book_id, o.page"
    rows = q(f"""
        SELECT o.oid, o.kind, o.page, o.book_id, o.label, o.ann_src, o.superclass,
               o.subclass, o.variant, o.class_path, o.pred_src, o.hc_cluster,
               b.full_title, b.year, b.is_tonson,
               {grp or 'NULL'} AS grp
        FROM ornament o LEFT JOIN book b USING (book_id)
        WHERE {class_cond(src, level)}
        ORDER BY {order_sql}
        LIMIT %(lim)s OFFSET %(off)s
    """, {"src": src, "v": value, "lim": limit, "off": offset})
    return rows


def class_book_agents(src, level, value):
    """(book, agent, role) rows for every book of the class — enough to compute
    composition, ownership and lending in Python."""
    return q(f"""
        WITH bks AS (SELECT DISTINCT o.book_id FROM ornament o WHERE {class_cond(src, level)})
        SELECT bks.book_id, b.year, b.full_title, b.has_imprint, b.place, b.place_key,
               b.false_imprint, ba.agent_id, ba.role, a.name, a.display_name
        FROM bks JOIN book b USING (book_id)
        LEFT JOIN book_agent ba USING (book_id) LEFT JOIN agent a USING (agent_id)
    """, {"src": src, "v": value})


def mv_classes(src, level, values):
    if not values:
        return []
    return q("""SELECT c.*, p.t_owned FROM mv_class c LEFT JOIN mv_plate p ON p.plate = c.plate_key
                WHERE c.src = %s AND c.level = %s AND c.value = ANY(%s)""",
             (src, level, list(values)))


def class_structure(src, level, value, sup, sub):
    """Children / siblings for the 'structure' section of a class page."""
    out = {"children": [], "siblings": [], "side": []}
    if level == "cluster":
        return out
    if level == "superclass":
        subs = [r["subclass"] for r in q(
            "SELECT DISTINCT subclass FROM ornament WHERE ann_src = %s AND superclass = %s "
            "AND subclass IS NOT NULL AND class_path LIKE '%%/%%'", (src, value))]
        out["children"] = sorted(mv_classes(src, "subclass", subs),
                                 key=lambda r: N.natural_key(r["value"]))
        return out
    variants = q("""SELECT c.*, p.t_owned FROM mv_class c
                    LEFT JOIN mv_plate p ON p.plate = c.plate_key
                    WHERE c.src = %s AND c.level = 'variant' AND c.value LIKE %s""",
                 (src, f"{sup}/{sub}/%"))
    variants.sort(key=lambda r: N.natural_key(r["value"]))
    if level == "subclass":
        out["children"] = variants
    else:
        out["siblings"] = variants
    others = [r["subclass"] for r in q(
        "SELECT DISTINCT subclass FROM ornament WHERE ann_src = %s AND superclass = %s "
        "AND subclass IS NOT NULL AND subclass <> %s", (src, sup, sub))]
    out["side"] = sorted(mv_classes(src, "subclass", others),
                         key=lambda r: N.natural_key(r["value"]))
    return out


def class_crossref(src, level, value):
    """Cluster vs human label for the images of one class."""
    p = {"src": src, "v": value}
    if level == "cluster":
        return q(f"""SELECT o.ann_src AS src, o.label, o.class_path, count(*) AS n
                     FROM ornament o WHERE {class_cond(src, level)} AND o.ann_src IS NOT NULL
                       AND o.class_path IS NOT NULL
                     GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 12""", p)
    return q(f"""SELECT o.pred_src AS src, o.hc_cluster AS value, o.kind, count(*) AS n
                 FROM ornament o WHERE {class_cond(src, level)} AND o.hc_cluster IS NOT NULL
                   AND NOT o.cluster_rejected
                 GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 12""", p)


SORTS = {"name": None, "n": "c.n", "books": "c.n_books", "first": "c.y0", "last": "c.y1",
         "span": "(c.y1 - c.y0)"}


def browse_classes(src, level, term="", sort="n", desc=True, limit=100, offset=0):
    like = f"%{(term or '').strip().lower()}%"
    d = "DESC" if desc else "ASC"
    if sort == "name" or sort not in SORTS:
        order = (f"(c.value ~ '^[0-9]+$') {d}, length(c.value) {d}, c.value {d}"
                 if level == "cluster" else f"c.value {d}")
    else:
        order = f"{SORTS[sort]} {d} NULLS LAST, c.value"
    params = {"src": src, "lvl": level, "t": like, "lim": limit, "off": offset}
    where = """c.src = %(src)s AND c.level = %(lvl)s
               AND (%(t)s = '%%' OR lower(c.value) LIKE %(t)s OR lower(cn.name) LIKE %(t)s
                    OR lower(cn.alt_name) LIKE %(t)s OR lower(cs.name) LIKE %(t)s)"""
    joins = """LEFT JOIN class_name cn ON cn.src = c.src AND cn.level = c.level
                                       AND cn.value = c.value
               LEFT JOIN class_name cs ON cs.src = c.src AND cs.level = 'superclass'
                    AND c.level IN ('subclass', 'variant')
                    AND cs.value = CASE WHEN c.level = 'variant' THEN split_part(c.value, '/', 1)
                                        ELSE regexp_replace(c.value, '_[0-9]+$', '') END"""
    rows = q(f"""SELECT c.*, cn.name, cn.alt_name, cs.name AS parent_name, p.t_owned,
                        (c.y1 - c.y0) AS span
                 FROM mv_class c {joins} LEFT JOIN mv_plate p ON p.plate = c.plate_key
                 WHERE {where} ORDER BY {order} LIMIT %(lim)s OFFSET %(off)s""", params)
    total = q(f"SELECT count(*) AS n FROM mv_class c {joins} WHERE {where}", params, one=True)["n"]
    return rows, total


def class_exemplar(src, level, value):
    r = q("SELECT exemplar FROM mv_class WHERE src = %s AND level = %s AND value = %s",
          (src, level, value), one=True)
    if r:
        return r["exemplar"]
    r = q(f"SELECT o.oid FROM ornament o WHERE {class_cond(src, level)} LIMIT 1",
          {"src": src, "v": value}, one=True)
    return r["oid"] if r else None


def class_samples(src, level, value, n=3):
    return q(f"""SELECT o.oid, o.label, b.year FROM ornament o LEFT JOIN book b USING (book_id)
                 WHERE {class_cond(src, level)} ORDER BY b.year NULLS LAST, o.oid
                 LIMIT %(n)s""", {"src": src, "v": value, "n": n})


def class_suggestions(src, sup=None, limit=400):
    """Existing labels for the report form's datalist."""
    if sup:
        rows = q("""SELECT DISTINCT label FROM ornament WHERE ann_src = %s AND superclass = %s
                    AND label IS NOT NULL ORDER BY 1 LIMIT %s""", (src, sup, limit))
    else:
        rows = q("""SELECT DISTINCT label FROM ornament WHERE ann_src = %s
                    AND label IS NOT NULL ORDER BY 1 LIMIT %s""", (src, limit))
    return [r["label"] for r in rows]


# ================================================================= agents
def get_agent(name):
    return q("SELECT * FROM agent WHERE name = %s", (name,), one=True)


def agents_by_id(ids):
    if not ids:
        return {}
    return {r["agent_id"]: r for r in q(
        "SELECT agent_id, name, display_name FROM agent WHERE agent_id = ANY(%s)", (list(ids),))}


AGENT_SORTS = {"books": "n_books", "plates": "n_plates", "owned": "n_owned", "name": "a.name"}


def agents_list(term, srcs, sort="books", desc=True, limit=200):
    like = f"%{(term or '').strip().lower()}%"
    order = AGENT_SORTS.get(sort, "n_books")
    d = "DESC" if desc else "ASC"
    return q(f"""
        SELECT a.agent_id, a.name, a.display_name, coalesce(b.n_books, 0) AS n_books,
               coalesce(s.n_plates, 0) AS n_plates, coalesce(s.n_owned, 0) AS n_owned,
               b.roles
        FROM agent a
        JOIN (SELECT agent_id, count(DISTINCT book_id) AS n_books,
                     string_agg(DISTINCT role, ',') AS roles
              FROM book_agent GROUP BY agent_id) b USING (agent_id)
        LEFT JOIN (SELECT agent_id, sum(n_plates) AS n_plates, sum(n_owned) AS n_owned
                   FROM mv_agent_src WHERE src = ANY(%(s)s) GROUP BY agent_id) s USING (agent_id)
        WHERE (%(t)s = '%%' OR a.name LIKE %(t)s OR lower(a.display_name) LIKE %(t)s)
        ORDER BY {order} {d} NULLS LAST, a.name LIMIT %(lim)s
    """, {"t": like, "s": list(srcs), "lim": limit})


def agent_stats(agent_id, srcs):
    return q("""
        SELECT count(DISTINCT ba.book_id) AS n_books, min(b.year) AS y0, max(b.year) AS y1,
               bool_or(ba.role = 'publisher') AS as_publisher,
               bool_or(ba.role = 'printer') AS as_printer,
               (SELECT count(*) FROM ornament o JOIN book_agent ba2 USING (book_id)
                WHERE ba2.agent_id = %(a)s AND o.src = ANY(%(s)s)) AS n_orn
        FROM book_agent ba JOIN book b USING (book_id) WHERE ba.agent_id = %(a)s
    """, {"a": agent_id, "s": list(srcs)}, one=True)


def agent_plates(agent_id, srcs, owned=False, joint=False, min_books=None):
    mb = min_books or config.OWNER_MIN_BOOKS
    cond = ""
    if owned:
        cond = (" AND pa.n_known >= %(mb)s AND " +
                ("pa.share >= pa.other_max" if joint else "pa.share > pa.other_max"))
    rows = q(f"""
        SELECT pa.plate, pa.src, pa.n_books AS agent_books, pa.n_known, pa.share, pa.other_max,
               pa.y0 AS agent_y0, pa.y1 AS agent_y1, p.y0, p.y1, p.exemplar, p.kind, p.n_orn,
               p.t_owned, p.n_books,
               (pa.share > pa.other_max) AS sole
        FROM mv_plate_agent pa JOIN mv_plate p USING (plate)
        WHERE pa.agent_id = %(a)s AND pa.src = ANY(%(s)s) {cond}
    """, {"a": agent_id, "s": list(srcs), "mb": mb})
    order = list(config.SOURCES)
    rows.sort(key=lambda r: (order.index(r["src"]) if r["src"] in order else 99,
                             N.natural_key(r["plate"])))
    return rows


def similar_agents(agent_id, srcs, limit=10):
    """Cosine over idf-weighted plate sets, weight 1/ln(1 + houses using the
    plate) — 'who drew on the same stock of blocks'."""
    return q("""
        WITH mine AS (SELECT plate FROM mv_plate_agent WHERE agent_id = %(a)s AND src = ANY(%(s)s)),
        w AS (SELECT p.plate, 1.0 / ln(1 + p.n_agents) AS wt FROM mv_plate p JOIN mine USING (plate)),
        mn AS (SELECT sqrt(sum(wt * wt)) AS n FROM w),
        dot AS (SELECT pa.agent_id, sum(w.wt * w.wt) AS d, count(*) AS shared
                FROM mv_plate_agent pa JOIN w USING (plate)
                WHERE pa.agent_id <> %(a)s GROUP BY pa.agent_id),
        nrm AS (SELECT pa.agent_id, sqrt(sum(power(1.0 / ln(1 + p.n_agents), 2))) AS n
                FROM mv_plate_agent pa JOIN mv_plate p USING (plate)
                WHERE pa.agent_id IN (SELECT agent_id FROM dot) AND pa.src = ANY(%(s)s)
                GROUP BY pa.agent_id)
        SELECT a.agent_id, a.name, a.display_name, d.shared,
               d.d / NULLIF(nrm.n * (SELECT n FROM mn), 0) AS score
        FROM dot d JOIN nrm USING (agent_id) JOIN agent a USING (agent_id)
        ORDER BY score DESC NULLS LAST, d.shared DESC LIMIT %(lim)s
    """, {"a": agent_id, "s": list(srcs), "lim": limit})


def coholders(agent_id, srcs, min_share=None, min_books=None, limit=10):
    """Σ over plates both hold (coverage >= min_share) of min(coverage_A, coverage_B)."""
    return q("""
        WITH mine AS (SELECT plate, share FROM mv_plate_agent
                      WHERE agent_id = %(a)s AND src = ANY(%(s)s)
                        AND share >= %(ms)s AND n_known >= %(mb)s)
        SELECT a.agent_id, a.name, a.display_name,
               sum(least(pa.share, mine.share)) AS score, count(*) AS n_plates
        FROM mv_plate_agent pa JOIN mine USING (plate) JOIN agent a USING (agent_id)
        WHERE pa.agent_id <> %(a)s AND pa.share >= %(ms)s
        GROUP BY a.agent_id, a.name, a.display_name
        ORDER BY score DESC, n_plates DESC LIMIT %(lim)s
    """, {"a": agent_id, "s": list(srcs), "ms": min_share or config.COHOLD_MIN_SHARE,
          "mb": min_books or config.OWNER_MIN_BOOKS, "lim": limit})


def pair_shared_plates(a, b, srcs):
    rows = q("""
        SELECT pa.plate, pa.src, pa.share AS share_a, pb.share AS share_b,
               least(pa.share, pb.share) AS co, pa.n_known, p.exemplar, p.kind,
               p.n_books, p.t_owned, p.y0, p.y1
        FROM mv_plate_agent pa JOIN mv_plate_agent pb USING (plate) JOIN mv_plate p USING (plate)
        WHERE pa.agent_id = %(a)s AND pb.agent_id = %(b)s AND pa.src = ANY(%(s)s)
        ORDER BY co DESC, p.n_books DESC
    """, {"a": a, "b": b, "s": list(srcs)})
    return rows


def pair_joint(a, b, srcs, limit=120, offset=0):
    params = {"a": a, "b": b, "s": list(srcs), "lim": limit, "off": offset}
    joint = """(SELECT book_id FROM book_agent WHERE agent_id = %(a)s
                INTERSECT SELECT book_id FROM book_agent WHERE agent_id = %(b)s)"""
    rows = q(f"""SELECT o.oid, o.kind, o.label, o.page, o.book_id, o.plate,
                        b.full_title, b.year, b.is_tonson
                 FROM ornament o JOIN book b USING (book_id)
                 WHERE o.book_id IN {joint} AND o.src = ANY(%(s)s)
                 ORDER BY b.year NULLS LAST, o.book_id, o.page
                 LIMIT %(lim)s OFFSET %(off)s""", params)
    tot = q(f"""SELECT count(*) AS n, (SELECT count(*) FROM {joint} j) AS n_books
                FROM ornament o WHERE o.book_id IN {joint} AND o.src = ANY(%(s)s)""",
            params, one=True)
    return rows, tot["n"], tot["n_books"]


# ================================================================= lending
def lending_plates(srcs, owner, lo, hi, min_books, plate=None, agent_id=None):
    cond, params = [], {"s": list(srcs), "own": owner, "lo": lo, "hi": hi, "mb": min_books}
    if plate:
        cond.append("p.plate = %(plate)s")
        params["plate"] = plate
    if agent_id:
        cond.append("EXISTS (SELECT 1 FROM mv_plate_agent x WHERE x.plate = p.plate "
                    "AND x.agent_id = %(ag)s)")
        params["ag"] = agent_id
    return [r["plate"] for r in q(f"""
        SELECT p.plate FROM mv_plate p
        WHERE p.src = ANY(%(s)s) AND p.n_known >= %(mb)s AND p.top_share >= %(own)s
          AND EXISTS (SELECT 1 FROM mv_plate_agent pa WHERE pa.plate = p.plate
                      AND pa.share BETWEEN %(lo)s AND %(hi)s)
          {''.join(' AND ' + c for c in cond)}
    """, params)]


def plate_book_agents(plates):
    if not plates:
        return []
    return q("""SELECT bp.plate, bp.book_id, b.year, b.full_title, ba.agent_id
                FROM mv_book_plate bp JOIN book b USING (book_id)
                LEFT JOIN book_agent ba USING (book_id)
                WHERE bp.plate = ANY(%s)""", (list(plates),))


def plate_info(plates):
    if not plates:
        return {}
    return {r["plate"]: r for r in q(
        "SELECT plate, src, kind, exemplar, n_books, n_known, y0, y1, t_owned FROM mv_plate "
        "WHERE plate = ANY(%s)", (list(plates),))}


def lending_reviews(plates):
    if not plates:
        return {}
    return {(r["plate"], r["owner_ids"], r["borrower_id"], r["book_id"]): r for r in q(
        "SELECT * FROM lending_review WHERE plate = ANY(%s)", (list(plates),))}


def set_lending_review(plate, owner_ids, borrower_id, book_id, status, who, note=None):
    if status == "clear":
        return x("""DELETE FROM lending_review WHERE plate = %s AND owner_ids = %s
                    AND borrower_id = %s AND book_id = %s""",
                 (plate, owner_ids, borrower_id, book_id))
    return x("""INSERT INTO lending_review (plate, owner_ids, borrower_id, book_id, status,
                                            note, reviewed_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (plate, owner_ids, borrower_id, book_id) DO UPDATE SET
                    status = EXCLUDED.status, note = EXCLUDED.note,
                    reviewed_by = EXCLUDED.reviewed_by, reviewed_at = now()""",
             (plate, owner_ids, borrower_id, book_id, status, note, who))


# ================================================================= crops
def get_crop_store(oid, thumb):
    col = "thumb_jpeg" if thumb else "full_jpeg"
    r = q(f"SELECT {col} AS data FROM crop_store WHERE oid = %s", (oid,), one=True)
    return bytes(r["data"]) if r else None


def put_crop_store(oid, full, thumb, width):
    x("""INSERT INTO crop_store (oid, full_jpeg, thumb_jpeg, width) VALUES (%s, %s, %s, %s)
         ON CONFLICT (oid) DO UPDATE SET full_jpeg = EXCLUDED.full_jpeg,
             thumb_jpeg = EXCLUDED.thumb_jpeg, width = EXCLUDED.width, fetched_at = now()""",
      (oid, psycopg2.Binary(full), psycopg2.Binary(thumb), width))


def crop_store_stats():
    return q("""SELECT (SELECT count(*) FROM crop_store) AS stored,
                       (SELECT count(*) FROM ornament WHERE ann_src IS NOT NULL) AS annotated,
                       (SELECT pg_size_pretty(pg_total_relation_size('crop_store'))) AS size""",
             one=True)


# ================================================================= human changes
def apply_changes(changes, who, batch_id=None, report_id=None):
    """changes: [{oid, action, ann_src, superclass, subclass, variant, rejected}]
    — the full *new* human state of each ornament. Writes label_change first,
    then the ornament, in one transaction (the history can never lag behind)."""
    if not changes:
        return 0
    batch_id = batch_id or uuid.uuid4().hex[:12]
    with conn_cursor(commit=True) as c:
        c.execute("""SELECT oid, ann_src, superclass, subclass, variant, cluster_rejected
                     FROM ornament WHERE oid = ANY(%s) FOR UPDATE""",
                  ([ch["oid"] for ch in changes],))
        old = {r["oid"]: r for r in c.fetchall()}
        n = 0
        for ch in changes:
            o = old.get(ch["oid"])
            if not o:
                continue
            s = ch.get("ann_src")
            path = N.class_path_for(s, ch.get("superclass"), ch.get("subclass"),
                                    ch.get("variant") or "") if s else None
            if not path:
                s, ch = None, dict(ch, superclass=None, subclass=None, variant="")
            rej = bool(ch.get("rejected", o["cluster_rejected"]))
            c.execute("""INSERT INTO label_change (oid, action, report_id, batch_id,
                             old_ann_src, old_superclass, old_subclass, old_variant,
                             new_ann_src, new_superclass, new_subclass, new_variant,
                             old_cluster_rejected, new_cluster_rejected, changed_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (ch["oid"], ch["action"], report_id, batch_id,
                       o["ann_src"], o["superclass"], o["subclass"], o["variant"],
                       s, ch.get("superclass"), ch.get("subclass"), ch.get("variant") or "",
                       o["cluster_rejected"], rej, who))
            c.execute("""UPDATE ornament SET ann_src = %s, superclass = %s, subclass = %s,
                             variant = %s, class_path = %s, cluster_rejected = %s,
                             ann_round = CASE WHEN %s IS NULL THEN NULL
                                              ELSE coalesce(ann_round, 'site') END
                         WHERE oid = %s""",
                      (s, ch.get("superclass"), ch.get("subclass"), ch.get("variant") or "",
                       path, rej, s, ch["oid"]))
            n += 1
    return n


def recent_changes(limit=30):
    return q("""SELECT lc.*, o.label FROM label_change lc LEFT JOIN ornament o USING (oid)
                ORDER BY lc.changed_at DESC, lc.change_id DESC LIMIT %s""", (limit,))


# ================================================================= reports
def add_report(oid, reporter, cur_label, cur_src, suggestion, note):
    with conn_cursor(commit=True) as c:
        c.execute("""INSERT INTO report (oid, reporter, cur_label, cur_src, suggestion, note)
                     VALUES (%s, %s, %s, %s, %s, %s) RETURNING report_id""",
                  (oid, reporter or None, cur_label, cur_src, suggestion or None, note or None))
        return c.fetchone()["report_id"]


def list_reports(status="open", limit=200):
    where = "" if status == "all" else "WHERE r.status = %(s)s"
    return q(f"""
        SELECT r.*, o.kind, o.label, o.ann_src, o.superclass, o.subclass, o.variant,
               o.class_path, o.pred_src, o.hc_cluster, b.full_title, b.year, b.book_id
        FROM report r JOIN ornament o USING (oid) LEFT JOIN book b USING (book_id)
        {where} ORDER BY r.created_at DESC LIMIT %(lim)s
    """, {"s": status, "lim": limit})


def get_report(report_id):
    return q("SELECT * FROM report WHERE report_id = %s", (report_id,), one=True)


def close_report(report_id, status, who, resolution=None):
    return x("""UPDATE report SET status = %s, resolved_at = now(), resolved_by = %s,
                                  resolution = %s
                WHERE report_id = %s AND status = 'open'""",
             (status, who, resolution, report_id))


# ================================================================= workbench
def workbench_members(src, level, value):
    return q(f"""
        SELECT o.oid, o.kind, o.page, o.book_id, o.label, o.ann_src, o.superclass,
               o.subclass, o.variant, o.class_path, o.pred_src, o.hc_cluster,
               b.full_title, b.year, b.is_tonson
        FROM ornament o LEFT JOIN book b USING (book_id)
        WHERE {class_cond(src, level)}
        ORDER BY b.year NULLS LAST, o.book_id, o.page, o.oid
    """, {"src": src, "v": value})


def existing_subclasses(src, sup):
    return [r["subclass"] for r in q(
        "SELECT DISTINCT subclass FROM ornament WHERE ann_src = %s AND superclass = %s "
        "AND subclass IS NOT NULL", (src, sup))]


# ================================================================= image search (§9)
def embedding_model():
    return q("SELECT name, dim, input_sizes, n, loaded_at FROM embedding_model "
             "ORDER BY loaded_at DESC LIMIT 1", one=True)


def model_row(name=None):
    if name:
        return q("SELECT * FROM model_store WHERE name = %s", (name,), one=True)
    return q("SELECT * FROM model_store ORDER BY loaded_at DESC LIMIT 1", one=True)


def model_rows():
    return q("SELECT name, arch, dim, n_bytes, source_file, loaded_at FROM model_store "
             "ORDER BY loaded_at DESC")


def kind_samples():
    """{kind: ([vec bytes], [oid])}"""
    out = {}
    for r in q("""SELECT s.kind, s.oid, e.vec FROM kind_sample s JOIN ornament_embedding e
                  USING (oid) ORDER BY s.kind, s.oid"""):
        vecs, oids = out.setdefault(r["kind"], ([], []))
        vecs.append(r["vec"]); oids.append(r["oid"])
    return out


def centroids_by_kind():
    """{kind: [row(src, value, level, n, vec, plate, n_books, y0, y1, exemplar,
                 t_owned)]} — one row per centroid, joined to its own class row."""
    out = {}
    fam = ",".join(f"('{s}','{v['family']}')" for s, v in config.SOURCES.items())
    for r in q(f"""
        WITH c AS (
          SELECT c.*, s.family,
                 CASE WHEN s.family = 'pred' THEN 'cluster'
                      WHEN array_length(string_to_array(c.value, '/'), 1) >= 3 THEN 'variant'
                      WHEN array_length(string_to_array(c.value, '/'), 1) = 2 THEN 'subclass'
                      ELSE 'superclass' END AS level
          FROM cluster_centroid c JOIN (VALUES {fam}) s(src, family) ON s.src = c.src)
        SELECT c.src, c.value, c.kind, c.n, c.vec, c.level, c.src || ':' || c.value AS plate,
               m.n_books, m.y0, m.y1, m.exemplar, p.t_owned
        FROM c
        LEFT JOIN mv_class m ON m.src = c.src AND m.level = c.level
             AND m.value = CASE c.level WHEN 'variant' THEN c.value
                                        WHEN 'subclass' THEN split_part(c.value, '/', 2)
                                        WHEN 'superclass' THEN split_part(c.value, '/', 1)
                                        ELSE c.value END
        LEFT JOIN mv_plate p ON p.plate = c.src || ':' || c.value
    """):
        out.setdefault(r["kind"], []).append(r)
    return out


def all_embeddings(kind):
    rows = q("""SELECT e.oid, e.vec FROM ornament_embedding e JOIN ornament o USING (oid)
                WHERE o.kind = %s""", (kind,))
    return [r["oid"] for r in rows], [r["vec"] for r in rows]


def members_with_vectors(keys):
    """{(src, value): [{oid, vec}]} for machine clusters and human plates."""
    out = {k: [] for k in keys}
    if not keys:
        return out
    for src, value in keys:
        if config.SOURCES[src]["family"] == "pred":
            rows = q("""SELECT o.oid, e.vec FROM ornament o JOIN ornament_embedding e USING (oid)
                        WHERE o.pred_src = %s AND o.hc_cluster = %s AND NOT o.cluster_rejected
                        ORDER BY o.oid LIMIT 400""", (src, value))
        else:
            rows = q("""SELECT o.oid, e.vec FROM ornament o JOIN ornament_embedding e USING (oid)
                        WHERE o.ann_src = %s AND o.class_path = %s ORDER BY o.oid LIMIT 400""",
                     (src, value))
        out[(src, value)] = rows
    return out


def plate_agent_shares(plates):
    """{plate: [{agent_id, name, display_name, role, coverage}]}"""
    out = {p: [] for p in plates}
    if not plates:
        return out
    for r in q("""SELECT pa.plate, a.agent_id, a.name, a.display_name, ba.role,
                         pa.share AS coverage
                  FROM mv_plate_agent pa JOIN agent a USING (agent_id)
                  JOIN LATERAL (SELECT role FROM book_agent x JOIN mv_book_plate bp USING (book_id)
                                WHERE x.agent_id = pa.agent_id AND bp.plate = pa.plate
                                GROUP BY role ORDER BY count(*) DESC LIMIT 1) ba ON TRUE
                  WHERE pa.plate = ANY(%s)""", (list(plates),)):
        out[r["plate"]].append(r)
    return out


def ornaments_brief(oids):
    """{oid: {label, kind, book_id, year, full_title, page}} for result lists."""
    if not oids:
        return {}
    return {r["oid"]: r for r in q(
        """SELECT o.oid, o.label, o.kind, o.book_id, o.page, o.ann_src, o.pred_src, o.hc_cluster,
                  o.class_path, o.superclass, o.subclass, o.variant, o.cluster_rejected,
                  b.year, b.full_title, b.is_tonson
           FROM ornament o LEFT JOIN book b USING (book_id) WHERE o.oid = ANY(%s)""",
        (list(oids),))}


# ================================================================= places, pairs, links (§12)
def design_place_rows(src, level, value):
    """(design, book_id, place_key, place) for the designs one level below
    (src, level, value): subclasses of a superclass, variants of a subclass."""
    if level == "superclass":
        grp, cond = "o.subclass", "o.ann_src = %(src)s AND o.superclass = %(v)s AND o.subclass IS NOT NULL"
    elif level == "subclass":
        grp, cond = "o.class_path", ("o.ann_src = %(src)s AND o.subclass = %(v)s AND o.variant <> ''"
                                     " AND o.class_path IS NOT NULL")
    else:
        return []
    return q(f"""SELECT DISTINCT {grp} AS design, o.book_id, b.place_key, b.place
                 FROM ornament o JOIN book b USING (book_id) WHERE {cond}""",
             {"src": src, "v": value})


def cluster_group_place_rows(src, values):
    if not values:
        return []
    return q("""SELECT DISTINCT o.hc_cluster AS design, o.book_id, b.place_key, b.place
                FROM ornament o JOIN book b USING (book_id)
                WHERE o.pred_src = %s AND o.hc_cluster = ANY(%s) AND NOT o.cluster_rejected""",
             (src, list(values)))


def cluster_links(src, value, limit=12):
    """Related clusters of (src, value) by stored centroid similarity."""
    return q("""
        SELECT l.sim, l.rejected, CASE WHEN l.a = %(v)s THEN l.b ELSE l.a END AS other,
               m.n, m.n_books, m.y0, m.y1, m.exemplar, m.kind
        FROM cluster_link l
        LEFT JOIN mv_class m ON m.src = l.src AND m.level = 'cluster'
             AND m.value = CASE WHEN l.a = %(v)s THEN l.b ELSE l.a END
        WHERE l.src = %(src)s AND (l.a = %(v)s OR l.b = %(v)s)
        ORDER BY l.rejected, l.sim DESC LIMIT %(lim)s""", {"src": src, "v": value, "lim": limit})


def cluster_group(src, value, cap=None):
    """Connected component of accepted links around (src, value), capped."""
    cap = cap or config.LINK_MAX_GROUP
    seen, frontier = {value}, [value]
    while frontier and len(seen) < cap:
        rows = q("""SELECT CASE WHEN a = ANY(%(f)s) THEN b ELSE a END AS other
                    FROM cluster_link WHERE src = %(src)s AND NOT rejected
                      AND (a = ANY(%(f)s) OR b = ANY(%(f)s))""", {"src": src, "f": frontier})
        frontier = [r["other"] for r in rows if r["other"] not in seen]
        seen.update(frontier)
    return sorted(seen, key=N.natural_key)


def set_link_rejected(src, a, b, rejected):
    return x("UPDATE cluster_link SET rejected = %s WHERE src = %s AND ((a = %s AND b = %s) "
             "OR (a = %s AND b = %s))", (rejected, src, a, b, b, a))


def link_stats():
    return q("SELECT src, count(*) AS n, count(*) FILTER (WHERE rejected) AS n_rej, "
             "max(computed_at) AS at FROM cluster_link GROUP BY src ORDER BY src")


def book_pairs(diff_only=True, limit=100, offset=0):
    where = "WHERE p.diff_place" if diff_only else ""
    rows = q(f"""
        SELECT p.*, ba.full_title AS title_a, bb.full_title AS title_b, ba.place AS pl_a,
               bb.place AS pl_b, ba.false_imprint AS fi_a, bb.false_imprint AS fi_b,
               count(*) OVER () AS total
        FROM mv_book_pair p JOIN book ba ON ba.book_id = p.a JOIN book bb ON bb.book_id = p.b
        {where} ORDER BY p.diff_place DESC, p.shared DESC, p.jaccard DESC, p.a, p.b
        LIMIT %s OFFSET %s""", (limit, offset))
    return rows, (rows[0]["total"] if rows else 0)


def pair_stats():
    return q("""SELECT count(*) AS n, count(*) FILTER (WHERE diff_place) AS n_diff,
                       count(*) FILTER (WHERE place_a IS NOT NULL AND place_b IS NOT NULL) AS n_placed
                FROM mv_book_pair""", one=True)


def shared_plates(pairs, per_pair=6):
    """{(a, b): [plate rows]} for the pairs on one page."""
    out = {}
    for p in pairs:
        out[(p["a"], p["b"])] = q("""
            SELECT x.plate, m.exemplar, m.kind FROM mv_book_plate x JOIN mv_book_plate y USING (plate)
            JOIN mv_plate m USING (plate)
            WHERE x.book_id = %s AND y.book_id = %s ORDER BY m.n_books, x.plate LIMIT %s""",
            (p["a"], p["b"], per_pair))
    return out


def book_pairs_for(book_id, limit=10):
    return q("""SELECT p.*, CASE WHEN p.a = %(b)s THEN p.b ELSE p.a END AS other
                FROM mv_book_pair p WHERE p.a = %(b)s OR p.b = %(b)s
                ORDER BY p.diff_place DESC, p.shared DESC LIMIT %(lim)s""",
             {"b": book_id, "lim": limit})
