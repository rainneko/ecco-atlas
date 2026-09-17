"""Database access. Every SQL statement in the project lives here so the
routers stay thin and the query plans are easy to audit in one file."""
from __future__ import annotations

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pgpool

from . import config

_pool: pgpool.ThreadedConnectionPool | None = None


def init_pool(minconn=1, maxconn=8):
    global _pool
    if _pool is None:
        _pool = pgpool.ThreadedConnectionPool(minconn, maxconn, config.DATABASE_URL)


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


class conn_cursor:
    """with conn_cursor() as cur: ...  — dict rows, auto commit/rollback."""

    def __init__(self, commit=False):
        self.commit = commit

    def __enter__(self):
        init_pool()
        self.conn = _pool.getconn()
        self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and self.commit:
                self.conn.commit()
            elif exc_type is not None:
                self.conn.rollback()
        finally:
            self.cur.close()
            _pool.putconn(self.conn)
        return False


def q(sql, params=None, one=False):
    with conn_cursor() as cur:
        cur.execute(sql, params or ())
        return (cur.fetchone() if one else cur.fetchall())


def x(sql, params=None):
    with conn_cursor(commit=True) as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


# ================================================================= stats
def site_stats():
    return q("""
        SELECT (SELECT count(*) FROM ornament)                      AS n_ornaments,
               (SELECT count(*) FROM book)                          AS n_books,
               (SELECT count(*) FROM agent)                         AS n_agents,
               (SELECT count(DISTINCT superclass) FROM ornament)    AS n_superclass,
               (SELECT count(DISTINCT subclass)   FROM ornament)    AS n_subclass,
               (SELECT count(*) FROM ornament WHERE variant <> '')  AS n_with_variant,
               (SELECT min(year) FROM book WHERE year > 1500)       AS year_min,
               (SELECT max(year) FROM book WHERE year < 1900)       AS year_max,
               (SELECT count(*) FROM report WHERE status='open')    AS n_open_reports
    """, one=True)


def kind_counts():
    return q("SELECT kind, count(*) AS n FROM ornament GROUP BY kind ORDER BY kind")


# ================================================================= books
def search_books(term: str, limit=40, offset=0, kind=None, tonson_only=False):
    """Fuzzy title search. Substring match first (cheap, exact-feeling), then
    trigram similarity so 'fable of' also finds 'The Fable of the Bees'."""
    t = (term or "").strip().lower()
    where, params = [], {"t": t, "like": f"%{t}%", "limit": limit, "offset": offset}
    if t:
        where.append("(b.title_norm LIKE %(like)s OR b.title_norm %% %(t)s)")
    if tonson_only:
        where.append("b.is_tonson")
    if kind:
        where.append("EXISTS (SELECT 1 FROM ornament o2 "
                     "WHERE o2.book_id = b.book_id AND o2.kind = %(kind)s)")
        params["kind"] = kind
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = q(f"""
        SELECT b.book_id, b.full_title, b.year, b.estc_id, b.is_tonson, b.total_pages,
               b.publishers_raw, b.printers_raw,
               count(o.oid) AS n_orn,
               CASE WHEN %(t)s = '' THEN 0 ELSE similarity(b.title_norm, %(t)s) END AS sim,
               CASE WHEN %(t)s <> '' AND b.title_norm LIKE %(like)s THEN 1 ELSE 0 END AS exact
        FROM book b LEFT JOIN ornament o ON o.book_id = b.book_id
        {w}
        GROUP BY b.book_id
        ORDER BY exact DESC, sim DESC, n_orn DESC, b.year NULLS LAST
        LIMIT %(limit)s OFFSET %(offset)s
    """, params)
    total = q(f"SELECT count(*) AS n FROM book b {w}", params, one=True)["n"]
    return rows, total


def get_book(book_id):
    return q("SELECT * FROM book WHERE book_id = %s", (book_id,), one=True)


