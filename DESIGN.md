# ECCO Ornament Atlas — Design Document

Version 2.1 · supersedes v2.0. v2.0 covered all seven data sources; v2.1 adds
**image search** (§9), **places of publication and reprint analysis** (§12),
the front-page scope, glossary, publications and people (§13), a fix to the
book-search cost (§4.7), and small layout changes (§5.2).

---

## 0. What changed, and why

### 0.1 v2.0 → v2.1 (September 2026)

| Request | Decision |
|---|---|
| Book search waits several seconds | Drop the whole-title trigram *similarity* branch; keep the indexed substring match and add a vocabulary-based "did you mean" for typos. 2 queries × ~2.3 s → 1 query × ~5 ms on 80k titles (§4.7). |
| Upload an ornament and find its class and house | New `/search/image` (§9): model checkpoint stored in Postgres, embeddings loaded from four per-type files, CPU inference, a four-stage progress bar, kind confirmation, cluster ranking, house estimate. |
| Places of publication | `book.place` from an ESTC export loaded with `--places`; place composition on class pages; subclass-versus-subclass place contrast; book-pair reprint list; cluster links from embeddings (§12). Every place feature degrades to "no place data" when the file was never loaded. |
| Front page too modest; glossary; publications; people | §13. |
| v2.1.1 (2026-09-20): header, `?` icon, About text, book-pair titles, file upload | §8 header, §9.4 status, §10 upload steps, §12.4, §13.2. |
| Imprint names clickable; sortable book list | §5.2. |

### 0.2 v1 → v2.0

| v1 assumption | Reality | v2 decision |
|---|---|---|
| The data is "DI, FT, HP" | The data is **annotation** (human, verified) **and prediction** (machine clusters), per ornament type | Seven named sources (§3.1). Every ornament can carry a human label *and* a machine cluster at the same time. |
| `superclass/subclass/variant` are global columns | They only exist in annotation files; prediction files have one `HC*` cluster column | Classes are always addressed as **(source, level, value)** — `HP-ann / variant / C067/C067_01/a`, `DI-pred / cluster / 8944`. |
| An unlabelled ornament is "(unclassified)" | It has a machine cluster | It is shown as **`DI-8944`**. |
| DI annotation has subclasses | Its subclass column is not meaningful | DI and FT annotation expose **superclass only**. |
| Annotation rows are extra rows | `DI_annotation.csv` ⊂ `DI.csv` (same detections) | Annotation rows **attach to** the existing prediction row with the same `id + boxes` (exact key, then IoU ≥ 0.9 fallback). |
| Publisher share = "books with this name / books" drawn as a stacked bar | Books name several houses, so shares sum to more than 100% and the bar overflowed | Two named measures (§4.1): **coverage** for ownership logic, **credit share** for the bar. |
| Agent key = "surname" | `J. and R. Tonson` produced the key `j r tonson` | Surname extraction rewritten and tested (§3.7). |
| Crops cached up to N MB | Annotated crops are the core evidence and must not depend on the image server | Annotated crops stored **in Postgres**; everything else in a **1,000-item LRU** disk cache (§6). |
| Admin = one password, report queue | Needs attribution, side-by-side comparison, and bulk relabelling | Named admin sessions, a **comparison** queue, and an **annotation workbench** (§5.9). |

New in v2: plates owned, Tonson/Watts plate badge, co-holding recommendations,
publisher-pair page, source checkboxes on every analysis page, the lending analysis,
formal HP names (Group / Subgroup), sortable class lists, and a front page with an image.

---

## 1. Audiences

**The supervisor** — "what ornaments are in this book?" from a half-remembered
title, in under a minute. → Books search and the page strip (§5.2).

**The team** — compare the variants of one design, see which houses held which
blocks, detect lending, correct labels. → Class pages, publisher pages, lending,
workbench.

**Everyone else** — a public, citable project page. Plain, fast, readable,
closer to a conference project page than a product site.

### Non-goals for this version
- No per-user accounts for the public. Reports are anonymous-with-optional-name.
- No editing of book metadata in the UI.
- No embedding similarity yet; the switch is in place (§4.6, §9).
- No IO data; adding it is one entry in `SOURCES`.

---

## 2. Deployment reality

The site runs on CSC Rahti, project `visual-search`:

| Object | Name | Notes |
|---|---|---|
| Web Deployment | `ecco-atlas` | built from this repo; env vars set in the Console |
| Database Deployment | `postgresql` | `openshift/postgresql:15-el8`, PVC `postgresql` (50 GiB) |
| URL | `https://ecco-atlas-visual-search.2.rahtiapp.fi` | |
| Env vars in use | `DATABASE_URL`, `ADMIN_PASSWORD`, `SECRET_KEY`, `IMAGE_BASE` | |

`deploy/rahti.yaml` is a **reference**, not the live state. The live YAML in the
Web Console is authoritative.

Constraints that still shape the code (unchanged from v1): arbitrary non-root UID,
port 8080, no GPU, 4 CPU / 16 GiB per container with limit ≤ 5 × request,
`standard-csi` is ReadWriteOnce, image-server access from the Rahti egress IP
is unresolved (a failed fetch shows a grey placeholder, never an error page).

**v2 needs no new environment variables.** Optional ones are listed in §10.

---

## 3. Data

### 3.1 The seven sources

| Code | File | Kind | Family | Size | Class column(s) | Levels exposed |
|---|---|---|---|---|---|---|
| `HP-ann` | `HP_concat_annotation.csv` | HP | human | ~28k | `superclass`, `subclass` (+ trailing variant letter) | superclass · subclass · variant |
| `DI-ann` | `DI_annotation.csv` (`init_3rd_DI202602.csv`) | DI | human | ~2k | `superclass` | superclass |
| `FT-ann` | `FT_annotation.csv` (not yet released) | FT | human | ~4k | `superclass` | superclass |
| `DI-pred` | `DI.csv` (`DI_202608.csv`) | DI | machine, MoCo-v3 | ~57k | `HC0.12` | cluster |
| `FT-pred` | `FT.csv` | FT | machine, SimCLR | ~113k | `HC0.03` | cluster |
| `HP-pred` | `HP.csv` (`HP_group1.csv`) | HP | machine, SimCLR | ~200k | `HC0.04` | cluster |
| `TP-pred` | `TPPD.csv` | TP | machine, SimCLR | — | `HC0.015` | cluster |

`TP` covers tailpieces and printer's devices as one kind. (An earlier `WE` kind also covered woodcut illustrations; those were dropped as not relevant to publishing history, and the file was renamed `TPPD.csv` accordingly.)
The `HC<threshold>` column name differs per file; the loader finds it by prefix
and records which one it used (`hc_column`), so the site can say
"`DI-8944` (HC0.12)".

Source descriptions (model, accuracy, review rounds) live in `app/config.py`
`SOURCES` and are shown on the front page, so the site states the status of
each dataset rather than presenting all of it as equally certain.

### 3.2 Column map

Columns differ in every file. The loader reads only what it needs, through
one alias table (`etl/load.py: ALIASES`):

| Field | Accepted column names |
|---|---|
| id | `id` |
| box | `boxes` |
| title | `fullTitle` |
| ESTC | `ESTCID` |
| year | `year` |
| publishers / printers | `publishers` / `printers` |
| total pages | `totalPages` |
| superclass | `superclass` |
| subclass | `subclass`, `subclass?` |
| annotation round | `source` (HP_concat: `hp` / `tonson`) |
| cluster | first column starting `HC` |

