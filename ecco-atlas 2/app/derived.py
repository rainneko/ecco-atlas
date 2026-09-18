"""Derived summary tables (materialized views).

Built by the loader after every load and refreshed in the background after
admin edits. Pages that *count* read these; pages that *edit* read the base
tables, so an edit is visible immediately even while a refresh is running.
"""
from __future__ import annotations

import logging
import threading
import time

import psycopg2

from . import config

log = logging.getLogger("ecco.derived")
LOCK_KEY = 7240011
ORDER = ["mv_book", "mv_book_plate", "mv_plate_agent", "mv_plate",
         "mv_agent_src", "mv_class", "mv_source"]


def _in(values):
    return "(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def build_sql() -> str:
    sub_srcs = [s for s, v in config.SOURCES.items()
                if v["family"] == "ann" and "subclass" in v["levels"]] or ["-"]
    var_srcs = [s for s, v in config.SOURCES.items()
                if v["family"] == "ann" and "variant" in v["levels"]] or ["-"]
    mb = int(config.OWNER_MIN_BOOKS)
    return f"""
DROP MATERIALIZED VIEW IF EXISTS {", ".join(reversed(ORDER))} CASCADE;

CREATE MATERIALIZED VIEW mv_book AS
SELECT b.book_id, count(o.oid) AS n_orn,
       count(o.oid) FILTER (WHERE o.ann_src IS NOT NULL) AS n_ann
FROM book b LEFT JOIN ornament o USING (book_id) GROUP BY b.book_id;
CREATE UNIQUE INDEX mv_book_pk ON mv_book (book_id);
CREATE INDEX mv_book_n ON mv_book (n_orn DESC);

CREATE MATERIALIZED VIEW mv_book_plate AS
SELECT DISTINCT book_id, plate, src FROM ornament
WHERE plate IS NOT NULL AND book_id IS NOT NULL;
CREATE UNIQUE INDEX mv_book_plate_pk ON mv_book_plate (book_id, plate);
CREATE INDEX mv_book_plate_plate ON mv_book_plate (plate);

-- coverage of every house on every plate; other_max = best share of anyone else
CREATE MATERIALIZED VIEW mv_plate_agent AS
WITH known AS (
    SELECT bp.plate, count(*) AS n_known
    FROM mv_book_plate bp JOIN book b USING (book_id)
    WHERE b.has_imprint GROUP BY bp.plate),
pa AS (
    SELECT bp.plate, bp.src, ba.agent_id, count(DISTINCT bp.book_id) AS n_books,
           min(b.year) AS y0, max(b.year) AS y1,
           bool_or(ba.role = 'publisher') AS as_pub, bool_or(ba.role = 'printer') AS as_prn
    FROM mv_book_plate bp JOIN book_agent ba USING (book_id) JOIN book b USING (book_id)
    GROUP BY bp.plate, bp.src, ba.agent_id),
s AS (
    SELECT pa.*, k.n_known, pa.n_books::float / k.n_known AS share
    FROM pa JOIN known k USING (plate))
SELECT s.*,
       coalesce(max(s.share) OVER (PARTITION BY s.plate
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            EXCLUDE CURRENT ROW), 0) AS other_max
FROM s;
CREATE UNIQUE INDEX mv_plate_agent_pk ON mv_plate_agent (plate, agent_id);
CREATE INDEX mv_plate_agent_agent ON mv_plate_agent (agent_id, src);

CREATE MATERIALIZED VIEW mv_plate AS
WITH o AS (
    SELECT plate, src, min(kind) AS kind, count(*) AS n_orn,
           count(DISTINCT book_id) AS n_books,
           (array_agg(oid ORDER BY (x2 - x1) * (y2 - y1) DESC))
               [greatest(1, (count(*) + 3) / 4)] AS exemplar
    FROM ornament WHERE plate IS NOT NULL GROUP BY plate, src),
yr AS (
    SELECT bp.plate, min(b.year) AS y0, max(b.year) AS y1
    FROM mv_book_plate bp JOIN book b USING (book_id) GROUP BY bp.plate),
ag AS (
    SELECT plate, max(n_known) AS n_known, count(*) AS n_agents, max(share) AS top_share,
           array_agg(agent_id ORDER BY share DESC, agent_id)
               FILTER (WHERE share >= other_max) AS top_agents,
           bool_or(share > other_max) AS has_sole
    FROM mv_plate_agent GROUP BY plate)
SELECT o.*, yr.y0, yr.y1,
       coalesce(ag.n_known, 0) AS n_known, coalesce(ag.n_agents, 0) AS n_agents,
       coalesce(ag.top_share, 0) AS top_share, ag.top_agents,
       coalesce(ag.has_sole, false) AS has_sole,
       coalesce(ag.n_known, 0) >= {mb} AND EXISTS (
           SELECT 1 FROM agent a WHERE a.agent_id = ANY (ag.top_agents)
           AND a.name IN {_in(config.TONSON_KEYS)}) AS t_owned
FROM o LEFT JOIN yr USING (plate) LEFT JOIN ag USING (plate);
CREATE UNIQUE INDEX mv_plate_pk ON mv_plate (plate);

CREATE MATERIALIZED VIEW mv_agent_src AS
SELECT agent_id, src, count(*) AS n_plates,
       count(*) FILTER (WHERE share > other_max AND n_known >= {mb}) AS n_owned
FROM mv_plate_agent GROUP BY agent_id, src;
CREATE UNIQUE INDEX mv_agent_src_pk ON mv_agent_src (agent_id, src);

-- one row per browsable class: (source, level, value)
CREATE MATERIALIZED VIEW mv_class AS
WITH base AS (
    SELECT o.oid, o.kind, o.book_id, b.year, (o.x2 - o.x1) * (o.y2 - o.y1) AS area,
           o.ann_src, o.superclass, o.subclass, o.variant, o.class_path,
           o.pred_src, o.hc_cluster, o.cluster_rejected
    FROM ornament o LEFT JOIN book b USING (book_id)),
r AS (
    SELECT ann_src AS src, 'superclass'::text AS level, superclass AS value,
           oid, kind, book_id, year, area, class_path
      FROM base WHERE ann_src IS NOT NULL AND class_path IS NOT NULL
                  AND superclass IS NOT NULL
    UNION ALL
    SELECT ann_src, 'subclass', subclass, oid, kind, book_id, year, area, class_path
      FROM base WHERE ann_src IN {_in(sub_srcs)} AND class_path IS NOT NULL
                  AND subclass IS NOT NULL
    UNION ALL
    SELECT ann_src, 'variant', class_path, oid, kind, book_id, year, area, class_path
      FROM base WHERE ann_src IN {_in(var_srcs)} AND variant <> ''
                  AND class_path IS NOT NULL
    UNION ALL
    SELECT pred_src, 'cluster', hc_cluster, oid, kind, book_id, year, area, NULL
      FROM base WHERE pred_src IS NOT NULL AND hc_cluster IS NOT NULL
                  AND NOT cluster_rejected)
SELECT src, level, value, min(kind) AS kind, count(*) AS n,
       count(DISTINCT book_id) AS n_books, min(year) AS y0, max(year) AS y1,
       (array_agg(oid ORDER BY area DESC))[greatest(1, (count(*) + 3) / 4)] AS exemplar,
       CASE WHEN level = 'cluster' THEN src || ':' || value
            WHEN count(DISTINCT class_path) = 1 THEN src || ':' || min(class_path)
       END AS plate_key
FROM r GROUP BY src, level, value;
CREATE UNIQUE INDEX mv_class_pk ON mv_class (src, level, value);
CREATE INDEX mv_class_plate ON mv_class (plate_key);

CREATE MATERIALIZED VIEW mv_source AS
SELECT ann_src AS src, count(*) AS n, count(DISTINCT book_id) AS n_books
FROM ornament WHERE ann_src IS NOT NULL GROUP BY ann_src
UNION ALL
SELECT pred_src, count(*), count(DISTINCT book_id)
FROM ornament WHERE pred_src IS NOT NULL GROUP BY pred_src;
CREATE UNIQUE INDEX mv_source_pk ON mv_source (src);
"""