def book_agents(book_id):
    return q("""SELECT a.name, a.display_name, ba.role FROM book_agent ba
                JOIN agent a USING (agent_id) WHERE ba.book_id = %s
                ORDER BY ba.role, a.name""", (book_id,))


def book_strip(book_id):
    """Data for the page strip: one row per kind, one point per (page, kind)."""
    return q("""
        SELECT kind, page, count(*) AS n,
               (array_agg(oid ORDER BY oid))[1] AS first_oid,
               array_agg(oid ORDER BY oid) AS oids
        FROM ornament WHERE book_id = %s AND page IS NOT NULL
        GROUP BY kind, page ORDER BY kind, page
    """, (book_id,))


def book_ornaments(book_id):
    return q("""SELECT oid, kind, page, superclass, subclass, variant, class_path,
                       x1, y1, x2, y2, image_id
                FROM ornament WHERE book_id = %s
                ORDER BY page NULLS LAST, kind""", (book_id,))


def similar_books(book_id, limit=10):
    """idf-weighted overlap of subclass sets, cosine-normalized.

    A book that shares a rare subclass with the target is far stronger evidence
    than one sharing a subclass that appears in 500 books, hence the 1/log(1+df)
    weight. Normalizing by the geometric mean of the two books' weights stops
    long books from dominating the ranking.
    """
    return q("""
        WITH tgt AS (
            SELECT DISTINCT subclass FROM ornament
            WHERE book_id = %(b)s AND subclass IS NOT NULL),
        w AS (SELECT subclass, 1.0 / ln(1 + df) AS wt FROM v_subclass_df),
        tgt_norm AS (SELECT sqrt(sum(wt * wt)) AS n FROM tgt JOIN w USING (subclass)),
        cand AS (
            SELECT o.book_id, sum(w.wt * w.wt) AS dot
            FROM ornament o JOIN tgt USING (subclass) JOIN w ON w.subclass = o.subclass
            WHERE o.book_id <> %(b)s
            GROUP BY o.book_id),
        cand_norm AS (
            SELECT o.book_id, sqrt(sum(w.wt * w.wt)) AS n
            FROM (SELECT DISTINCT book_id, subclass FROM ornament
                  WHERE subclass IS NOT NULL) o
            JOIN w USING (subclass)
            WHERE o.book_id IN (SELECT book_id FROM cand)
            GROUP BY o.book_id)
        SELECT b.book_id, b.full_title, b.year, b.is_tonson, b.publishers_raw,
               c.dot / NULLIF(cn.n * (SELECT n FROM tgt_norm), 0) AS score,
               (SELECT count(*) FROM ornament o3 WHERE o3.book_id = b.book_id) AS n_orn
        FROM cand c JOIN cand_norm cn USING (book_id) JOIN book b USING (book_id)
        ORDER BY score DESC NULLS LAST, n_orn DESC
        LIMIT %(lim)s
    """, {"b": book_id, "lim": limit})


# ================================================================= ornaments
def get_ornament(oid):
    return q("SELECT * FROM v_ornament_full WHERE oid = %s", (oid,), one=True)


def similar_ornaments(oid, limit=3):
    """Phase 1 similarity is rule-based on the annotation hierarchy:
    variant match -> 1.00, subclass match -> 0.98, superclass match -> 0.90.
    These are placeholder numbers agreed with the team. When embeddings land,
    replace this function body with a pgvector `ORDER BY embedding <=> t.embedding`
    and nothing else in the app changes.
    """
    return q("""
        WITH t AS (SELECT superclass, subclass, variant FROM ornament WHERE oid = %(o)s)
        SELECT o.oid, o.kind, o.image_id, o.x1, o.y1, o.x2, o.y2, o.page, o.book_id,
               o.superclass, o.subclass, o.variant, o.class_path, b.full_title, b.year,
               CASE WHEN o.variant <> '' AND o.variant = t.variant AND o.subclass = t.subclass
                         THEN 1.00
                    WHEN o.subclass = t.subclass     THEN 0.98
                    WHEN o.superclass = t.superclass THEN 0.90 END AS score,
               CASE WHEN o.variant <> '' AND o.variant = t.variant AND o.subclass = t.subclass
                         THEN 'same variant'
                    WHEN o.subclass = t.subclass     THEN 'same subclass'
                    ELSE 'same superclass' END AS reason
        FROM ornament o CROSS JOIN t LEFT JOIN book b ON b.book_id = o.book_id
        WHERE o.oid <> %(o)s AND o.superclass = t.superclass
        ORDER BY score DESC, b.year NULLS LAST
        LIMIT %(lim)s
    """, {"o": oid, "lim": limit})