Deliberately **ignored**: `url`, `path*`, `img_path*`, `crop_path`, `folder`,
`index`, `labels`, `page_labels`, `scores`, `Init_cluster`, `is_tonson`
(recomputed from the imprint), `book_id` (lost its leading zeros — derived from
`id` instead), `page` (derived from `id`), and the HP_concat bookkeeping columns
(`page_hit`, `book_hit`, `year_span`, `is_tonson_superclass`, `work_id`).
`annotation_name` is not needed per row because the same information arrives
with the names file (§3.6).

**Known metadata gap.** `DI.csv` and `FT.csv` carry no `year`, `publishers` or
`printers`. A book that appears *only* in those files has no date and no imprint,
so its ornaments can be browsed but take no part in publisher analysis. Books
shared with another source get their metadata from there. The loader prints how
many books are affected, and accepts an optional metadata file
(`--books meta.csv`, keyed on `book_id` or `ESTCID`) to fill the gap later.

### 3.3 Keys

Unchanged principle: **an ornament is `id + boxes`**.

```
image_id = id without ".TIF"                 (15 digits = docId + page(4) + "0")
box_key  = x1,y1,x2,y2 rounded to 2 dp
oid      = sha1(image_id | box_key)[:16]     short URL handle
book_id  = image_id[:10]                     leading zeros preserved
page     = int(image_id[10:14])
```

**Attaching annotation to prediction.** For each annotation row:

1. same `oid` exists → attach the human label to that row;
2. otherwise, a row on the same page (`image_id`) whose box has **IoU ≥ 0.9**
   (same kind first, else a `TP` row) → attach, and record the annotation's own
   key in `ornament_alias` so a link built from the annotation CSV still resolves;
3. otherwise → insert a new ornament that has a human label and no cluster.

The loader prints the three counts, so a drift between the annotation and
prediction boxes shows up immediately.

### 3.4 De-duplication of `TPPD.csv`

The TP file contains many detections that are also in the HP (and other) files.
The loader applies your procedure, in order, after the other prediction files
are in the database:

1. drop exact duplicates **within** the file (`id + boxes`, keep first);
2. drop TP rows whose key already exists in **another** source;
3. drop TP rows with **IoU ≥ 0.9** against any other ornament on the same page
   (`--tp-iou`, default 0.9).

Before step 3 it prints the IoU histogram (bins 0 / .5 / .8 / .9 / .95 / .99 / 1)
so the threshold can be chosen from the data. Across the non-TP files, a key
that appears in two sources keeps the first-loaded kind (order DI, FT, HP) and
the collision count is printed.

### 3.5 Two labels per ornament, one vote in analysis

```
ornament ── machine label: pred_src, hc_column, hc_cluster, cluster_rejected
         └─ human label:   ann_src, superclass, subclass, variant, class_path
```

Every page that *shows* a class uses its own membership: the cluster page
`DI-8944` shows every ornament the model put there, including annotated ones.

Every page that *counts* (publisher analysis, book similarity, lending) uses a
**disjoint** assignment so no ornament is counted twice:

```
src   = ann_src  if it has a human label, else pred_src (if clustered)
plate = src + ":" + class_path   or   src + ":" + hc_cluster
```

Consequence for the source checkboxes (§5.7): ticking `HP-pred` means
"HP predictions **excluding** ornaments that already have a human label".
Ticking `HP-ann` and `HP-pred` together covers every HP ornament exactly once,
with the human label preferred. This matches the team's requirement and is
explained under the checkboxes on the page.

`cluster_rejected` is set when an admin says "this image does not belong in
this cluster". Machine output is never rewritten; the rejection is a human
judgment stored beside it.

### 3.6 Classes and their names

A class is `(source, level, value)`:

| Source | Level | Value | Displayed as |
|---|---|---|---|
| HP-ann | superclass | `C067` | `C067` + formal name |
| HP-ann | subclass | `C067_01` | `C067_01` |
| HP-ann | variant | `C067/C067_01/a` | `C067_01a` |
| DI-ann | superclass | `A_Heart` | `A_Heart` |
| DI-pred | cluster | `8944` | `DI-8944` |

Variant parsing is unchanged from v1 (trailing letter on the subclass).
`class_path` is built from the levels the source exposes, so for DI-ann it is
just the superclass.

**Plate** = the level we believe corresponds to a physical block: HP variant,
or HP subclass when it has no variants; DI/FT superclass; any machine cluster.

**Formal names.** `etl/data/hp_superclass_names.csv` maps every HP superclass to
its **Group** name and its **previous annotation name** (transcribed from the
"Subclass_id" list). The full list can be re-imported in its original text
format with `--names file.txt`, which also stores each subclass's **Subgroup**.
Names are displayed with underscores as spaces; previous names containing
`_AND_` are shown as merged classes. The loader warns about duplicate subgroup
names (e.g. `C005_02` and `C005_03` both carry `floral_scroll_helmet_trophy_1`
in the source list).

### 3.7 Books and agents

`book` gains `has_imprint` (at least one parsed agent). Agent keys:

| Imprint fragment | v1 key | v2 key |
|---|---|---|
| `Tonson, J.` | tonson | tonson |
| `J. and R. Tonson` | j r tonson | tonson |
| `R. and J. Dodsley` | r j dodsley | dodsley |
| `J. Rivington & Sons` | j rivington sons | rivington |
| `Robinson and Roberts` | robinson roberts | robinson, roberts |
| `Sword and Buckler Court` | sword buckler court | (dropped: an address) |

Rules: split on `;`; drop fragments that are addresses; with a comma the
surname is before it; without a comma, split "X and Y" into two names unless one
side is only initials, then take the last non-initial word. Still surname-only
and therefore still lossy — two different Watts are one key. This is stated on
the publisher list page.

`is_tonson` (book) = publishers or printers match `tonson|watts`.

### 3.8 Tables and derived views

```
book            book_id PK, estc_id, full_title, title_norm, year, total_pages,
                is_tonson, has_imprint, publishers_raw, printers_raw
agent           agent_id PK, name (key), display_name
book_agent      (book_id, agent_id, role ∈ publisher|printer)
ornament        oid PK, image_id, box_key, x1..y2, kind, book_id, page,
                pred_src, hc_column, hc_cluster, cluster_rejected,
                ann_src, superclass, subclass, variant, class_path, ann_round,
                src, plate, label            ← generated columns
                UNIQUE (image_id, box_key)
ornament_alias  alias_oid → oid
class_name      (src, level, value) → name, alt_name
crop_store      oid → full JPEG, thumb JPEG            (annotated crops, §6)
report          open / accepted / rejected suggestions
label_change    every human relabel, drop or cluster rejection (history)
lending_review  admin verdicts on lending events
upload          [FUTURE] query images
meta            schema_version, refresh status
```

Derived (materialized views, rebuilt after a load and refreshed in the
background after admin edits):

```
mv_book          n ornaments per book
mv_book_plate    distinct (book, plate)
mv_plate_agent   per plate × agent: n_books, n_known, share (coverage), other_max
mv_plate         per plate: size, years, exemplar, n_agents, top_share, top agents,
                 t_owned
mv_agent_src     per agent × source: plates used, plates owned
mv_class         per (source, level, value): n, n_books, years, exemplar, plate_key
mv_source        per source: ornaments, books
```

`label_change` is what makes review work survive a data reload: after every
load, the latest change per ornament is replayed.

**Added in v2.1** (all created by `schema.sql` on the next load, no reset):
`book.place`, `book.place_key`; `book_word` (title vocabulary for typo
fallback); `model_store`, `embedding_model`, `ornament_embedding`,
`cluster_centroid`, `kind_sample` (§9); `cluster_link` (§12.6);
`mv_book_pair` (§12.4).

### 3.9 Updating the data — in one sentence

Copy the new CSV into the web pod and run
`python -m etl.load --src <SOURCE> <file.csv>`; rows upsert on `id + boxes`,
accepted corrections are replayed, and the summary tables rebuild.