def rebuild(conn):
    """Drop and create every derived view (used by the loader)."""
    t = time.time()
    with conn.cursor() as c:
        c.execute(build_sql())
        c.execute("""INSERT INTO meta VALUES ('derived_at', now()::text)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""")
    conn.commit()
    log.info("derived views rebuilt in %.1fs", time.time() - t)
    return time.time() - t


def _set_meta(cur, key, value):
    cur.execute("""INSERT INTO meta VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, value))


def refresh_blocking(dsn=None):
    """Refresh all views concurrently (readers are never blocked). Only one
    process refreshes at a time; a request that arrives meanwhile sets a dirty
    flag and the running refresher does one more pass."""
    conn = psycopg2.connect(dsn or config.DATABASE_URL)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        if not cur.fetchone()[0]:
            _set_meta(cur, "derived_dirty", "1")
            return False
        try:
            _set_meta(cur, "derived_refreshing", "1")
            for _ in range(5):
                _set_meta(cur, "derived_dirty", "0")
                t = time.time()
                for mv in ORDER:
                    cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
                log.info("derived views refreshed in %.1fs", time.time() - t)
                cur.execute("SELECT value FROM meta WHERE key = 'derived_dirty'")
                row = cur.fetchone()
                if not row or row[0] != "1":
                    break
            _set_meta(cur, "derived_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        finally:
            _set_meta(cur, "derived_refreshing", "0")
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        return True
    finally:
        conn.close()


def refresh_async():
    def run():
        try:
            refresh_blocking()
        except Exception as e:  # noqa: BLE001 — a failed refresh must not kill the app
            log.warning("derived refresh failed: %s", e)
    threading.Thread(target=run, daemon=True, name="derived-refresh").start()
