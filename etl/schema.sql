-- ECCO Ornament Atlas — schema
-- Phase 1: annotation tables (DI / FT / HP / WE).
-- Phase 2 hooks are marked [FUTURE] and are already present so no migration is needed later.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- [FUTURE] enable when the pgvector-enabled Postgres image is deployed:
-- CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------- books
CREATE TABLE IF NOT EXISTS book (
    book_id     TEXT PRIMARY KEY,          -- 10-digit ECCO documentID, leading zeros kept
    estc_id     TEXT,
    full_title  TEXT,
    title_norm  TEXT,                      -- lowercased, punctuation stripped -> fuzzy search
    year        INTEGER,
    total_pages INTEGER,
    is_tonson   BOOLEAN NOT NULL DEFAULT FALSE,
    publishers_raw TEXT,                   -- original imprint string, kept for display
    printers_raw   TEXT
);
CREATE INDEX IF NOT EXISTS book_title_trgm  ON book USING gin (title_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS book_year_idx    ON book (year);

-- ---------------------------------------------------------------- agents
CREATE TABLE IF NOT EXISTS agent (
    agent_id     SERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,     -- normalized key, e.g. "tonson"
    display_name TEXT                      -- most frequent surface form, e.g. "Tonson, J."
);
CREATE INDEX IF NOT EXISTS agent_name_trgm ON agent USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS book_agent (
    book_id  TEXT NOT NULL REFERENCES book(book_id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agent(agent_id) ON DELETE CASCADE,
    role     TEXT NOT NULL CHECK (role IN ('publisher', 'printer')),
    PRIMARY KEY (book_id, agent_id, role)
);
CREATE INDEX IF NOT EXISTS book_agent_agent_idx ON book_agent (agent_id);

-- ---------------------------------------------------------------- ornaments
-- PRIMARY KEY is the pair (image_id, box).  oid is a stable hash of that pair,
-- used as a short URL-safe handle.  box_key is the rounded box string that went
-- into the hash, so the natural key stays visible and queryable.
CREATE TABLE IF NOT EXISTS ornament (
    oid         TEXT PRIMARY KEY,
    image_id    TEXT NOT NULL,             -- 15 digits, no .TIF  == docId + page + "0"
    box_key     TEXT NOT NULL,
    x1 DOUBLE PRECISION, y1 DOUBLE PRECISION,
    x2 DOUBLE PRECISION, y2 DOUBLE PRECISION,
    kind        TEXT NOT NULL,             -- DI | FT | HP | WE
    book_id     TEXT REFERENCES book(book_id) ON DELETE CASCADE,
    page        INTEGER,
    superclass  TEXT,
    subclass    TEXT,
    variant     TEXT NOT NULL DEFAULT '',  -- '' when the class has no variant level
    class_path  TEXT,                      -- "superclass/subclass/variant" for display + grouping
    label_src   TEXT,                      -- which CSV / annotation round this came from
    hc_cluster  TEXT,                      -- [FUTURE] machine cluster id (HC* columns)
    hc_column   TEXT,                      -- which HC threshold column it came from
    -- [FUTURE] embedding vector(512),
    UNIQUE (image_id, box_key)
);
CREATE INDEX IF NOT EXISTS orn_book_page_idx ON ornament (book_id, page);
CREATE INDEX IF NOT EXISTS orn_superclass_idx ON ornament (superclass);
CREATE INDEX IF NOT EXISTS orn_subclass_idx   ON ornament (subclass);
CREATE INDEX IF NOT EXISTS orn_variant_idx    ON ornament (subclass, variant);
CREATE INDEX IF NOT EXISTS orn_kind_idx       ON ornament (kind);
CREATE INDEX IF NOT EXISTS orn_image_idx      ON ornament (image_id);

-- ---------------------------------------------------------------- corrections
CREATE TABLE IF NOT EXISTS report (
    report_id   SERIAL PRIMARY KEY,
    oid         TEXT NOT NULL REFERENCES ornament(oid) ON DELETE CASCADE,
    reporter    TEXT,                      -- free-text name/email, optional
    cur_superclass TEXT, cur_subclass TEXT, cur_variant TEXT,
    sug_superclass TEXT, sug_subclass TEXT, sug_variant TEXT,
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'open'   -- open | accepted | rejected
                CHECK (status IN ('open', 'accepted', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS report_status_idx ON report (status, created_at DESC);

-- Every accepted correction is written here before ornament is updated, so the
-- annotation history is never lost and the CSVs can be regenerated.
CREATE TABLE IF NOT EXISTS label_change (
    change_id   SERIAL PRIMARY KEY,
    oid         TEXT NOT NULL,
    report_id   INTEGER REFERENCES report(report_id),
    old_superclass TEXT, old_subclass TEXT, old_variant TEXT,
    new_superclass TEXT, new_subclass TEXT, new_variant TEXT,
    changed_by  TEXT,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- [FUTURE] uploads
CREATE TABLE IF NOT EXISTS upload (
    upload_id   TEXT PRIMARY KEY,
    filename    TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL DEFAULT 'pending',
    -- [FUTURE] embedding vector(512),
    note        TEXT
);

-- ---------------------------------------------------------------- helper views
CREATE OR REPLACE VIEW v_ornament_full AS
SELECT o.*, b.full_title, b.year, b.estc_id, b.is_tonson,
       b.publishers_raw, b.printers_raw, b.total_pages
FROM ornament o LEFT JOIN book b USING (book_id);

-- document frequency of each subclass, used for idf-weighted similarity
CREATE OR REPLACE VIEW v_subclass_df AS
SELECT subclass, COUNT(DISTINCT book_id) AS df
FROM ornament WHERE subclass IS NOT NULL GROUP BY subclass;