(Add `--replace` when a new model run should *replace* a source's clusters
rather than be merged into them; add `--reset` once, for the v1 → v2 schema
change.)

---

## 4. Analysis definitions

### 4.1 Two share measures

For a set of books (a class or a plate), counting only books whose imprint names
at least one house (`n_known`):

- **coverage(agent)** = books naming the agent ÷ `n_known`.
  A book "printed for Tonson and Draper" counts for both, so coverages can sum
  to more than 100%. This is the measure the lending definition uses
  ("owns > 70% of the books").
- **credit share(agent)** = the agent's book count ÷ the sum of all agents'
  book counts. Sums to 100%. Used only to draw the stacked composition bar.

The composition panel says this in one line. Publishers and printers are shown
in separate panels; ownership and lending **pool** the two roles, because in
this period the block was usually the printer's while the imprint names the
bookseller first — pooling avoids deciding that per book.

### 4.2 Plates used, plates owned

- **Plates used** by a house: every plate in any of its books.
- **Plates owned**: plates where the house's coverage is **strictly greater**
  than every other house's, and the plate has at least 3 imprint-known books
  (`OWNER_MIN_BOOKS`). A toggle also admits **joint** holdings (tied for the
  largest share) — common when two booksellers appear together on every book.
- **Tonson/Watts plate badge** (solid **T**): the largest holder of the plate
  (sole or tied) is Tonson or Watts, with ≥ 3 known books. Distinct from the
  outlined **T** that marks a *book* whose imprint names them.

### 4.3 Houses with similar stock (kept from v1)

Cosine over idf-weighted plate sets, weight `1/ln(1 + number of houses using
the plate)`. Answers "who drew on the same stock of blocks".

### 4.4 Co-holders (new)

For houses A and B and every plate both hold with coverage ≥ 20%
(`min_share`, adjustable) and ≥ 3 known books:

```
co-holding(A, B) = Σ over those plates of min(coverage_A, coverage_B)
```

"I hold 40%, you hold 30% → 0.30 of shared ownership", summed. Unlike 4.3 it
ignores plates a house merely used once; it rewards houses that *together*
account for the bulk of the same plates. Both lists link to the pair page.

### 4.5 Lending

**Question.** A plate is predominantly one house's; a few books under another
house use it. Is that a loan?

**Share evidence** (the new definition). For a plate with at least
`min_books` imprint-known books:

- **owners** = houses with coverage ≥ `owner_min` (default **0.70**). Usually
  one; if two houses co-sign most books, both are owners and shown as
  "Tonson + Draper".
- **borrowers** = every other house with coverage in `[lo, hi]`
  (default **0.05–0.20**). With three or more small holders, each is a candidate.
- **borrow events** = the borrower's books on this plate that name **none** of
  the owners. A book co-signed by owner and borrower is co-publishing, not a
  loan, and is excluded.
- `min_books` default **5**: below 5 books a single book is already ≥ 20%, so
  the band cannot express "an occasional loan".

