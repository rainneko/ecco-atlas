-- ECCO Ornament Atlas — schema v2.  See DESIGN.md §3.8.
-- Derived tables (mv_*) are defined in app/derived.py and rebuilt after each load.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- [FUTURE] with the pgvector-enabled Postgres image:  CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- ---------------------------------------------------------------- books
CREATE TABLE IF NOT EXISTS book (
    book_id     TEXT PRIMARY KEY,          -- 10-digit ECCO documentID, leading zeros kept
    estc_id     TEXT,
    full_title  TEXT,
    title_norm  TEXT,
    place       TEXT,                      -- as given (v2.1, §12.1)
    place_key   TEXT,                      -- normalised city key
    year        INTEGER,
    total_pages INTEGER,
    is_tonson   BOOLEAN NOT NULL DEFAULT FALSE,
    has_imprint BOOLEAN NOT NULL DEFAULT FALSE,   -- at least one parsed agent
    publishers_raw TEXT,
    printers_raw   TEXT
);
-- v2.0 -> v2.1 upgrade without a reset (DESIGN §10)
ALTER TABLE book ADD COLUMN IF NOT EXISTS place TEXT,
                 ADD COLUMN IF NOT EXISTS place_key TEXT;
CREATE INDEX IF NOT EXISTS book_title_trgm ON book USING gin (title_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS book_place_idx  ON book (place_key) WHERE place_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS book_year_idx   ON book (year);
CREATE INDEX IF NOT EXISTS book_estc_idx   ON book (estc_id);

-- ---------------------------------------------------------------- agents
CREATE TABLE IF NOT EXISTS agent (
    agent_id     SERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,     -- surname key, e.g. "tonson"
    display_name TEXT
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
-- Natural key (image_id, box_key); oid is its short hash. One row carries a
-- machine label (pred_*) and/or a human label (ann_*). src/plate/label are
-- derived so every query agrees on them (DESIGN §3.5).
CREATE TABLE IF NOT EXISTS ornament (
    oid         TEXT PRIMARY KEY,
    image_id    TEXT NOT NULL,
    box_key     TEXT NOT NULL,
    x1 DOUBLE PRECISION, y1 DOUBLE PRECISION, x2 DOUBLE PRECISION, y2 DOUBLE PRECISION,
    kind        TEXT NOT NULL,             -- DI | FT | HP | WE
    book_id     TEXT REFERENCES book(book_id) ON DELETE CASCADE,
    page        INTEGER,
    -- machine label
    pred_src    TEXT,                      -- HP-pred | DI-pred | FT-pred | WE-pred
    hc_column   TEXT,                      -- e.g. HC0.12
    hc_cluster  TEXT,
    cluster_rejected BOOLEAN NOT NULL DEFAULT FALSE,
    -- human label
    ann_src     TEXT,                      -- HP-ann | DI-ann | FT-ann
    superclass  TEXT,
    subclass    TEXT,
    variant     TEXT NOT NULL DEFAULT '',
    class_path  TEXT,                      -- levels the source exposes, '/'-joined
    ann_round   TEXT,                      -- HP_concat "source" column, or file name
    -- derived
    src   TEXT GENERATED ALWAYS AS (
            CASE WHEN ann_src IS NOT NULL AND class_path IS NOT NULL THEN ann_src
                 WHEN pred_src IS NOT NULL AND hc_cluster IS NOT NULL
                      AND NOT cluster_rejected THEN pred_src END) STORED,
    plate TEXT GENERATED ALWAYS AS (
            CASE WHEN ann_src IS NOT NULL AND class_path IS NOT NULL
                      THEN ann_src || ':' || class_path
                 WHEN pred_src IS NOT NULL AND hc_cluster IS NOT NULL
                      AND NOT cluster_rejected THEN pred_src || ':' || hc_cluster END) STORED,
    label TEXT GENERATED ALWAYS AS (
            CASE WHEN ann_src IS NOT NULL AND class_path IS NOT NULL THEN
                      CASE WHEN position('/' in class_path) > 0
                           THEN subclass || variant ELSE superclass END
                 WHEN hc_cluster IS NOT NULL AND NOT cluster_rejected
                      THEN kind || '-' || hc_cluster END) STORED,
    -- [FUTURE] embedding vector(512),
    UNIQUE (image_id, box_key)
);
CREATE INDEX IF NOT EXISTS orn_book_page_idx ON ornament (book_id, page);
CREATE INDEX IF NOT EXISTS orn_image_idx     ON ornament (image_id);
CREATE INDEX IF NOT EXISTS orn_kind_idx      ON ornament (kind);
CREATE INDEX IF NOT EXISTS orn_ann_sup_idx   ON ornament (ann_src, superclass);
CREATE INDEX IF NOT EXISTS orn_ann_sub_idx   ON ornament (ann_src, subclass);
CREATE INDEX IF NOT EXISTS orn_ann_path_idx  ON ornament (ann_src, class_path);
CREATE INDEX IF NOT EXISTS orn_pred_idx      ON ornament (pred_src, hc_cluster);
CREATE INDEX IF NOT EXISTS orn_plate_idx     ON ornament (plate);

-- annotation keys that were matched to an existing detection by IoU
CREATE TABLE IF NOT EXISTS ornament_alias (
    alias_oid TEXT PRIMARY KEY,
    oid       TEXT NOT NULL REFERENCES ornament(oid) ON DELETE CASCADE
);

-- formal names (HP Group / Subgroup, previous annotation names)
CREATE TABLE IF NOT EXISTS class_name (
    src      TEXT NOT NULL,
    level    TEXT NOT NULL,
    value    TEXT NOT NULL,
    name     TEXT,
    alt_name TEXT,
    PRIMARY KEY (src, level, value)
);

-- annotated crops live in the database (DESIGN §6)
CREATE TABLE IF NOT EXISTS crop_store (
    oid        TEXT PRIMARY KEY REFERENCES ornament(oid) ON DELETE CASCADE,
    full_jpeg  BYTEA NOT NULL,             -- native crop, at most 1400 px wide
    thumb_jpeg BYTEA NOT NULL,             -- 300 px wide
    width      INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- review
CREATE TABLE IF NOT EXISTS report (
    report_id   SERIAL PRIMARY KEY,
    oid         TEXT NOT NULL REFERENCES ornament(oid) ON DELETE CASCADE,
    reporter    TEXT,
    cur_label   TEXT,                      -- what the site showed, e.g. C067_01a or DI-8944
    cur_src     TEXT,
    suggestion  TEXT,                      -- free text, parsed on accept
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'accepted', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution  TEXT                       -- the label actually applied
);
CREATE INDEX IF NOT EXISTS report_status_idx ON report (status, created_at DESC);

-- Every human change (report accept, workbench move, drop, cluster rejection)
-- is written here before the ornament is updated; the loader replays the
-- latest change per ornament after every data reload.
CREATE TABLE IF NOT EXISTS label_change (
    change_id   SERIAL PRIMARY KEY,
    oid         TEXT NOT NULL,
    action      TEXT NOT NULL,             -- relabel | drop | reject_cluster
    report_id   INTEGER,
    batch_id    TEXT,
    old_ann_src TEXT, old_superclass TEXT, old_subclass TEXT, old_variant TEXT,
    new_ann_src TEXT, new_superclass TEXT, new_subclass TEXT, new_variant TEXT,
    old_cluster_rejected BOOLEAN, new_cluster_rejected BOOLEAN,
    changed_by  TEXT,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS label_change_oid_idx ON label_change (oid, changed_at DESC);

CREATE TABLE IF NOT EXISTS lending_review (
    plate       TEXT NOT NULL,
    owner_ids   TEXT NOT NULL,             -- sorted agent ids joined by ','
    borrower_id INTEGER NOT NULL,
    book_id     TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('confirmed', 'rejected')),
    note        TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plate, owner_ids, borrower_id, book_id)
);

-- ---------------------------------------------------------------- [FUTURE] uploads
CREATE TABLE IF NOT EXISTS upload (
    upload_id   TEXT PRIMARY KEY,
    filename    TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL DEFAULT 'pending',
    note        TEXT
);

CREATE OR REPLACE VIEW v_ornament_full AS
SELECT o.*, b.full_title, b.year, b.estc_id, b.is_tonson,
       b.publishers_raw, b.printers_raw, b.total_pages
FROM ornament o LEFT JOIN book b USING (book_id);

INSERT INTO meta (key, value) VALUES ('schema_version', '2')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- ------------------------------------------------------------------ v2.1: image search (DESIGN §9)
CREATE TABLE IF NOT EXISTS model_store (
    name        TEXT PRIMARY KEY,
    arch        TEXT NOT NULL,              -- vit_s | resnet18 | resnet50
    dim         INTEGER NOT NULL,
    state       BYTEA NOT NULL,             -- torch.save of the backbone state dict
    sha256      TEXT,
    n_bytes     BIGINT,
    source_file TEXT,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS embedding_model (
    name        TEXT PRIMARY KEY,           -- one row: the model the vectors came from
    dim         INTEGER NOT NULL,
    input_sizes JSONB,                      -- {"HP": {"size": [100,400], "crop": "square"}, ...}
    n           INTEGER,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ornament_embedding (
    oid   TEXT PRIMARY KEY REFERENCES ornament(oid) ON DELETE CASCADE,
    vec   BYTEA NOT NULL                    -- float16, little-endian, L2-normalised
);
CREATE TABLE IF NOT EXISTS cluster_centroid (
    src    TEXT NOT NULL,                   -- HP-pred (cluster) or HP-ann (plate)
    value  TEXT NOT NULL,                   -- hc_cluster, or class_path of the plate
    kind   TEXT NOT NULL,
    n      INTEGER NOT NULL,
    vec    BYTEA NOT NULL,
    PRIMARY KEY (src, value)
);
CREATE INDEX IF NOT EXISTS cluster_centroid_kind ON cluster_centroid (kind);
CREATE TABLE IF NOT EXISTS kind_sample (
    oid   TEXT PRIMARY KEY REFERENCES ornament(oid) ON DELETE CASCADE,
    kind  TEXT NOT NULL
);
-- §12.6: stored similarity between machine clusters of one source
CREATE TABLE IF NOT EXISTS cluster_link (
    src   TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL,
    sim   REAL NOT NULL, n_a INTEGER, n_b INTEGER,
    rejected BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (src, a, b)
);
CREATE INDEX IF NOT EXISTS cluster_link_b ON cluster_link (src, b);
-- §12.1: places of publication (loaded with --places)
ALTER TABLE book ADD COLUMN IF NOT EXISTS country TEXT,
                 ADD COLUMN IF NOT EXISTS false_imprint BOOLEAN,
                 ADD COLUMN IF NOT EXISTS imprint_place TEXT,
                 ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION,
                 ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION;