# ================================================================= class pages
LEVEL_COL = {"superclass": "superclass", "subclass": "subclass", "variant": "class_path"}


def class_members(level, value, limit=400, offset=0):
    col = LEVEL_COL[level]
    rows = q(f"""
        SELECT o.oid, o.kind, o.image_id, o.x1, o.y1, o.x2, o.y2, o.page, o.book_id,
               o.superclass, o.subclass, o.variant, o.class_path, o.hc_cluster,
               b.full_title, b.year, b.is_tonson, b.publishers_raw, b.printers_raw
        FROM ornament o LEFT JOIN book b USING (book_id)
        WHERE o.{col} = %(v)s
        ORDER BY b.year NULLS LAST, o.book_id, o.page
        LIMIT %(lim)s OFFSET %(off)s
    """, {"v": value, "lim": limit, "off": offset})
    total = q(f"SELECT count(*) AS n FROM ornament WHERE {col} = %s", (value,), one=True)["n"]
    return rows, total


def class_agent_mix(level, value, role, limit=12):
    """Publisher/printer composition of a class — the 'shareholding' view.
    Counted over distinct books so a thick book cannot skew the pie."""
    col = LEVEL_COL[level]
    return q(f"""
        WITH bks AS (SELECT DISTINCT book_id FROM ornament WHERE {col} = %(v)s),
             tot AS (SELECT count(DISTINCT ba.book_id) AS n FROM book_agent ba
                     JOIN bks USING (book_id) WHERE ba.role = %(r)s)
        SELECT a.name, a.display_name, count(DISTINCT ba.book_id) AS n_books,
               count(DISTINCT ba.book_id)::float / NULLIF((SELECT n FROM tot), 0) AS share
        FROM book_agent ba JOIN bks USING (book_id) JOIN agent a USING (agent_id)
        WHERE ba.role = %(r)s
        GROUP BY a.agent_id ORDER BY n_books DESC LIMIT %(lim)s
    """, {"v": value, "r": role, "lim": limit})


def class_siblings(level, value):
    """What the other branches of the same tree look like."""
    if level == "superclass":
        return q("""SELECT subclass AS value, 'subclass' AS level, count(*) AS n,
                           min(year) AS y0, max(year) AS y1
                    FROM ornament o LEFT JOIN book b USING (book_id)
                    WHERE superclass = %s AND subclass IS NOT NULL
                    GROUP BY subclass ORDER BY subclass""", (value,))
    if level == "subclass":
        return q("""SELECT class_path AS value, 'variant' AS level, count(*) AS n,
                           min(year) AS y0, max(year) AS y1
                    FROM ornament o LEFT JOIN book b USING (book_id)
                    WHERE subclass = %s GROUP BY class_path ORDER BY class_path""", (value,))
    row = q("SELECT subclass FROM ornament WHERE class_path = %s LIMIT 1", (value,), one=True)
    if not row:
        return []
    return q("""SELECT class_path AS value, 'variant' AS level, count(*) AS n,
                       min(year) AS y0, max(year) AS y1
                FROM ornament o LEFT JOIN book b USING (book_id)
                WHERE subclass = %s GROUP BY class_path ORDER BY class_path""",
             (row["subclass"],))


