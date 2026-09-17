# ECCO Ornament Atlas

A project site for browsing printers' ornaments in ECCO: which ornaments appear in
which books, what the variants of a single design look like, and which publishers
and printers held which stock of blocks.

Full requirements and UI spec: **[DESIGN.md](DESIGN.md)**.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# any Postgres 14+ works; pg_trgm ships with it
createdb ecco
export DATABASE_URL="postgresql://localhost/ecco"
export ADMIN_PASSWORD=letmein SECRET_KEY=dev-secret

python -m etl.load \
    --kind HP data/HP_concat_hp_tonson.csv \
    --kind DI data/init_3rd_DI202602.csv \
    --kind FT data/FT_annotations.csv

uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000.

`./smoke.sh` starts the server, walks every route including the report/admin
workflow, and prints the status codes.

## Layout

```
app/normalize.py   parsing + key rules shared by the loader and the site
app/db.py          every SQL statement in the project
app/imaging.py     page fetch, crop, disk cache
app/main.py        routes
app/templates/     Jinja2 pages
etl/schema.sql     tables, indexes, views
etl/load.py        CSV -> Postgres, idempotent
deploy/rahti.yaml  Rahti (OpenShift) manifests
```

## Adding a new annotation CSV

Column names differ between files, so only `etl/load.py`'s `ALIASES` map needs a
new entry. Kind codes are `DI`, `FT`, `HP`, `WE`; add `IO` to `KINDS` in
`app/config.py` when that data is ready.

## Deploy to Rahti

See the "Deployment" section of DESIGN.md, then `oc apply -f deploy/rahti.yaml`.
