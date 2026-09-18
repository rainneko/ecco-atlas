# ECCO Ornament Atlas

A research site for printers' ornaments in Eighteenth Century Collections
Online: which ornaments appear in which books, how human classes and machine
clusters relate, which publishers and printers held which blocks, and when a
block seems to have been lent.

The full specification is **[DESIGN.md](DESIGN.md)** (v2).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

createdb ecco                                   # any Postgres 14+
export DATABASE_URL="postgresql://localhost/ecco"
export ADMIN_PASSWORD=letmein SECRET_KEY=dev-secret

python -m etl.load --reset \
    --src HP-pred data/HP.csv    --src DI-pred data/DI.csv \
    --src FT-pred data/FT.csv    --src TP-pred data/TPPD.csv
    --src HP-ann  data/HP_concat_annotation.csv \
    --src DI-ann  data/DI_annotation.csv
python -m etl.fetch_crops                        # optional: store annotated crops

uvicorn app.main:app --reload --port 8000
```

No real data at hand? `python -m tests.make_fixtures /tmp/fx` writes all seven
CSVs in their real layouts, with planted test cases.

## Updating the data

Re-run `python -m etl.load --src <SOURCE> <file.csv>` for the files that changed
(add `--replace` when a new model run should replace a source's clusters); the
loader upserts on image id + box and re-applies every admin correction.

## Tests

```bash
python -m tests.test_normalize          # parsing rules
./smoke.sh http://localhost:8000        # every route; admin flows too if ADMIN_PASSWORD is set
```

## Layout

```
app/config.py      sources, thresholds, colours: every tunable in one place
app/normalize.py   parsing and key rules shared by the loader and the site
app/db.py          every SQL statement of the web app
app/derived.py     summary views (plates, shares, class counts) and their refresh
app/lending.py     ownership and lending analysis
app/workbench.py   the admin annotation workbench
app/imaging.py     page fetch, crop, database crop store, LRU disk cache
app/main.py        routes
app/templates/     Jinja2 pages
app/static/        CSS, JS, fonts, hero image, logos/
etl/schema.sql     tables, indexes, views
etl/load.py        CSV -> Postgres (idempotent)
etl/fetch_crops.py bulk fill of the database crop store
etl/data/          HP superclass names (C001–C172)
tests/             unit tests and fixture generator
```

## Logos

Put the official files in `app/static/logos/` (SVG or PNG). They appear in
file-name order in the footer and under the front-page hero.