def class_exemplar(level, value):
    col = LEVEL_COL[level]
    return q(f"""SELECT oid, image_id, x1, y1, x2, y2 FROM ornament
                 WHERE {col} = %s ORDER BY (x2-x1)*(y2-y1) DESC LIMIT 1""",
             (value,), one=True)


def browse_classes(level, term="", limit=200):
    col = LEVEL_COL[level]
    like = f"%{(term or '').strip().lower()}%"
    return q(f"""
        SELECT o.{col} AS value, count(*) AS n, count(DISTINCT o.book_id) AS n_books,
               min(b.year) AS y0, max(b.year) AS y1,
               (array_agg(o.kind))[1] AS kind
        FROM ornament o LEFT JOIN book b USING (book_id)
        WHERE o.{col} IS NOT NULL AND (%(t)s = '%%' OR lower(o.{col}) LIKE %(t)s)
        GROUP BY o.{col} ORDER BY n DESC LIMIT %(lim)s
    """, {"t": like, "lim": limit})


# ================================================================= agents
def get_agent(name):
    return q("SELECT * FROM agent WHERE name = %s", (name,), one=True)


def search_agents(term="", limit=100):
    like = f"%{(term or '').strip().lower()}%"
    return q("""
        SELECT a.name, a.display_name, count(DISTINCT ba.book_id) AS n_books,
               count(DISTINCT ba.role) AS n_roles
        FROM agent a LEFT JOIN book_agent ba USING (agent_id)
        WHERE (%(t)s = '%%' OR a.name LIKE %(t)s)
        GROUP BY a.agent_id ORDER BY n_books DESC LIMIT %(lim)s
    """, {"t": like, "lim": limit})


def agent_plates(name, limit=300):
    """Plates (variant level) this agent's books carry, sorted by class id."""
    return q("""
        SELECT o.class_path, o.subclass, o.superclass, o.variant, o.kind,
               count(*) AS n, count(DISTINCT o.book_id) AS n_books,
               min(b.year) AS y0, max(b.year) AS y1,
               (array_agg(o.oid ORDER BY (o.x2-o.x1)*(o.y2-o.y1) DESC))[1] AS exemplar
        FROM ornament o JOIN book b USING (book_id)
        WHERE o.book_id IN (SELECT ba.book_id FROM book_agent ba JOIN agent a USING (agent_id)
                            WHERE a.name = %(n)s)
          AND o.class_path IS NOT NULL
        GROUP BY o.class_path, o.subclass, o.superclass, o.variant, o.kind
        ORDER BY o.superclass, o.subclass, o.variant
        LIMIT %(lim)s
    """, {"n": name, "lim": limit})


def agent_stats(name):
    return q("""
        SELECT count(DISTINCT ba.book_id) AS n_books, min(b.year) AS y0, max(b.year) AS y1,
               count(DISTINCT o.oid) AS n_orn,
               count(DISTINCT o.class_path) AS n_plates,
               bool_or(ba.role = 'publisher') AS as_publisher,
               bool_or(ba.role = 'printer') AS as_printer
        FROM book_agent ba JOIN agent a USING (agent_id) JOIN book b USING (book_id)
        LEFT JOIN ornament o ON o.book_id = ba.book_id
        WHERE a.name = %s
    """, (name,), one=True)