**Time evidence** (the notebook's idea, made explicit). Each event is compared
with the owners' own use of the plate, the span `[y0, y1]` of their books:

| Borrower book year | Verdict shown |
|---|---|
| inside the owner's span | **Lending** |
| outside by ≤ `gap` years (default 5) | **Lending (near)** |
| more than `gap` after the owner's last use | **Later holder?** — transfer or sale |
| more than `gap` before the owner's first use | **Earlier holder?** |
| no date | **Undated** |

The notebook compared with the median year; the span is used instead because a
borrower book in the middle of a 40-year owner span can be far from the median
yet is the clearest possible loan. The median gap is still shown in the tooltip.

**Why two indicators and not one score.** A single confidence number would imply
a calibration we do not have. The share test decides *whether* a row is a
candidate; the time test says *what kind of transfer* the evidence supports. Both
are shown side by side; nothing is hidden behind a formula.

**Parameters** are URL parameters with sliders on the page — no thresholds are
hard-coded in SQL. Defaults live in `config.LENDING_DEFAULTS`.

**Stored or live?** Live, from `mv_plate_agent`, which already holds every
plate × house share; candidate plates are pre-filtered in SQL
(`top_share ≥ owner_min` and some share in the band), so the page stays fast
at any threshold. Only the admin's verdict on an event is stored
(`lending_review`), keyed by plate + owner + borrower + book, so it survives
threshold changes and data reloads.

### 4.6 Ornament similarity

| Method | Used when | Score |
|---|---|---|
| Annotation hierarchy | the ornament has a human label | same variant 1.00 · subclass 0.98 · superclass 0.90 |
| Machine cluster | no human label, or chosen | same cluster 0.95 (placeholder) |
| Embedding cosine | **not yet available** — shown disabled | pgvector `<=>` (§9) |

All numbers are in `config.SIM_SCORES`. Swapping in embeddings changes only
`db.similar_ornaments`.

### 4.7 Book search cost (v2.1)

**Before.** `/books?q=fable of` ran two SQL queries (results, then a count),
each with the predicate `title_norm LIKE '%fable of%' OR title_norm % 'fable of'`.
The `%` (trigram similarity) branch is the problem: with the GIN trigram index
the planner builds a bitmap of every row sharing *any* trigram with the query,
which for a query containing ` of` is essentially every row, and then rechecks
each candidate by computing `similarity()` on a ~280-character title. Cost per
query is therefore **O(N · L)** — N = 77,848 titles, L ≈ 280 characters — twice.
Measured on 80,000 synthetic ECCO-length titles: 2.27 s + 2.33 s ≈ **4.6 s**.
The similarity branch also never matched anything useful: similarity of an
8-character query to a 280-character title is ~0.02, far below the 0.3
threshold, so all hits came from the LIKE branch anyway.

**After.** One query, three parts:
1. **Substring match, indexed.** `LIKE '%w%'` for each query word (all words
   must occur; adjacent-phrase hits ranked first). pg_trgm answers LIKE from the
   GIN index by intersecting the posting lists of the query's own trigrams:
   **O(T · log N + M)** with T = trigrams in the query (≈ 8) and M = matching
   rows; the recheck runs on M rows, not N. Measured: **3 ms** for `fable of`,
   28 ms for `history of england` (7 hits), 0.2 ms for a miss.
2. **Count in the same query** (`count(*) OVER ()`): no second scan.
3. **Typo fallback on the vocabulary, not on titles.** When step 1 finds
   nothing, each query word is matched against `book_word` — the ~50k distinct
   words of all titles, with their own trigram index — and the closest word
   (similarity ≥ 0.5) replaces it; the page says "Showing results for *fable*;
   no title contains *fabel*". Similarity runs on 5–10-character words, so it is
   **O(T · log V + M_v)** with V ≈ 50k — milliseconds. The old design paid the
   full-title cost on every search to get this for the rare typo.

`book_word` is rebuilt with the derived views (`derived.rebuild`).

---

## 5. Pages

Every analysis page carries the same **source checkboxes** (§5.7) where the
choice changes the result; the current choice is kept in the URL, so any view
can be bookmarked or sent to a colleague.

### 5.1 `/` — Front page

- **Hero**: a full-width band with the workshop image in black-and-white under a
  dark veil, white Caslon title, one sentence of purpose, and the book search.
  The image fades in once on load (1.2 s, no motion if the visitor prefers
  reduced motion). Without `app/static/hero.jpg` the band is plain ink-coloured.
- **"Data in this atlas"**: a table, one row per source — family, ornaments,
  books, classes, and its status line (model, accuracy, review rounds). This
  replaces the v1 stat chips: it tells a scholar what is verified and what is
  machine output.
- Four entries: Books · Ornament classes · Publishers & printers · Lending.
- Ornament type legend with colours.

### 5.2 `/books` — Book search

Substring search on every query word (§4.7), typo fallback via the title
vocabulary, filters for ornament type, Tonson/Watts and — when loaded — place
of publication (§12). Counts come from the same query, all page strips from one
query. v2.1 layout changes:

- **Sortable columns** with the same sort arrows as the class list: Title,
  Year, Ornaments (default: relevance when there is a query, else year).
- **Imprint names are links.** The imprint cell shows the parsed houses as
  chips — `Bickerton, T.` *printer* — each linking to its `/agent/{name}`
  page; the raw ESTC string is the cell's hover title, and is shown as text
  only when the parser found no house in it.
- Place of publication shown after the year when known.
- hover card shows the ornament's label (`C067_01a` / `DI-8944`) as well as the
  page; pager with previous / next and a window around the current page.

**Page strip** (kept): four rows DI / FT / HP / TP, colours
DI `#e8833a`, FT `#e3c41e`, HP `#4a9d5f`, TP `#3d7ea6`; a count above a dot when a
page carries more than one; dots are links; one shared lazy hover card.

### 5.3 `/book/{id}` — Book detail

Kept: metadata, imprint with agent chips, strip, gallery. Gallery captions
link to the class (`C067_01a` or `DI-8944`). **Ten most similar books** are
computed over plates (not only HP subclasses), with the source checkboxes.

### 5.4 `/ornament/{oid}` — Ornament detail

Three columns:

- **Image**: 900 px crop with 4% margin; full page; ECCO viewer.
- **Record**:
  - *Human label* — source, superclass **with its formal Group name** and
    previous annotation name, subclass (with Subgroup when loaded), variant,
    annotation round; or "none".
  - *Machine cluster* — `DI-8944` (HC0.12) linking to the cluster page, or
    "rejected by reviewer".
  - Book, year, page, ESTC, imprint, image id, and the `id + boxes` key.
- **Most similar**: method switch (hierarchy / cluster / embedding-disabled),
  three thumbnails with score and reason, and **See all images of this kind**
  (most specific class), plus **See its machine cluster** when both exist.

Below: the report form — one "Suggested class" field with suggestions of
existing classes (e.g. `C067_01b`, `A_Heart`), a reason, an optional name.

### 5.5 `/classes` — Browse classes

One selector with two groups:

```
Human annotation   HP · superclass | HP · subclass | HP · variant | DI · superclass | FT · superclass
Machine prediction DI clusters (HC0.12) | FT clusters | HP clusters | TP clusters
```

Only sources with data are listed. The filter box also matches formal names
(searching "eagle" finds C001). Table columns are **sortable** — click a header,
the arrow shows direction:

| thumb | class ▾ | images | books | first year | last year | span |

Solid **T** on plate-level rows held mainly by Tonson/Watts. Paged by 100.

### 5.6 `/class/{source}/{level}/{value}` — Class detail

1. **Header**: display label, formal name and previous name, breadcrumb
   source › superclass › subclass › variant; admins get "Open in workbench".
2. **Composition** — publisher and printer panels; stacked bar of credit share,
   list with coverage and book counts; owner and borrower chips (§4.5).
3. **Lending** (plate-level classes only): owners, borrower candidates, their
   books with year evidence, link to the full lending list with the same
   parameters. On a non-plate class (an HP superclass, a subclass with
   variants) a sentence explains that lending is computed per plate, below.
4. **Structure**:
   - superclass → its subclasses, one image each;
   - subclass → its variants; sidebar: the other subclasses of the superclass;
   - variant → sibling variants; **sidebar: the other subclasses of the
     superclass, one image each** (requested);
   - cluster → which human labels occur inside it, and vice versa on human
     classes: which clusters their images fall in. This is the first
     cluster-versus-annotation view, and it costs one query.
5. **All images**. Order switch: **by year** (default — a block's wear appears
   in date order) or **grouped by the next level down** (superclass → subclass,
   subclass → variant; DI superclass → its raw subclass codes), each group by
   year. Captions: year, variant, book, book-level T.

Legacy v1 URLs `/class/subclass/C067_01` redirect to `/class/HP-ann/…`.

### 5.7 Publishers & printers

**Source checkboxes** (on list, detail and pair pages):

```
[x] HP annotation  [x] DI annotation  [x] FT annotation
[x] HP prediction  [x] DI prediction  [x] FT prediction  [x] TP prediction
    Predictions exclude ornaments that already have a human label, so each
    ornament is counted once.
```

**`/agents`** — search; columns books, plates used, plates owned (for the
selected sources); sortable.

**`/agent/{name}`** —
- books, ornaments, active years, role(s);
- tabs **Plates used** / **Plates owned** (+ "include joint holdings");
  the owned tab explains its rule in two sentences; each plate thumbnail shows
  the house's coverage as a thin bar, the plate label, and the solid T when
  applicable; sorted by class id (`C067_01a` next to `C067_01b`), then clusters
  numerically;
- **Houses with similar stock** (4.3) and **Co-holders** (4.4), each row with a
  "compare" link to the pair page;
- lending summary: "lent N times, borrowed M times" → `/lending?agent=…`.

**`/agents/pair/{a}/{b}`** — the two houses side by side: similarity and
co-holding scores, **shared plates** (thumbnail + both coverages), and
**ornaments in books that name both**, by year.

### 5.8 `/lending` — Suspected lending events

- Sliders: owner ≥, borrower band low / high, minimum books, year gap;
  source checkboxes; optional filters `agent=` (as owner or borrower) and
  `plate=`.
- Summary line: plates meeting the rule, events, and the verdict breakdown.
- Table, one row per event: plate thumbnail and label · owner(s) with coverage ·
  borrower with coverage · book (title, year) · owner span · verdict chip.
  Admins see Confirm / Reject per row; the verdict is stored.
- `/lending.csv` with the same parameters, for use in a notebook.

### 5.9 Admin

**Login** — name + password. The password is `ADMIN_PASSWORD` (shared) or a
personal one from the optional `ADMIN_USERS`; the name is recorded in every
change (`changed_by`). Signed, HttpOnly, 8-hour cookie. A discreet "Admin" link
sits in the footer (v1 had no visible entry point, which is why it looked
missing).

**`/admin`** — dashboard: open reports, recent changes, workbench launcher,
summary-table refresh status and a "refresh now" button.

**`/admin/reports`** — the comparison queue. Each report shows three things side
by side: the reported crop, three images of its **current** class, and three
images of the **suggested** class. The suggested label is an editable field, so
an admin can accept a corrected version. Accept writes a `label_change` row then
updates the ornament in one transaction.

**`/admin/workbench/{source}/{level}/{value}`** — bulk annotation for any
superclass (or HP subclass), **including a machine cluster such as `DI-8944`**.

```
┌ C067 · baskets with curved foliage and decorative frame ─── 412 images ─┐
│ [ 3 changes pending ]  Move selected to [ C067_02a ▾ ] [Move]  [Save]   │
├─ C067_01 a ─────────────────────────────── 88 ──────────────────────────┤
│ [img][img][img][img]      four per row, 900-px source, by year           │
├─ C067_01 b ─────────────────────────────── 20 ─┤ ...                     │
├─ New 1  [C067_05    ]  ─┤ New 2 [C067_06] ─┤ … New 5 ─┤ Drop ─┤          │
└──────────────────────────────────────────────────────────────────────────┘
```

- Sections are the existing buckets (each variant, grouped under its subclass),
  then **five new buckets** with editable names (prefilled with the next free
  subclass numbers, or the next variant letters on a subclass), then **Drop**
  ("not this class").
- Move an image by dragging it, by its own menu, or select several (click,
  shift-click for a range) and move them together.
- **Save changes** posts only the moves; the page reloads from the base table,
  so the result is visible at once. Summary tables refresh in the background.
- On a **machine cluster**: sections are defined by each member's *human*
  label. The first is "no human label yet"; then one section per human label
  already given to members (so after saving and reloading, the images you just
  labelled appear in their own section instead of snapping back). New buckets
  create **human labels** (e.g. `DI-ann` superclass `B_Lion`); the images stay
  in the machine cluster. Drop marks `cluster_rejected`. TP clusters have no
  human-label source yet, so they offer Drop only.
- A new bucket named after a class elsewhere (e.g. `C006_01` inside the C005
  workbench) moves the images into that class; the page says so.
- Saving is refused for any image that has left the class since the page was
  loaded ("reload the page"), so two admins cannot silently overwrite each
  other.
- On a human class, Drop removes the human label; the ornament falls back to its
  machine cluster.
- Every move is a `label_change` row with a batch id, so a session can be
  audited.

---

## 6. Images

| Kind of ornament | Where its crop lives | Why |
|---|---|---|
| **Annotated** (any `ann_src`) | **Postgres `crop_store`**: native-resolution crop (≤ 1400 px) + 300 px thumb | These are the evidence the project rests on; they must not depend on the image server staying reachable. ~34k × ~120 KB ≈ 4 GB of the 50 GiB volume. |
| **Everything else** | **LRU disk cache, 1,000 items** (`CACHE_MAX_ITEMS`) | ~400k possible crops; only what people look at is worth keeping. |

**Filling `crop_store`.** Write-through on first view, plus a bulk job
`python -m etl.fetch_crops` that fetches each page **once** for all annotated
ornaments on it. If Rahti's egress is still blocked by the image server, run the
same command from a machine that can reach it, with `DATABASE_URL` pointing at
the database through `oc port-forward`.

**The LRU cache** is the "queue" you described, with one refinement: a cache hit
moves the item to the back of the queue (it is the file's modification time), so
frequently viewed crops are not evicted just because they were first in.
When the queue exceeds 1,000 files the oldest are deleted. A crop is 20–80 KB,
so 1,000 items is about 50 MB; raising the limit to 20,000 costs about 1 GB.

**Widths** snap to 300 / 900 / 1400 so the three views share cache entries.
Failures return a grey SVG placeholder and are not cached; a page that failed is
not re-requested for 60 s.

---

## 7. URL map

```
GET  /                                        front page
GET  /books?q=&kind=&tonson=&place=&sort=&dir=&p=  search (v2.1: sortable, place filter)
GET  /book/{book_id}?src=                     book detail
GET  /ornament/{oid}?sim=hier|cluster|embed   ornament detail
POST /ornament/{oid}/report                   suggest a correction
GET  /classes?tax=SRC:LEVEL&q=&sort=&dir=&p=  browse classes
GET  /class/{src}/{level}/{value}?order=&p=   class detail
GET  /agents?q=&src=&sort=                    publishers & printers
GET  /agent/{name}?src=&view=used|owned&joint= house detail
GET  /agents/pair/{a}/{b}?src=&p=             two houses
GET  /lending?owner=&lo=&hi=&min_books=&gap=&src=&agent=&plate=
GET  /lending.csv?…                           same, as CSV
GET  /reprints?p=  /reprints.csv               book pairs sharing plates (§12.4)
GET  /about                                   glossary, method, publications, people (§13)

GET  /search/image                            upload form (§9)
POST /api/image-search                        multipart upload → {token}
GET  /api/image-search/status                 {model_loaded, expected_ms}
POST /api/image-search/{token}/embed          → type scores
POST /api/image-search/{token}/match  {kind}  → classes, images, houses
GET  /search/image/{token}?kind=              result page, valid 24 h
POST /search/image/from/{oid}                 search with an existing ornament

GET  /admin/login  POST /admin/login  GET /admin/logout
GET  /admin                                   dashboard
GET  /admin/reports?status=                   comparison queue
POST /admin/report/{id}                       accept | reject
GET  /admin/workbench/{src}/{level}/{value}   workbench
POST /admin/workbench/save                    JSON moves
POST /admin/lending/review                    confirm | reject event
POST /admin/refresh                           rebuild summary tables

GET  /img/crop/{oid}.jpg?w=                   crop
GET  /img/class/{src}/{level}/{value}.jpg     class exemplar
GET  /img/page/{oid}                          redirect to the page image
GET  /api/book/{id}/strip  /api/ornament/{oid}  /api/search/books  /api/docs
GET  /healthz
```

---

## 8. Visual design

The images are the content; the frame stays quiet. v2 keeps the v1 palette the
team liked and spends its one bold move on the front page.

| Token | Value | Use |
|---|---|---|
| paper | `#faf9f6` | background |
| ink | `#1c1a17` | text, hero veil |
| rule | `#e2ddd3` | hairlines |
| rubric | `#8a3324` | links, buttons, badges — the printer's red |
| muted | `#8d867b` | secondary text |

**Type.** Headings in **Libre Caslon Text**, a revival of William Caslon's
types — the face of much of the English printing in this very corpus. Served
from `/static/fonts` (no third-party requests, OFL licence included). Body text
stays in the system sans for legibility of tables and data.

**Hero.** Full-bleed image, `grayscale(1)` with raised contrast under an ink
gradient; white Caslon title. One orchestrated fade on load, nothing else moves
on the site.

**Line-break fix (5.1 in the request).** Source newlines inside a paragraph do
not render as breaks in HTML; the odd breaks came from the lead paragraph's
narrow measure (`max-width: 62ch` inside a `70ch` block), which wrapped after a
word like "which". v2 uses a 68ch measure, `text-wrap: pretty` (keeps a lone word
off the last line), and keeps each sentence on one source line.

**Header (v2.1.1).** On every page except the front page the site header is
ink (`#1c1a17`) with white links; the front page keeps the light header above
its dark hero. The current page and the hovered link are shown the same way —
**bold, white**, no underline (v2.1 underlined the hover *and* marked the
current page with a red underline: two different lines for two states). Bold
text is wider than regular, so each link reserves its bold width (a hidden
bold copy of its label) and the row does not shift when the pointer moves.

**Help icons.** The `?` circle is styled with a selector more specific than
any container rule (`span.help > a`), so it keeps its shape inside tab rows,
table headers and panels — in v2.1 the tab-row style turned it into a pill on
the house page. It sits outside the tab row there. Inline tags and help icons
inside a sentence get 3–4 px of space on each side.

Other fixes: composition bar no longer overflows (credit share); report form
no longer shows current values as greyed placeholders that look like input;
long class names wrap at underscores instead of mid-word; visible keyboard focus
on every control; all multi-column layouts collapse below 980 px.

---

## 9. Image search (v2.1)

Upload one ornament image; the atlas says what type it is, which classes it
most resembles, and which houses those classes point to. This replaces the v2.0
"Phase 2" plan: **no pgvector** (the cluster runs stock `postgresql:15-el8` and
the vectors are small enough to search in NumPy), **no separate embedder
deployment** (one ViT-S forward pass on CPU is 0.1–0.3 s), and **no GPU**.

### 9.1 Why CPU is enough

| Step | CPU cost (ViT-S/16, one image) | When |
|---|---|---|
| Load the model from Postgres into memory | 5–15 s | once per pod start, in a background thread at startup |
| Forward pass, one input size | 0.1–0.3 s | up to 4 passes if the four types use different input sizes |
| Type check, kNN on 4 × 300 samples | < 5 ms | |
| Cluster ranking, cosine on ≤ 70k centroids | 20–50 ms | |
| Exact match on the members of the top classes | 30–100 ms | |

The site serves a handful of concurrent users; a GPU would save two seconds
per search and cost a second deployment, a driver stack and quota. Not worth
it. The one thing to do is raise the web pod's **memory limit to 2 Gi**
(torch + timm + model + centroids ≈ 1.2 GB resident).

### 9.2 The model: where it lives and how it is loaded

The checkpoint is stored **in Postgres**, like the annotated crops: no PVC
to set up, survives pod restarts, moves with the database.

```
python -m etl.load_model --ckpt moco_vit_s_stage2.ckpt --name moco_vit_s
```

The loader opens the Lightning checkpoint, works out the architecture from
`hyper_parameters.backbone` (falling back to the key names), keeps only the
**online backbone** weights (`backbone.base_model.*` for MoCo/BYOL
`BackboneWrapper`; `convnet.*` minus `fc` for the SimCLR family), and stores a
plain state dict (~88 MB for ViT-S, 45 MB for ResNet-18) in
`model_store(name, arch, dim, state bytea, sha256, loaded_at)`. Supported
architectures: `vit_s` (timm `vit_small_patch16_224`, 384-d), `resnet18`
(512-d), `resnet50` (2048-d).

`app/embedding.py` reproduces the research code's inference exactly:
grayscale → RGB repeat, ViT input interpolated to 224×224, ResNet features
flattened, output L2-normalised (`docret.eval.uap.extract_features`). It does
not import lightning, lightly or the training modules, so the web image only
needs `torch` (CPU wheel), `timm`, `numpy`. The checkpoint in use is the
Stage II `MoCoSupervised` (decided 2026-09-18).

**Resize, not centre crop (fixed 2026-09-19).** The research evaluation
transform `RandomResizedCrop(size, scale=(1,1), ratio=(1,1))` was documented
as "equivalent to a plain resize". It is only for exactly square images:
torchvision's fallback (verified in the 0.21 source) crops any other image to
its centre square — a 4:1 headpiece kept 25 % of its width, a 190×210 initial
90 %. The ECCO "none" training views (`RandomResizedCrop(scale=1)` with the
default ratio 3/4–4/3) kept the middle 33 % of a headpiece. Effect on reported
numbers: small on `ecco_di` (near-square initials), large on `ecco_hp`.

Fix: the atlas and `export_embeddings.py` now **resize the whole box**
(`crop: full`, the default; recorded per type in `embedding_model`, so older
vectors marked `square` are still queried the way they were made).
`fix_resize.py` patches the research code: `EvalSpec.resize` — `stretch` for
the print collections (ECCO, Rey), `center_square` kept for ImageNet, where it
is the standard protocol; `--also-training` switches the three ECCO "none"
views to `Resize((100, 400))` (opt-in: it changes future models, and the code
marks those lines "do not touch"). Reported evaluations should be re-run.
"Find similar" cuts the box without padding, exactly as the export does, so an
ornament matches itself at 1.00.

### 9.3 Embedding files and tables

One file per ornament type, produced on the GPU machine by
`tools/export_embeddings.py` (added to the research repository, §9.7):

```
emb_DI.npz   emb_FT.npz   emb_HP.npz   emb_TP.npz
```

each an `np.savez_compressed` archive with arrays
`id` (the CSV `id`, e.g. `147110010002570.TIF`), `boxes` (N×4 float32, the CSV
boxes), `emb` (N×D float32, L2-normalised) and `meta` (a JSON string: model
name, `dim`, `input_size`, transform description). CSV is accepted for small
tests but 464k × 384 floats as text is 1.5 GB; npz is ~350 MB total.

```
python -m etl.load_embeddings --model moco_vit_s \
    emb_DI.npz emb_FT.npz emb_HP.npz emb_TP.npz [--replace]
```

matches rows to ornaments by `(image_id, box_key)`, then IoU ≥ 0.9 (the
annotation-attach logic of §3.3), stores float16 vectors in
`ornament_embedding(oid, vec bytea)` and records the model in
`embedding_model(name, dim, input_sizes, n, loaded_at)`. **One model per
database at a time**: `--replace` swaps the whole set, so ResNet-18 today and
ViT-S tomorrow is a reload, not a migration. The app compares the checkpoint's
output width with `embedding_model.dim` and refuses to search — with a clear
message on the page — when they differ.

Derived after each load (also by `derived.rebuild`):
- `cluster_centroid(src, value, n, vec)`: mean vector per machine cluster and
  per human plate (variant for HP-ann, superclass for DI-/FT-ann);
- `kind_sample`: 300 random ornaments per type, for the type check.

### 9.4 Pipeline

```
upload ─▶ embed ─▶ type check ─▶ (confirm) ─▶ rank classes ─▶ exact match ─▶ houses
  15%       60%        pause                     85%              95%         100%
```

1. **Upload** (`POST /api/image-search`): JPEG, PNG, TIFF or WebP, ≤ 8 MB.
   The image is converted to RGB, resized to ≤ 1600 px on the long side and
   written to `CACHE_DIR/queries/<token>.jpg` (token: 16 hex characters).
   Everything about a query lives under that token for **24 hours** and is then
   pruned; nothing goes into the database. A visitor can leave the page and
   come back to `/search/image/<token>` within that time, so the page does not
   need a "do not leave" warning — only the upload itself has a
   `beforeunload` guard.
2. **Embed** (`POST /api/image-search/<token>/embed`): one forward pass per
   distinct input size in `config.EMBED_INPUT` (HP 100×400, DI/FT/TP 200×200 by
   default — the sizes the export used, recorded in `embedding_model`). The
   query must be preprocessed the way the stored vectors were, and the right
   preprocessing depends on the type we do not know yet; running each type's
   own preprocessing and comparing like with like resolves this at the cost of
   ≤ 0.6 s.
3. **Type check**: for each type, cosine to its 300 samples, score = mean of
   the 10 nearest. The result is shown as four bars; the user confirms or picks
   another type and presses Continue. (Ten samples per type would be too
   noisy — a random 10 rarely spans a type's shapes.)
4. **Rank classes** (`POST /api/image-search/<token>/match`, body
   `{kind}`): cosine to every centroid of that type — machine clusters of
   `<KIND>-pred` and human plates of `<KIND>-ann` — top 25 of each.
5. **Exact match**: the members of those classes (a few thousand vectors) are
   fetched from `ornament_embedding`; each class gets `max` (shown) and `mean`
   member similarity; the 12 closest single ornaments are listed.
6. **Houses**: for the top classes with `max ≥ 0.50`, weight
   `w_c = (max_c − 0.50) / 0.50`; each house's estimate is
   `Σ_c w_c · coverage(house | c) / Σ_c w_c` using `mv_plate_agent` (§4.1),
   publishers and printers separately. Shown as bars with the sentence "based
   on the imprints of the books that carry the N most similar classes; a
   similar design is not proof of the same block".
   Optional `EMBED_INDEX=full` builds a float16 memmap of *all* vectors of a
   type (≈ 360 MB for 464k × 384) for exhaustive nearest neighbours; the
   default centroid-first mode answers the same question for a fraction of
   the memory.

The engine's readiness is **re-checked**: a status request while the model is
not loaded retries loading in the background at most every 30 s, so tables
created or embeddings loaded after the pod started are picked up without a
restart, and a missing table is reported as "no embeddings have been loaded",
not as a database error.

Steps 4–6 are re-run when the user changes the type; results are cached in
`<token>.json` so the result page is server-rendered and shareable.

### 9.5 Progress bar

Real progress is only observable for the upload. The rest is short but not
instant, and a bar that sits at 0 % then jumps to 99 % teaches people to
reload. Design:

- four labelled stages with fixed spans (Upload 0–15, Embedding 15–60,
  Matching 60–95, Results 95–100); the kind confirmation is a pause at 60 %
  with the stage list showing "step 2 of 4 — confirm the type";
- upload uses the XHR progress event (real);
- embedding and matching are **time-eased**: the bar advances linearly to 90 %
  of the stage span over the stage's *expected* duration, then crawls
  asymptotically toward the span's end, and snaps to the end when the response
  arrives. Expected durations are a rolling average of the last 20 runs,
  reported by `GET /api/image-search/status` together with `model_loaded`;
  defaults 2.0 s and 0.5 s;
- each stage shows "usually about 2 s"; after 8 s the text changes to "still
  working — the first search after a restart loads the model, 10–20 s"; when
  the status call says the model is not loaded yet, a preliminary "Loading the
  model" stage is shown instead of pretending to embed.

### 9.6 Page layout (`/search/image`)

Same visual language as the rest of the site. Two columns on desktop:

- **Left (320 px)**: the query image; "Type" panel — four bars (DI/FT/HP/TP)
  with scores, radio buttons to override, Continue button; a note on how the
  score was computed; "Search another image" link; the 24-hour validity note.
- **Right, top**: "Most likely classes" — table: thumbnail, class (link),
  human/machine tag, similarity max · mean, images, books, years, T tag.
- **Right, middle**: "Closest single images" — gallery of 12 with year and
  book, same thumb style as class pages.
- **Right, bottom**: two panels, Publishers / Printers, the house estimate as
  bars, with the caveat sentence.

Entry points: "Image search" in the header; "Find similar" button on every
ornament page (posts the ornament's own crop, so the machinery can be tested
without an upload).

### 9.7 What the research repository needs

`tools/export_embeddings.py` (new file; reuses `docret.eval.backbones.
load_backbone` and the collection transforms):

```
python -m tools.export_embeddings --csv HP.csv --kind HP \
    --backbone moco_vit_s --ckpt <path> --input-size 100x400 --out emb_HP.npz
```

It crops each box from the page image named in the `path` column (one page
opened per page, all its boxes cropped), or uses a crop column
(`--crop-col img_path`) when present, applies the same deterministic
transform as evaluation (`RandomResizedCrop(scale=1, ratio=1)` + `ToTensor`,
no channel normalisation for ECCO), runs the frozen backbone in batches, and
L2-normalises. `TP` has no `EvalSpec` today; the script takes `--input-size`
so nothing else needs adding. Two remarks from reviewing the code: (1)
`uap_callback.compute_embeddings` calls `.squeeze()`, which drops the batch
axis for a batch of one — the export script does not use it; (2) the callback
uses `min_matches_super=1` while `docret.eval.uap` uses 2 — inert today
(`check_class_sizes.py`), worth aligning.

---

## 10. Deploying this version

1. **Rebuild the web image.** v2.1 adds `torch` (CPU wheel), `timm` and `numpy`
   to `requirements.txt`; the Containerfile installs torch from the CPU wheel
   index so the image grows by ~700 MB, not ~3 GB. Raise the `ecco-atlas`
   Deployment's **memory limit to 2 Gi** (Web Console → the Deployment →
   Resources) — needed only once image search is used.
2. **Load** (a v2.0 database does not need `--reset` for v2.1; `schema.sql` adds
   the new tables and columns on the next load):
   ```
   python -m etl.load --src HP-pred /tmp/data/HP.csv   --src DI-pred /tmp/data/DI.csv \
       --src FT-pred /tmp/data/FT.csv   --src TP-pred /tmp/data/TPPD.csv \
       --src HP-ann  /tmp/data/HP_concat.csv \
       --src DI-ann  /tmp/data/DI_annotation.csv \
       --src FT-ann  /tmp/data/FT_annotation.csv \
       --places /tmp/data/places.csv                  # optional, §12.1
   python -m etl.fetch_crops                          # optional, fills crop_store
   python -m etl.load_model --ckpt /tmp/data/moco_vit_s.ckpt --name moco_vit_s
   python -m etl.load_embeddings --model moco_vit_s /tmp/data/emb_*.npz
   python -m etl.link_clusters --min-sim 0.85         # §12.6, needs embeddings
   ```
   `--reset` still refuses to drop a database that holds reports or label
   changes unless `--force-reset` is given.
   **Getting files into the pod.** The web pod's filesystem is the container's
   own: everything under `/tmp` disappears when the pod is replaced (every
   build, every change of resources). Copy files in right before loading
   them, load, then delete them:
   ```
   oc get pods                                   # the ecco-atlas-xxxxx-yyyyy name
   oc rsync ./data/ ecco-atlas-xxxxx-yyyyy:/tmp/data/   # a folder
   oc cp ./model.ckpt ecco-atlas-xxxxx-yyyyy:/tmp/data/model.ckpt   # one file
   ```
   ("rsync not available in container" is a warning: `oc rsync` falls back to
   tar and still copies.) Large files — the checkpoint, the four
   `emb_*.npz` (~350 MB together) — one at a time, deleting each after its
   load (`oc exec <pod> -- rm /tmp/data/<file>`), to stay inside the pod's
   disk allowance. Nothing loaded into Postgres is lost when the pod goes.
   With `WEB_CONCURRENCY=2` each of the two workers holds its own copy of the
   model and centroids (~0.6 GB each); if the pod is OOMKilled, set
   `WEB_CONCURRENCY=1`.
3. **Environment variables**: none required. Optional, in the `ecco-atlas`
   Deployment's environment settings:
   - `ADMIN_USERS` = `alice:pw1,bob:pw2` for personal admin passwords;
   - `CACHE_MAX_ITEMS` (default 1000);
   - `HERO_CREDIT` to change the caption on the front-page engraving;
   - `EMBED_MODEL` to pick a model when `model_store` holds several;
   - `EMBED_INDEX=full` for exhaustive nearest neighbours (§9.4).
4. **Logos**: official University of Helsinki and COMHIS files in
   `app/static/logos/` (SVG or PNG; shown in file-name order in the footer and
   under the hero). Container path is `/app/app/static/logos/`.
5. **People and publications** are data in `app/config.py` (`PEOPLE`,
   `PUBLICATIONS`): fill in the e-mail addresses there.

---

## 11. Open questions

Carried over: image-server egress (1), ESTC export for DI-/FT-only books (2),
publisher/printer pooling (3), lending thresholds (4), cluster score
placeholder (5), FT annotation status (6), TP IoU threshold (7), agent
disambiguation (8).

New in v2.1 — answers needed before the code can be finished:

9. **The checkpoint**: which file — Stage I `MoCoMulti` or Stage II
   `MoCoSupervised`, and trained on which types? The loader handles both; the
   choice decides what "similar" means.
10. **Export sizes**: confirm the input size per type used in
    `export_embeddings.py` (proposed HP 100×400, others 200×200, no
    normalisation). Whatever is used is recorded in `embedding_model` and
    reused for queries.
11. **Places file**: which columns does the ESTC export have (`ESTCID` +
    `place`?), and is the place a free string ("London : printed for T. Cox")
    or already a city? The normaliser copes with both but a sample helps.
12. **Thresholds** for §12: place contrast 0.6, 5 books minimum; reprint pairs
    3 shared plates on plates in ≤ 30 books; cluster link 0.85. All are
    parameters on the pages; these are starting values.
13. **E-mail addresses** for the people section, and whether Enes
    Yılandiloğlu (DHQ co-author) should be listed.
14. **The DHQ record** spells the second author "Pivovatova"; the site uses
    "Pivovarova" (as in the SCIA paper). Correct?

---

## 12. Places of publication, reprints and copies (v2.1)

The DHQ paper's argument — variants of one design appearing in London and in
Dublin, Dublin reprints that reuse or copy London ornaments — needs one more
column: where each book was printed. Everything in this section **degrades
gracefully**: if no places file has been loaded, the panels say "No place
data has been loaded" and nothing else changes.

### 12.1 Data

`book.place` (the string as given) and `book.place_key` (a normalised city
key). Loaded with

```
python -m etl.load --places places.csv
```

The file is the ESTC place export (columns `estc_id, publication_place,
publication_country, false_imprint, org_260_a, …, longitude, latitude`);
columns are found loosely by name, so a three-column extract also works.
`publication_place` is already the resolved city, so the normaliser only
lower-cases it and strips brackets and `?`; `org_260_a` (what the title page
says, e.g. `Londres [i.e. Paris] :`) is kept as `imprint_place`;
`false_imprint` becomes a boolean shown as a **false imprint** tag on the book
and ornament pages, with the claimed place in its hover text; `country`,
`lat`, `lon` are stored for a later map. Rows with an empty place (e.g. `The
Hague?` books, which the export leaves blank) get no place. `--places` never
overwrites a place already loaded; `--replace-places` does.

### 12.2 Where places are shown

- Book list, book page, ornament page (Book section): "Place: Dublin".
- `/books`: a place filter (the 12 most frequent places).
- Class page: a third panel next to Publishers and Printers, **"Where it was
  printed"**, coverage per place over the class's books with a known place,
  same bar style (§4.1); each place links to the book list filtered by it.

### 12.3 Places by design — spotting copies

On a **superclass** page (and on a linked-cluster group, §12.6): a table with
one row per subclass (or variant/cluster) and one column per place, cells as
coverage percentages; below it, the pairs whose place profiles differ most.
For two designs A and B with at least `min_books` books each (default 5), the
contrast is the total-variation distance

```
d(A, B) = ½ Σ_place | p_A(place) − p_B(place) |        0 = same mix, 1 = disjoint
```

Pairs with `d ≥ 0.6` are flagged **"printed in different places — possibly a
copy"** and shown with the two exemplars side by side (the C002_01 / C002_02
case of the paper: London birds facing out, Dublin birds facing in). Both
numbers are query parameters (`?place_min=5&place_d=0.6`) with defaults in
`config.PLACE_DEFAULTS`. This is the ornament-side counterpart of lending
(§4.5): lending asks *who* used one block; this asks *where* two blocks of one
design were used.

### 12.4 Book pairs — reprints and suspicious editions

Two books that share many plates but were printed in different places are the
book-side signal (the paper's *A New System of Agriculture*, London 1726 /
Dublin 1727). `mv_book_pair` holds, for every pair of books that share at
least 3 plates, the shared count, the Jaccard index over their plate sets, and
whether their places differ. To keep the pair count bounded, only plates found
in ≤ 30 books generate pairs (a plate in 1,000 books would alone generate
500,000 pairs and says nothing about a specific relationship). Shown:

- on the book page, a table "Shares several plates with": the other book's
  **title** (linked; the ECCO id small underneath), year, place, shared plates,
  Jaccard and the tag; the "similar books" list gains the place and a
  **different place** tag;
- `/reprints`: the pairs, different-place pairs first, sorted by shared plates;
  each row shows both titles, years, places and the shared plates as
  thumbnails; the CSV download has the same columns.

### 12.5 Human classes versus machine clusters

For human sources a superclass already says which designs belong together.
For machine sources nothing does, so §12.3 needs a way to say that clusters
`HP-8944` and `HP-10231` are "the same design".

### 12.6 Cluster links (stored)

```
python -m etl.link_clusters --min-sim 0.85 [--src HP-pred]
```

computes the cosine similarity between all centroid pairs of one source
(61,640 HP centroids × 384 dims: about 30 s in blocks of 2,000 rows) and
stores every pair at or above the threshold in
`cluster_link(src, a, b, sim, n_a, n_b, computed_at)`. Then:

- a cluster page gets a **"Related clusters"** section (like the sibling
  variants on a subclass page), with the similarity;
- the connected components of the link graph act as pseudo-superclasses for
  the place contrast of §12.3, reachable from any member cluster;
- links are data, not recomputed per request; rerun the command after loading
  new embeddings. An admin can reject a link in the workbench (a
  `rejected` flag on the row) — a stored decision that survives reruns.

The threshold is the one quantity here that must be chosen from the data: the
command prints the distribution of the best similarity per cluster, and the
class page shows the similarity on each link, so wrong links are visible.

**Implemented 2026-09-18** (all of §12): `--places`; place on book, ornament
and class pages; "Places by design" with sliders on superclass, subclass and
linked-cluster pages; `mv_book_pair`, `/reprints`, `/reprints.csv`, pair rows
and a *different place* tag on book pages; `etl.link_clusters`, "Related
clusters" with admin *Not the same* / *Restore*; counts on the admin page.
Every part shows a "no place data" sentence when `--places` has not run.

---

## 13. Front page scope, glossary, publications, people (v2.1)

### 13.1 Scope sentence

Current: "464,352 ornaments in 77,848 books printed 1616–1839, from 4,861
publishers and printers." True but it hides the scale. Proposed lead:

> Every page of Eighteenth Century Collections Online — some 200,000 books and
> 33 million pages, the most complete collection of eighteenth-century printing
> in English — was searched for printers' ornaments. This atlas holds what the
> detection and clustering kept: 464,352 ornaments in 77,848 books printed
> 1616–1839, from 4,861 publishers and printers. Human annotation covers a
> checked subset; machine clusters cover the rest, and the two are kept apart
> throughout.

The numbers in the second sentence come from the database; the first sentence
is fixed text (`config.SCOPE_SENTENCE`) so it can be reworded without a code
change. "What the detection kept" is the honest qualifier: narrow rules, tiny
clusters and illustrations were left out (§9 of the DHQ paper).

### 13.2 Glossary and tooltips

A small `?` circle after any term that needs it (type codes, "plate", "coverage
share", "credit share", "owner", "borrower", "cluster", "superclass",
"contrast"). Hover or keyboard focus opens a short definition; the circle links
to the full entry on `/about`. Implemented as a `help(term)` macro reading
`config.GLOSSARY`; no JavaScript needed (CSS `:hover`/`:focus-within`), so it
works on the sortable tables and inside panels.

`/about` carries the full definitions supplied by the annotation guideline —
printers' ornaments (device PD, headpiece HP, tailpiece TP; "border" and
"other printer's ornament" are not listed, since neither is a type in the
atlas; the device entry says that devices and tailpieces form one type, TP),
illustrations (woodcut/engraving, frontispiece, other), initials (decorative
DI, factotum FT), library stamps — and states the assumption the whole site
rests on: **an ornament block was a physical asset of a printing or publishing
house, added by the printer; illustrations belong to the book, not to the
house, and are therefore not in the atlas.** This is also why the WE
(woodcut/illustration) data was dropped in v2.1.

### 13.3 Publications and people

Front page, a band **"The research behind the atlas"** under *Explore*: two
publication cards (authors, title, venue, year; links to the paper and DOI)
each with a **Cite** button that copies the BibTeX to the clipboard and briefly
shows "Copied" (or reveals the text for manual copying when the browser refuses
clipboard access); below, the group line and links to the findings and the
people. **People are deliberately not on the front page**: they sit in the
footer behind a "Contributors" toggle (a `<details>` element) on every page,
and in full on `/about#people` — Ruilin Wang, Lidia Pivovarova, Yann Ryan,
Mikko Tolonen, each with a mail link when an address is given. `/about` also
carries **"What the data has shown so far"**: five findings of the DHQ paper in
its own hedged register (incomplete printer catalogues; Bowyer → Faulkner;
Tonson–Watts variants in Dublin; Dublin reprints rarely copying images;
caveats), each linking to the page of the atlas that follows it up. All of it
is data in `config.PUBLICATIONS` and `config.PEOPLE`.

BibTeX as given by the university research portal; the DHQ entry has no DOI
in the record, the SCIA entry has `10.1007/978-3-031-95911-0_28`.
