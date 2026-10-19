# ECCO Ornament Atlas

A research site for printers' ornaments in Eighteenth Century Collections
Online: which ornaments appear in which books, how human classes and machine
clusters relate, under whose imprints each block appears, and which books
carry a block under an unusual imprint.

The full specification is **[DESIGN.md](DESIGN.md)** (v2.6). What changed and how
to deploy it: **[CHANGES_v2.6.md](CHANGES_v2.6.md)** and
**[CHANGES_v2.5.md](CHANGES_v2.5.md)**.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

createdb ecco                                   # any Postgres 14+
export DATABASE_URL="postgresql://localhost/ecco"
export ADMIN_PASSWORD=letmein SECRET_KEY=dev-secret

python -m etl.load --reset \
    --src HP-pred data/HP.csv    --src DI-pred data/DI.csv \
    --src FT-pred data/FT.csv    --src TP-pred data/TPPD.csv \
    --src HP-ann  data/HP_concat_annotation.csv \
    --src DI-ann  data/DI_annotation.csv
python -m etl.fetch_crops                        # optional: store annotated crops
python -m etl.load --places data/places.csv      # optional: ESTC places of publication
python -m etl.load_model --ckpt model.ckpt --name moco_vit_s      # optional: image search (§9)
python -m etl.load_embeddings --model moco_vit_s data/emb_*.npz  # from tools/export_embeddings.py
python -m etl.link_clusters --min-sim 0.85       # related machine clusters (§12.6)

uvicorn app.main:app --reload --port 8000
```

The site works without the last three steps: image search and embedding
similarity then show a sentence instead (DESIGN §9.0).

No real data at hand? `python -m tests.make_fixtures /tmp/fx` writes all seven
CSVs in their real layouts, with planted test cases.

## Updating the data

Re-run `python -m etl.load --src <SOURCE> <file.csv>` for the files that changed
(add `--replace` when a new model run should replace a source's clusters); the
loader upserts on image id + box and re-applies every admin correction. A
cluster rejection is re-applied only while the ornament is still in the
rejected cluster. The summary views are rebuilt at the end; the site waits
during that time, so run loads outside working hours.

## The image server

Page images come from a colleague's server. The atlas limits itself per web
worker (`IMAGE_MAX_PARALLEL`, default 3 at a time; `IMAGE_MAX_RPS`, default 3
per second), keeps every 300 px thumbnail in Postgres, and pauses after a 429
or 5xx answer. Run bulk jobs at night with `IMAGE_MAX_RPS=1`.

## Tests

```bash
python -m tests.test_normalize          # parsing rules
python -m tests.test_glossary           # every ? lands on its /about entry
./smoke.sh http://localhost:8000        # every route; admin flows too if ADMIN_PASSWORD is set
```

## Layout

```
app/config.py      sources, thresholds, colours, limits: every tunable in one place
app/normalize.py   parsing and key rules shared by the loader and the site
app/db.py          every SQL statement of the web app
app/derived.py     summary views (plates, shares, class counts) and their refresh
app/lending.py     main houses and unusual imprints (URL /lending)
app/workbench.py   the admin annotation workbench
app/imaging.py     page fetch with limits, crop, crop and thumbnail stores, LRU disk cache
app/embedding.py   image search: preprocessing, extractor, type check, class ranking
app/places.py      place profiles per design and their contrast
app/compare.py     comparison sides, design alignment, click menus
app/static/geomap.js  map through time (d3 + Natural Earth coastlines, vendored)
app/static/v25.css    v2.5 and v2.6 styles, loaded after style.css
app/static/v26.js     image lightbox; old section links to class-page tabs
app/main.py        routes
app/templates/     Jinja2 pages
app/static/        CSS, JS, fonts, hero image, logos/
etl/schema.sql     tables, indexes, views
etl/load.py        CSV -> Postgres (idempotent)
etl/fetch_crops.py bulk fill of the database crop store
etl/load_model.py  store the image model in Postgres
etl/load_embeddings.py  load emb_<KIND>.npz files, build centroids
etl/link_clusters.py    stored similarity between machine clusters
etl/data/          HP superclass names (C001–C172)
tests/             unit tests and fixture generator
tools/descriptive_stats.py  descriptive statistics for papers (books.csv, by_decade.csv, paragraph.md)
```

## Logos

Put the official files in `app/static/logos/` (SVG or PNG). They appear in
file-name order in the footer and under the front-page hero.