def similar_agents(name, limit=10):
    """Cosine over idf-weighted plate (class_path) sets — 'who used the same
    stock of ornaments'. Shared possession of a rare plate is the strong signal."""
    return q("""
        WITH me AS (
            SELECT DISTINCT o.class_path FROM ornament o
            JOIN book_agent ba ON ba.book_id = o.book_id JOIN agent a USING (agent_id)
            WHERE a.name = %(n)s AND o.class_path IS NOT NULL),
        df AS (
            SELECT o.class_path, count(DISTINCT ba.agent_id) AS d
            FROM ornament o JOIN book_agent ba ON ba.book_id = o.book_id
            WHERE o.class_path IS NOT NULL GROUP BY o.class_path),
        w AS (SELECT class_path, 1.0 / ln(1 + d) AS wt FROM df),
        others AS (
            SELECT DISTINCT a.agent_id, a.name, a.display_name, o.class_path
            FROM ornament o JOIN book_agent ba ON ba.book_id = o.book_id
            JOIN agent a USING (agent_id)
            WHERE o.class_path IS NOT NULL AND a.name <> %(n)s),
        dot AS (
            SELECT o.agent_id, o.name, o.display_name, sum(w.wt * w.wt) AS d
            FROM others o JOIN me USING (class_path) JOIN w ON w.class_path = o.class_path
            GROUP BY o.agent_id, o.name, o.display_name),
        nrm AS (
            SELECT o.agent_id, sqrt(sum(w.wt * w.wt)) AS n FROM others o
            JOIN w ON w.class_path = o.class_path GROUP BY o.agent_id),
        mynrm AS (SELECT sqrt(sum(wt * wt)) AS n FROM me JOIN w USING (class_path))
        SELECT d.name, d.display_name,
               d.d / NULLIF(n.n * (SELECT n FROM mynrm), 0) AS score,
               (SELECT count(DISTINCT ba.book_id) FROM book_agent ba
                WHERE ba.agent_id = d.agent_id) AS n_books
        FROM dot d JOIN nrm n USING (agent_id)
        ORDER BY score DESC NULLS LAST LIMIT %(lim)s
    """, {"n": name, "lim": limit})


# ================================================================= reports
def add_report(oid, reporter, cur_lv, sug_lv, note):
    with conn_cursor(commit=True) as c:
        c.execute("""INSERT INTO report (oid, reporter, cur_superclass, cur_subclass,
                        cur_variant, sug_superclass, sug_subclass, sug_variant, note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING report_id""",
                  (oid, reporter or None, *cur_lv, *sug_lv, note or None))
        return c.fetchone()["report_id"]


def list_reports(status="open", limit=200):
    where = "" if status == "all" else "WHERE r.status = %(s)s"
    return q(f"""
        SELECT r.*, o.kind, o.image_id, o.x1, o.y1, o.x2, o.y2, o.class_path,
               b.full_title, b.year, b.book_id
        FROM report r JOIN ornament o USING (oid) LEFT JOIN book b USING (book_id)
        {where} ORDER BY r.created_at DESC LIMIT %(lim)s
    """, {"s": status, "lim": limit})


def resolve_report(report_id, accept: bool, who: str):
    with conn_cursor(commit=True) as c:
        c.execute("SELECT * FROM report WHERE report_id = %s AND status = 'open'", (report_id,))
        r = c.fetchone()
        if not r:
            return None
        if accept:
            c.execute("SELECT superclass, subclass, variant FROM ornament WHERE oid = %s",
                      (r["oid"],))
            old = c.fetchone()
            new = (r["sug_superclass"] or old["superclass"],
                   r["sug_subclass"] or old["subclass"],
                   r["sug_variant"] if r["sug_variant"] is not None else old["variant"])
            c.execute("""INSERT INTO label_change (oid, report_id, old_superclass, old_subclass,
                            old_variant, new_superclass, new_subclass, new_variant, changed_by)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (r["oid"], report_id, old["superclass"], old["subclass"], old["variant"],
                       *new, who))
            c.execute("""UPDATE ornament SET superclass=%s, subclass=%s, variant=%s,
                            class_path = concat_ws('/', NULLIF(%s,''), NULLIF(%s,''),
                                                        NULLIF(%s,''))
                         WHERE oid = %s""", (*new, *new, r["oid"]))
        c.execute("""UPDATE report SET status=%s, resolved_at=now(), resolved_by=%s
                     WHERE report_id=%s""",
                  ("accepted" if accept else "rejected", who, report_id))
        return r
