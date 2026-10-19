# ECCO Ornament Atlas v2.5 — changes and deploy steps

DESIGN.md §0.0 and §14 give the reasons. This file gives the steps.

## 1. What the package contains

Unzip the package in the repository root (the folder that holds `app/` and
`etl/`). The package replaces these files with complete new versions, or adds
them. You do not have to edit a file by hand.

| File | Change |
|---|---|
| `DESIGN.md` | v2.5 |
| `CHANGES_v2.5.md` | new (this file) |
| `README.md` | wording, v2.5 notes |
| `app/config.py` | image limits, trade publishers, review explanations, glossary text, menu name |
| `app/db.py` | runtime schema, thumbnail store, stored vectors, search fixes, rejected-cluster record |
| `app/imaging.py` | download limits, merged requests, pause on 429/5xx, thumbnail store |
| `app/embedding.py` | works without embeddings, index without model, grey/white query images |
| `app/lending.py` | neutral time positions, `trade_publisher()` |
| `app/main.py` | image thread pool, robots.txt, security fixes, new class-page data |
| `app/static/v25.css` | new styles (loaded after `style.css`) |
| `app/templates/base.html` | loads `v25.css` |
| `app/templates/_macros.html` | house bars, houses through time, folded source boxes |
| `app/templates/class.html` | "Who used it", "Houses through time", "Books under other imprints", design rows |
| `app/templates/lending.html` | "Unusual imprints" |
| `app/templates/agent.html` | "Blocks used" / "Main house of", paged |
| `app/templates/agents.html` | column names |
| `app/templates/classes.html` | filter finds `C067_01a`; T-tag text |
| `app/templates/index.html` | hero sentence, cards |
| `app/templates/about.html` | assumption, main house / other house, new term "Houses through time" |
| `app/templates/ornament.html` | honeypot, "Find similar" and "Embedding" only when they can work |
| `app/templates/search_image.html` | a sentence instead of the form when no embeddings exist |
| `app/templates/admin.html` | thumbnail store, image-search status |
| `app/templates/workbench.html` | 300 px crops |
| `etl/load.py` | runtime schema; replay of cluster rejections; ESTC numbers in `--places` |
| `etl/load_embeddings.py` | one vector per ornament; exact match within the file's type |
| `etl/link_clusters.py` | blocks of 256 rows (`--block`) |
| `etl/load_model.py` | `weights_only=False` for torch ≥ 2.6 |

The package does not contain the other files of the repository (for example
`schema.sql`, `Containerfile`, `requirements.txt`, fonts, images, d3). Do not
delete them. They do not change.

## 2. Deploy

1. Unzip the package in the repository root. Let it replace the files.
2. Optional: run the tests.
   `python -m tests.test_normalize` and `python -m tests.test_glossary`
   must pass.
3. Commit and push. Rahti builds the new image.
4. Open `/healthz`. It must show `"ok": true`.
5. Open a class page, `/lending` and `/search/image`. Without embeddings the
   search page shows one sentence; this is correct.

No database reset is necessary. No new environment variable is necessary.
The web pod adds the table `thumb_store` and two columns of `label_change`
when it starts.

## 3. After the deploy

- Fill `TRADE_PUBLISHERS` in `app/config.py` from the literature
  (surname key → first year, last year). It is empty now, so no row has the
  tag "trade publisher?".
- Tell the image-server owner about the limits: per web worker at most
  3 downloads at a time and 3 per second (`IMAGE_MAX_PARALLEL`,
  `IMAGE_MAX_RPS`). Ask if the server can return a region at a given size.
- Run bulk crop jobs at night, with a low rate:
  `IMAGE_MAX_RPS=1 python -m etl.fetch_crops`.
- Optional: set `UNUSUAL_IMPRINTS_PUBLIC=0` to show the unusual-imprint
  page only to logged-in admins until the supervisors agree.
- When the embedding files are ready: `etl.load_model`, then
  `etl.load_embeddings`, then `etl.link_clusters` (DESIGN §10). The site
  finds them within a minute, without a restart.

## 4. Not in v2.5 (proposed in DESIGN §11)

- Tabs on the class page; each tab loads only its data (question 20).
- Image lightbox with ←/→ through a class by year.
- Two-column ornament page.
- `REFRESH … CONCURRENTLY` after loads (question 19).
