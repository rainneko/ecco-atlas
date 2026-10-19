# ECCO Ornament Atlas — Design Document

Version 2.5 · supersedes v2.4. v2.0 covered all seven data sources; v2.1 added
**image search** (§9), **places of publication and reprint analysis** (§12),
the front-page scope, glossary, publications and people (§13), a fix to the
book-search cost (§4.7), and small layout changes (§5.2). v2.5 changes the
**wording of the imprint analysis** (the former "lending", §4.5), protects the
**image server** (§6), makes the site run without embeddings (§9.0), and fixes
a list of bugs (§14).

---

## 0. What changed, and why

### 0.0 v2.4 → v2.5 (September 2026)

| Request | Decision |
|---|---|
| The "lending" pages claim more than the data can show; the DHQ paper argues against lending, and the usual explanation of a block under another name is a **trade publisher** or the printer working for another bookseller | The analysis stays, the claims go. "Suspected lending" → **Unusual imprints**; *owner* → **main house**; *borrower* → **other house**; *Plates owned* → **Main house of**; verdicts become descriptive time positions (*Within its years*, *After its years*…); the page lists the possible explanations with trade publishers first; known trade publishers are flagged (`config.TRADE_PUBLISHERS`) and can be hidden; admin review records an explanation instead of confirm/reject (§4.5, §5.8). |
| Front page and About say blocks "belonged to" houses and "changed hands" | Reworded: the atlas shows under whose **imprints** a block appears; the block itself usually belonged to the printer, who is often not named (§13.2). |
| The colleague's image server receives too many requests | Every 300 px thumbnail is kept in Postgres (`thumb_store`), so a crop is fetched from the image server at most once, surviving restarts and deploys; page downloads are limited per worker (parallel requests, requests per second), concurrent requests for one page are merged, and a busy server (429/5xx) pauses all fetching; the image routes run on their own small thread pool so waiting images never block HTML pages; `robots.txt` keeps crawlers off images and query variants; heavy pages send fewer images (§6). |
| Publisher bar on class pages: the largest house disappeared among many small ones | The stacked credit bar is replaced by one bar per house (share of books), top 8 + "N more"; publishers and printers each use their **own denominator** (books that name a publisher / a printer) (§4.1, §5.6). |
| Lending and Places-by-design sections felt odd when unfolded | A **house × year strip** (§5.6) shows who used a class when; outlier books are hollow dots. Places by design becomes **one row per design** with a 100 % place bar (§12.3). Neither is folded any more. |
| Embedding files are not loaded yet; the site must not break | Every embedding feature checks for data and degrades to a sentence (§9.0). |
| Bugs found in review | §14. |

**Status of v2.5.** Everything in this document is in the code: `app/`, the
templates, and the four loader scripts in `etl/`. The package replaces whole
files; no file needs to be edited by hand (§10).

### 0.1 v2.0 → v2.1 (September 2026)

| Request | Decision |
|---|---|
| Book search waits several seconds | Drop the whole-title trigram *similarity* branch; keep the indexed substring match and add a vocabulary-based "did you mean" for typos. 2 queries × ~2.3 s → 1 query × ~5 ms on 80k titles (§4.7). |
| Upload an ornament and find its class and house | New `/search/image` (§9): model checkpoint stored in Postgres, embeddings loaded from four per-type files, CPU inference, a four-stage progress bar, kind confirmation, cluster ranking, house estimate. |
| Places of publication | `book.place` from an ESTC export loaded with `--places`; place composition on class pages; subclass-versus-subclass place contrast; book-pair reprint list; cluster links from embeddings (§12). Every place feature degrades to "no place data" when the file was never loaded. |
| Front page too modest; glossary; publications; people | §13. |
| v2.1.1 (2026-09-20): header, `?` icon, About text, book-pair titles, file upload | §8 header, §9.4 status, §10 upload steps, §12.4, §13.2. |
| v2.2 (2026-09-20): place pie; "what does Dublin's version look like?"; compare two sets of ornaments | §12.2 pie and click menu, §12.3 empty cases, §12.7 `/compare`, §12.8 filtered class view. |
| v2.2: About page hard to navigate; `?` texts unclear | §13.2 sidebar, one anchor per glossary term, rewritten texts. |
| v2.3 (2026-09-20): "where did this design travel, and when?" | §12.9 map through time on every class page. |
| v2.3: shared plates on /reprints should compare the two books; compare images too small; admin login unclear | §12.4, §12.7.1 book filter, §12.7.2 cell layout, §10 admin login. |
| v2.4 (2026-09-20): too many top-level links; class pages too long | §8 navigation with two menus; §12.10 folded sections on class pages (replaced in v2.5). |
| Imprint names clickable; sortable book list | §5.2. |

### 0.2 v1 → v2.0

| v1 assumption | Reality | v2 decision |
|---|---|---|
| The data is "DI, FT, HP" | The data is **annotation** (human, verified) **and prediction** (machine clusters), per ornament type | Seven named sources (§3.1). Every ornament can carry a human label *and* a machine cluster at the same time. |
| `superclass/subclass/variant` are global columns | They only exist in annotation files; prediction files have one `HC*` cluster column | Classes are always addressed as **(source, level, value)** — `HP-ann / variant / C067/C067_01/a`, `DI-pred / cluster / 8944`. |
| An unlabelled ornament is "(unclassified)" | It has a machine cluster | It is shown as **`DI-8944`**. |
| DI annotation has subclasses | Its subclass column is not meaningful | DI and FT annotation expose **superclass only**. |
| Annotation rows are extra rows | `DI_annotation.csv` ⊂ `DI.csv` (same detections) | Annotation rows **attach to** the existing prediction row with the same `id + boxes` (exact key, then IoU ≥ 0.9 fallback). |
| Publisher share = "books with this name / books" drawn as a stacked bar | Books name several houses, so shares sum to more than 100% and the bar overflowed | Two named measures (§4.1). In v2.5 the class page draws only coverage. |
| Agent key = "surname" | `J. and R. Tonson` produced the key `j r tonson` | Surname extraction rewritten and tested (§3.7). |
| Crops cached up to N MB | Annotated crops are the core evidence and must not depend on the image server | Annotated crops stored **in Postgres**; everything else in a **1,000-item LRU** disk cache (§6); since v2.5 all thumbnails also in Postgres. |
| Admin = one password, report queue | Needs attribution, side-by-side comparison, and bulk relabelling | Named admin sessions, a **comparison** queue, and an **annotation workbench** (§5.9). |

New in v2: plates by main house, Tonson/Watts plate badge, co-holding recommendations,
publisher-pair page, source checkboxes on every analysis page, the imprint analysis,
formal HP names (Group / Subgroup), sortable class lists, and a front page with an image.

---

## 1. Audiences

**The supervisor** — "what ornaments are in this book?" from a half-remembered
title, in under a minute. → Books search and the page strip (§5.2).

**The team** — compare the variants of one design, see under which houses'
imprints each block appears, find imprints that break the pattern, correct
labels. → Class pages, publisher pages, unusual imprints, workbench.

**Everyone else** — a public, citable project page. Plain, fast, readable,
closer to a conference project page than a product site. It must not claim
more than the research papers do (§4.5).

### Non-goals for this version
- No per-user accounts for the public. Reports are anonymous-with-optional-name.
- No editing of book metadata in the UI.
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

**The image server belongs to a colleague.** It is not ours to load freely:
every page download is a full-page image of 1–3 MB. §6 lists what the atlas
does to keep its share small.

**v2.5 needs no new environment variables.** Optional ones are listed in §10.

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

Because the embedding export for TP is made from the raw `TPPD.csv`, its npz
contains the same duplicates; `etl.load_embeddings` keeps one vector per
ornament and only matches rows to ornaments of the file's own kind (v2.5;
before, a duplicate oid made the COPY fail).

### 3.5 Two labels per ornament, one vote in analysis

```
ornament ── machine label: pred_src, hc_column, hc_cluster, cluster_rejected
         └─ human label:   ann_src, superclass, subclass, variant, class_path
```

Every page that *shows* a class uses its own membership: the cluster page
`DI-8944` shows every ornament the model put there, including annotated ones.

Every page that *counts* (publisher analysis, book similarity, imprint analysis) uses a
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
judgment stored beside it. Since v2.5 the change log also records **which**
cluster was rejected (`label_change.rej_pred_src`, `rej_hc_cluster`). The
loader's replay applies a rejection only when the ornament is still in that
cluster, and prints how many it skipped; older rows without the columns replay
as before (§3.8).

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
just the superclass. DI/FT annotation files carry a raw `subclass` code that is
kept in the column for grouping only; no page treats it as a class level
(v2.5 fix, §14).

**Plate** = the level we believe corresponds to a physical block: HP variant,
or HP subclass when it has no variants; DI/FT superclass; any machine cluster.
On the site the word *block* is preferred in running text; "plate" remains the
technical name in the code and URLs.

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
thumb_store     oid → 300 px JPEG                      (every viewed crop, v2.5, §6)
report          open / accepted / rejected suggestions
label_change    every human relabel, drop or cluster rejection (history);
                v2.5: rej_pred_src, rej_hc_cluster
lending_review  admin notes on unusual-imprint cases (table name kept)
upload          [FUTURE] query images
meta            schema_version, refresh status
```

`thumb_store` and the two `label_change` columns are created by the web app at
start-up and by the loader (`CREATE TABLE / ADD COLUMN IF NOT EXISTS`), so
`schema.sql` does not need editing and no reset is needed.

Derived (materialized views, rebuilt after a load and refreshed in the
background after admin edits):

```
mv_book          n ornaments per book
mv_book_plate    distinct (book, plate)
mv_plate_agent   per plate × agent: n_books, n_known, share (coverage), other_max
mv_plate         per plate: size, years, exemplar, n_agents, top_share, top agents,
                 t_owned
mv_agent_src     per agent × source: plates used, plates where main house
mv_class         per (source, level, value): n, n_books, years, exemplar, plate_key
mv_source        per source: ornaments, books
```

`label_change` is what makes review work survive a data reload: after every
load, the latest change per ornament is replayed.

**Rebuild locks the site.** `derived.rebuild` drops and recreates the views in
one transaction; while it runs, page queries wait. Run loads outside working
hours. (A `REFRESH … CONCURRENTLY` path for loads that do not change the schema
is an open item, §11.)

**Added in v2.1** (all created by `schema.sql` on the next load, no reset):
`book.place`, `book.place_key`; `book_word` (title vocabulary for typo
fallback); `model_store`, `embedding_model`, `ornament_embedding`,
`cluster_centroid`, `kind_sample` (§9); `cluster_link` (§12.6);
`mv_book_pair` (§12.4).

---

## 4. Analysis definitions

### 4.1 Two share measures

For a set of books (a class or a plate), counting only books whose imprint names
at least one house (`n_known`):

- **coverage(agent)** = books naming the agent ÷ `n_known`.
  A book "printed for Tonson and Draper" counts for both, so coverages can sum
  to more than 100%. This is the measure the main-house definition uses
  ("named in > 70% of the books").
- **credit share(agent)** = the agent's book count ÷ the sum of all agents'
  book counts. Sums to 100%. No longer drawn (v2.5): in classes whose books
  name five or six booksellers each, it hid the largest house.

**On class pages (v2.5)** the publisher and printer panels each use their own
denominator: *books that name a publisher* and *books that name a printer*.
Most books name no printer; with the pooled denominator a printer named on
every book that names any printer appeared as 12 %. The main-house and
unusual-imprint analysis still **pool** the two roles, because the block was
usually the printer's while the imprint names the bookseller first — pooling
avoids deciding that per book.

### 4.2 Blocks used, main house of

- **Blocks used** by a house: every plate in any of its books.
- **Main house of**: plates where the house's coverage is **strictly greater**
  than every other house's, and the plate has at least 3 imprint-known books
  (`OWNER_MIN_BOOKS`). A toggle also admits ties for the largest share —
  common when two booksellers appear together on every book.
- This is an **association in imprints**, not ownership. v2.0 called it
  "plates owned"; the name was dropped in v2.5 (§4.5).
- **Tonson/Watts badge** (solid **T**): Tonson or Watts are the house named
  most often in the plate's books (sole or tied), with ≥ 3 known books.
  Distinct from the outlined **T** that marks a *book* whose imprint names them.

### 4.3 Houses with similar stock (kept from v1)

Cosine over idf-weighted plate sets, weight `1/ln(1 + number of houses using
the plate)`. Answers "who drew on the same stock of blocks".

### 4.4 Co-holders

For houses A and B and every plate both appear with at coverage ≥ 20%
(`min_share`, adjustable) and ≥ 3 known books:

```
co-holding(A, B) = Σ over those plates of min(coverage_A, coverage_B)
```

Unlike 4.3 it ignores plates a house appears with only once; it rewards houses
that *together* account for the bulk of the same plates. Both lists link to the
pair page.

### 4.5 Unusual imprints (formerly "lending")

**Why the name changed (v2.5).** v2.0–2.4 called this analysis *lending* and
labelled rows "Lending". The inference behind it runs from **imprint names** to
**block ownership**, and that step is weak in this period:

- the block usually belonged to the **printer**, and the printer is often not
  named; the imprint names the bookseller(s);
- **trade publishers** (e.g. names in the Roberts / Dodd / Morphew group,
  to be checked against the literature) lent their names to books printed for
  others, so a block can appear under their name without their having anything
  to do with it;
- a printer working for several booksellers puts his blocks into all of their
  books;
- later sale of stock, reissues of old sheets under a new title page, copied
  (re-cut) blocks, false imprints and data errors produce the same pattern.

The DHQ paper does not argue for lending, and the site must not either. The
analysis is kept because the cases it finds are worth looking at; the site now
**describes the pattern and lists the explanations**, and never names one.

**The pattern.** For a plate with at least `min_books` imprint-known books:

- **main house(s)** = houses with coverage ≥ `owner_min` (default **0.70**).
  Usually one; if two houses co-sign most books, both are shown as
  "Tonson + Draper".
- **other houses** = every other house with coverage in `[lo, hi]`
  (default **0.05–0.20**).
- **cases** = an other house's books on this plate that name **none** of the
  main houses. A book co-signed by both is co-publishing and is excluded.
- `min_books` default **5**: below 5 books a single book is already ≥ 20%.

**Time position** (descriptive only). Each case is placed against the main
houses' own dated use of the plate, the span `[y0, y1]`:

| Book year | Shown as |
|---|---|
| inside the span | **Within its years** |
| outside by ≤ `gap` years (default 5) | **Close to its years** |
| more than `gap` after the last use | **After its years** |
| more than `gap` before the first use | **Before its years** |
| no year, or the main house has no dated book | **Undated** |

All time chips share one neutral colour; none reads as a verdict. The internal
keys (`lending`, `near`, `later`, `earlier`, `undated`) are unchanged so that
existing links and CSV consumers keep working.

**Trade publishers.** `config.TRADE_PUBLISHERS` maps a surname key to the
years in which that name acted as a trade publisher (`(first, last)`, `None`
for open). Because agent keys are surname-only, the years matter: "cooper" in
1790 is not the same person. Cases whose other house matches are tagged
**trade publisher?**, and `/lending?hide_trade=1` hides them; the number that
remains is the count of cases the known explanations do not cover. The list is
**to be filled in by the team from the literature**; it ships empty.

**Admin notes.** A reviewer records an *explanation* per case: trade publisher,
same printer, reissue / sale of stock, copied block, unexplained, or data
error. It is stored in `lending_review` (status `confirmed` + note for the first
five, `rejected` + note `error` for a data error), so no schema change was
needed and older reviews stay readable.

**Parameters** are URL parameters with sliders on the page — no thresholds are
hard-coded in SQL. Defaults live in `config.LENDING_DEFAULTS`. Computation is
live from `mv_plate_agent`, candidate plates pre-filtered in SQL.

**Public or team-only.** The page is public but carries the explanation text
at the top; if the supervisors prefer, set `UNUSUAL_IMPRINTS_PUBLIC=0` to show
it to logged-in admins only.

### 4.6 Ornament similarity

| Method | Used when | Score |
|---|---|---|
| Annotation hierarchy | the ornament has a human label | same variant 1.00 · subclass 0.98 (HP only) · superclass 0.90 |
| Machine cluster | no human label, or chosen | same cluster 0.95 (placeholder) |
| Embedding cosine | this ornament has a stored vector (§9) | cosine of stored vectors; the tab is hidden otherwise |

All numbers are in `config.SIM_SCORES`.

### 4.7 Book search cost (v2.1)

**Before.** `/books?q=fable of` ran two SQL queries (results, then a count),
each with the predicate `title_norm LIKE '%fable of%' OR title_norm % 'fable of'`.
The `%` (trigram similarity) branch is the problem: with the GIN trigram index
the planner builds a bitmap of every row sharing *any* trigram with the query,
which for a query containing ` of` is essentially every row, and then rechecks
each candidate by computing `similarity()` on a ~280-character title. Cost per
query is therefore **O(N · L)** — N = 77,848 titles, L ≈ 280 characters — twice.
Measured on 80,000 synthetic ECCO-length titles: 2.27 s + 2.33 s ≈ **4.6 s**.

**After.** One query, three parts:
1. **Substring match, indexed.** `LIKE '%w%'` for each query word (all words
   must occur; adjacent-phrase hits ranked first). Measured: **3 ms** for
   `fable of`, 28 ms for `history of england`, 0.2 ms for a miss.
2. **Count in the same query** (`count(*) OVER ()`): no second scan.
3. **Typo fallback on the vocabulary, not on titles.** When step 1 finds
   nothing, each query word is matched against `book_word` (similarity ≥ 0.5)
   and the page says "Showing results for *fable*". The pager then carries the
   corrected words, so page 2 of a corrected search is not empty (v2.5 fix).

v2.5: a query that looks like an ESTC number (`T089836`) or a 10-digit ECCO id
goes straight to the book.

`book_word` is rebuilt with the derived views (`derived.rebuild`).

---

## 5. Pages

Every analysis page carries the same **source checkboxes** (§5.7) where the
choice changes the result; the current choice is kept in the URL, so any view
can be bookmarked or sent to a colleague.

### 5.1 `/` — Front page

- **Hero**: a full-width band with the workshop image in black-and-white under a
  dark veil, white Caslon title, one sentence of purpose, and the book search.
  v2.5 sentence: "Which printers' ornaments appear in which eighteenth-century
  books, under whose imprints, and how that changed over time."
- **"Data in this atlas"**: a table, one row per source — family, ornaments,
  books, classes, and its status line (model, accuracy, review rounds).
- Entries: Books · Ornaments · Publishers & printers · Unusual imprints ·
  Book pairs · Image search.
- Ornament type legend with colours.

### 5.2 `/books` — Book search

Substring search on every query word (§4.7), typo fallback via the title
vocabulary, filters for ornament type, Tonson/Watts and — when loaded — place
of publication (§12). Counts come from the same query, all page strips from one
query.

- **Sortable columns**: Title, Year, Ornaments.
- **Imprint names are links** (chips to `/agent/{name}`); the raw ESTC string is
  the cell's hover title.
- Place of publication shown when known.

**Page strip** (kept): four rows DI / FT / HP / TP, colours
DI `#e8833a`, FT `#e3c41e`, HP `#4a9d5f`, TP `#3d7ea6`; a count above a dot when a
page carries more than one; dots are links; one shared lazy hover card.

### 5.3 `/book/{id}` — Book detail

Metadata, imprint with agent chips, strip, gallery. **Ten most similar books**
are computed over plates, ignoring plates found in more than
`SIMILAR_MAX_PLATE_BOOKS` (500) books — very common blocks made the query slow
and say nothing about two particular books (v2.5).

### 5.4 `/ornament/{oid}` — Ornament detail

Three columns: image; record (human label, machine cluster, book); most
similar (hierarchy / cluster / embedding when available). Below: the report
form, with a hidden honeypot field and a per-address limit (v2.5).

**Find similar** (v2.5) uses the ornament's **stored vector** when there is
one: no page download, no model run. Without a stored vector it falls back to
cutting the crop and embedding it, and is hidden when the model is not loaded.

### 5.5 `/classes` — Browse classes

One selector with two groups (human annotation / machine prediction); the
filter box matches ids, formal names and, since v2.5, the displayed variant
label (`C067_01a`). `_` and `%` in the filter are taken literally. Sortable
columns: thumb · class · images · books · first year · last year · span.

### 5.6 `/class/{source}/{level}/{value}` — Class detail

1. **Header**: label, formal name, breadcrumb, key numbers, exemplar.
2. **Who used it** (v2.5):
   - **Publishers** and **printers**: one bar per house, bar length = share of
     books naming a publisher (resp. printer); top 8 and "N more houses, each
     in x % of books or fewer". Main house marked *main house*, other houses in
     the unusual-imprint band marked *other house*.
   - **Where it was printed**: the pie (§12.2).
   - **Houses through time**: one row per house (main house first, then by
     books, at most 6), x = year, one dot per book. The main house's dated use
     is a pale band. A **hollow dot** is a book that names neither main house.
     Under it, for plate-level classes, the list of those books with their
     time position and the *trade publisher?* tag; no verdict.
3. **Map through time** (§12.9), opened on demand.
4. **Related clusters** (cluster pages, when links exist).
5. **Places by design** (§12.3), no longer folded.
6. **Cross-reference** human labels ↔ clusters.
7. **Structure**: sub-designs / sibling variants; sidebar with other subclasses.
8. **All images**, by year or grouped.

### 5.7 Publishers & printers

**Source checkboxes** (on list, detail and pair pages), as in v2.0.

**`/agents`** — columns books, blocks used, main house of.

**`/agent/{name}`** — tabs **Blocks used** / **Main house of** (+ ties); the
second tab explains that this is an association in imprints, not ownership;
**Houses with similar stock**, **Co-holders**; summary "main house in N
unusual-imprint cases, other house in M".

**`/agents/pair/{a}/{b}`** — shared blocks with both coverages, ornaments in
books that name both.

### 5.8 `/lending` — Unusual imprints

(URL kept for existing links.) Sliders for main-house share, other-house band,
minimum books and year gap; source checkboxes; filters `agent=`, `plate=`;
**hide known trade publishers** (`hide_trade=1`). The page opens with the
explanation paragraph of §4.5. One row per case: plate · main house(s) ·
other house (+ *trade publisher?*) · book · year · main house's years · time
position. Admins record an explanation per row. `/lending.csv` with the same
parameters, plus a `trade_publisher` column.

### 5.9 Admin

Unchanged from v2.0 (login, dashboard, comparison queue, workbench), except:
workbench cards use 300 px crops (the cards are 190 px high; 900 px images for
400 cards meant 400 page downloads of the large size); the login compares
passwords in constant time and slows down after repeated failures from one
address.

---

## 6. Images

| Kind of crop | Where it lives | Why |
|---|---|---|
| **Annotated**, full size | **Postgres `crop_store`**: native crop (≤ 1400 px) + 300 px thumb | The evidence the project rests on; must not depend on the image server. |
| **Any crop at 300 px** (v2.5) | **Postgres `thumb_store`** | Lists and galleries show 300 px crops; kept for good, surviving restarts and deploys. 10–30 KB each; all 464k would be ≈ 10 GB, in practice only viewed ones are stored. |
| 900 / 1400 px of non-annotated, 900 px of annotated | LRU disk cache, `CACHE_MAX_ITEMS` (default 1000) | Only what people look at closely. |

**Protecting the image server (v2.5).** One cache miss = one full page
download from a colleague's server. Before v2.5 a cold `/classes` page meant
100 page downloads, `/reprints` 300, and a redeploy emptied the cache.

- **Limits per web worker**: at most `IMAGE_MAX_PARALLEL` (3) downloads at a
  time and `IMAGE_MAX_RPS` (3) per second. The httpx pool has the same size.
- **Merged requests**: when two requests need the same page, the second waits
  for the first; the last 8 pages are kept in memory, so several ornaments of
  one page cost one download.
- **Circuit breaker**: a 429 or 5xx pauses all downloads for `Retry-After`
  (default 30 s). A failed page is not retried for 60 s.
- **Own thread pool**: image routes run on a pool of 6 threads; after 20 s a
  placeholder is returned (not cached) and the download finishes in the
  background. HTML pages never wait for images.
- **One download, all sizes**: whatever width was asked, the 300 px thumb is
  stored too.
- **robots.txt** disallows `/img/`, `/api/`, `/search/`, `/compare`, `/admin`,
  `/lending`, `/reprints` and sorted variants.
- **Lighter pages**: book pairs show 3 shared blocks per pair; the workbench
  uses 300 px.
- **Bulk jobs** (`etl.fetch_crops`) use the same limits; run them at night with
  `IMAGE_MAX_RPS=1`, and tell the colleague first.
- Responses under `/img/` are not gzip-compressed (JPEG does not shrink).

**Open question for the colleague:** can the server return a region at a
given size (IIIF-style `/region/size`)? That would replace full-page downloads
by small crops.

Failures return a grey SVG placeholder and are not cached.

---

## 7. URL map

```
GET  /                                        front page
GET  /robots.txt                              crawler rules (v2.5)
GET  /books?q=&kind=&tonson=&place=&sort=&dir=&p=  search
GET  /book/{book_id}?src=                     book detail
GET  /ornament/{oid}?sim=hier|cluster|embed   ornament detail
POST /ornament/{oid}/report                   suggest a correction
GET  /classes?tax=SRC:LEVEL&q=&sort=&dir=&p=  browse classes
GET  /class/{src}/{level}/{value}?order=&p=   class detail
GET  /agents?q=&src=&sort=                    publishers & printers
GET  /agent/{name}?src=&view=used|owned&joint= house detail
GET  /agents/pair/{a}/{b}?src=&p=             two houses
GET  /lending?owner=&lo=&hi=&min_books=&gap=&src=&agent=&plate=&hide_trade=
GET  /lending.csv?…                           same, as CSV
GET  /reprints?p=  /reprints.csv              book pairs sharing plates (§12.4)
GET  /about                                   glossary, method, publications, people
GET  /compare?a=&a_place=&a_agent=&a_role=&b=…  compare two sets of ornaments
GET  /api/classes/suggest?q=                  class picker suggestions
GET  /api/class/facets?c=SRC:LEVEL:VALUE      places and houses in a class
GET  /api/class/geo?c=SRC:LEVEL:VALUE         data for the map (§12.9)

GET  /search/image                            upload form (§9)
POST /api/image-search                        multipart upload → {token}
GET  /api/image-search/status                 {model_loaded, index_ready, expected_ms}
POST /api/image-search/{token}/embed          → type scores
POST /api/image-search/{token}/match  {kind}  → classes, images, houses
GET  /search/image/{token}?kind=              result page, valid 24 h
POST /search/image/from/{oid}                 search with an existing ornament

GET  /admin/login  POST /admin/login  GET /admin/logout
GET  /admin  /admin/reports  /admin/workbench/{src}/{level}/{value}
POST /admin/report/{id}  /admin/workbench/save  /admin/lending/review
POST /admin/refresh  /admin/link

GET  /img/crop/{oid}.jpg?w=  /img/class/{src}/{level}/{value}.jpg
GET  /img/page/{oid}  /img/query/{token}.jpg
GET  /api/book/{id}/strip  /api/ornament/{oid}  /api/search/books  /api/docs
GET  /healthz
```

---

## 8. Visual design

The images are the content; the frame stays quiet.

| Token | Value | Use |
|---|---|---|
| paper | `#faf9f6` | background |
| ink | `#1c1a17` | text, hero veil |
| rule | `#e2ddd3` | hairlines |
| rubric | `#8a3324` | links, buttons, badges — the printer's red |
| muted | `#8d867b` | secondary text |

**Type.** Headings in **Libre Caslon Text**, served from `/static/fonts`. Body
text in the system sans.

**Header, navigation (v2.4).** Six top-level links; **Reprints ▾** holds
*Book pairs* and *Unusual imprints* (v2.5 name); **Compare ▾** holds *Compare
ornaments* and *Image search*. Menus open on hover, focus and a ▾ button.

**Charts (v2.5).** Houses are drawn as one bar per house (lengths do not add up
to a whole); places as a pie or a 100 % bar (a book has one place, so shares
partition the books). The same place colours are used by the pie, the design
rows and the map. v2.5 styles live in `static/v25.css`, loaded after
`style.css`.

Other rules unchanged: `?` circles keep their shape everywhere, visible
keyboard focus, layouts collapse below 980 px.

---

## 9. Image search (v2.1)

### 9.0 Running without embeddings (v2.5)

Embeddings are optional. With no model and no vectors loaded:

- the site starts normally; the engine retries in the background at most every
  30 s and reports "No embeddings have been loaded";
- `/search/image` shows that sentence as its status;
- the ornament page hides **Find similar** and the *Embedding* similarity tab;
- class pages have no *Related clusters* section (no links);
- the admin page shows the status.

The engine has two parts that load separately: the **index** (type samples,
centroids — needs only the vectors) and the **extractor** (needs torch and a
stored model). Searches from an existing ornament need only the index.

### 9.1–9.7

Unchanged from v2.1 (CPU inference, model stored in Postgres, npz files per
type, pipeline, progress bar, page layout, research-repository script), with
these v2.5 corrections:

- `etl.load_model` loads Lightning checkpoints with `weights_only=False`
  (torch ≥ 2.6 changed the default and refused them). The web app refuses a
  stored model whose keys do not match the backbone (before, `strict=False`
  silently produced a random network).
- Query images: transparent backgrounds are filled white and every query is
  converted to grayscale before the RGB repeat, as the training scans were.
  Uploads over 8 MB are refused while reading; images over 40 megapixels are
  refused before decoding.
- `etl.load_embeddings` keeps one vector per ornament and matches exact keys
  only within the file's kind (§3.4).
- `etl.link_clusters` computes similarities in blocks of 256 rows (was 2,000:
  ≈ 2 GB peak, enough to kill the web pod).

---

## 10. Deploying this version

1. **v2.4 → v2.5 needs no reset and no new variables.** Unzip the package in
   the repository root (it replaces whole files and adds `app/static/v25.css`
   and `CHANGES_v2.5.md`), commit and push. The web pod creates `thumb_store`
   and the `label_change` columns at start-up, and again before each admin
   edit; `etl.load` does the same after `schema.sql`. The site works without
   embeddings (§9.0).
2. **Optional environment variables**:
   - `IMAGE_MAX_PARALLEL` (3), `IMAGE_MAX_RPS` (3) — per worker;
   - `THUMB_STORE=0` to turn the thumbnail store off;
   - `UNUSUAL_IMPRINTS_PUBLIC=0` to show the imprint analysis to admins only;
   - `ADMIN_USERS`, `CACHE_MAX_ITEMS`, `HERO_CREDIT`, `EMBED_MODEL`,
     `EMBED_INDEX=full` as before.
3. **Trade publishers**: fill `TRADE_PUBLISHERS` in `app/config.py`.
4. **Embeddings, when available** (§9): load the model, then the four npz files,
   then `etl.link_clusters`. Getting files into the pod: `oc rsync` / `oc cp`
   to `/tmp/data`, one large file at a time, deleted after loading.
   With `WEB_CONCURRENCY=2` each worker holds its own model; if the pod is
   OOMKilled, set `WEB_CONCURRENCY=1`.
5. **Admin login**: any name and the value of `ADMIN_PASSWORD` on the web
   Deployment (`oc set env deployment/ecco-atlas --list | grep ADMIN`).
6. **Logos**, **people and publications**: as before.

---

## 11. Open questions

Carried over: image-server egress (1), ESTC export for DI-/FT-only books (2),
publisher/printer pooling (3), imprint-analysis thresholds (4), cluster score
placeholder (5), FT annotation status (6), TP IoU threshold (7), agent
disambiguation (8), checkpoint choice (9), export sizes (10), places file
columns (11), §12 thresholds (12), e-mail addresses (13), "Pivovarova" spelling (14).

New in v2.5:

15. **Trade publishers**: the names and years for `TRADE_PUBLISHERS`, from the
    literature (Treadwell's work on London trade publishers is the usual
    starting point).
16. **Public or team-only** for the unusual-imprint page.
17. **Image server**: does it support region/size requests? What request rate
    is acceptable to its owner?
18. **"plate" vs "block"** in the interface: book historians use *plate* for
    intaglio; the running text now says *block*.
19. **Concurrent refresh** after loads, so the site does not pause (§3.8).
20. **Class page tabs and an image lightbox** (proposed, not yet built).

---

## 12. Places of publication, reprints and copies (v2.1)

Everything in this section **degrades gracefully**: if no places file has been
loaded, the panels say "No place data has been loaded" and nothing else changes.

### 12.1 Data

`book.place`, `book.place_key`, loaded with `python -m etl.load --places places.csv`
from the ESTC place export (`estc_id, publication_place, publication_country,
false_imprint, org_260_a, …, longitude, latitude`). `false_imprint` becomes a
**false imprint** tag; `--places` never overwrites a loaded place,
`--replace-places` does.

v2.5: ESTC numbers are normalised on both sides before matching (upper case,
no spaces, no leading zeros after the letter: `T089836` = `T89836`). When no
book matches, the loader says so.

### 12.2 Where places are shown

Book list, book page, ornament page; `/books` place filter; on class pages a
**pie** of books by place (a book has one place, so the shares partition the
books), with the click menu of §12.7.4.

### 12.3 Places by design — spotting copies

On a superclass or subclass page of a source with those levels (HP
annotation), and on linked-cluster groups: **one row per design** (v2.5) —
exemplar, label, a 100 % bar of the places its books were printed in, the top
place and the number of placed books. Designs with fewer than `min_books`
placed books are dimmed.

Below, the pairs whose place mix differs most (total-variation distance
`d(A, B) = ½ Σ |p_A − p_B|`, shown in the tooltip), flagged at `d ≥ 0.6` as
"printed in different places — possibly a copy", each with **Compare side by
side**. Thresholds are behind a *Settings* toggle.

The section is shown only when it can say something: fewer than two designs →
not shown; no place known → one sentence; too few placed books → the rows are
shown, the pair list says so.

DI and FT annotation have no subclass level; their raw subclass codes never
appear as designs (v2.5 fix: they produced links that returned 404).

### 12.4 Book pairs — reprints and suspicious editions

`mv_book_pair` holds every pair of books sharing ≥ 3 plates found in ≤ 30 books,
with the shared count, Jaccard and whether places differ. `/reprints` lists them
(different places first), 3 shared-block thumbnails per pair (v2.5), each with
the click menu *Compare the two books on this plate*.

### 12.5–12.6 Cluster links

`python -m etl.link_clusters --min-sim 0.85` stores centroid pairs above the
threshold in `cluster_link`; cluster pages show **Related clusters**; admins can
mark *Not the same*. Needs embeddings; without them the section is absent.

### 12.7 Compare two sets of ornaments (`/compare`, v2.2)

Unchanged: sides defined by class + place + house (+ role, + book), `-x`
negation, aligned design table, images, click menus.

### 12.8 Filtered class view

`?place=`, `?agent=` (and `-` forms) filter the gallery; a chip shows the filter.

### 12.9 Map through time (v2.3)

Unchanged: coastlines only, circles ∝ books, arcs "next found here", slider,
cumulative or ±5-year window, table view, no third-party requests.

### 12.10 Class page sections (v2.5)

v2.4 folded *Lending* and *Places by design*; both were core content hidden
as appendices. v2.5 shows them open, in the new forms of §5.6 and §12.3.
Proposed next step: tabs (Images · Who used it · Where & when · Designs ·
Related), each loading only its own data (§11, question 20).

---

## 13. Front page scope, glossary, publications, people

### 13.1 Scope sentence

> Every page of Eighteenth Century Collections Online — some 200,000 books and
> 33 million pages — was searched for printers' ornaments. This atlas holds what
> the detection and clustering kept: N ornaments in N books …

### 13.2 Glossary, About and the assumption

`?` circles read `config.GLOSSARY`; each term has its own anchor on `/about`;
`tests/test_glossary.py` checks the anchors.

**v2.5 wording.** The About page's assumption now reads: an ornament block was
a physical asset, usually of the **printing** house; it was reused until it
wore out, was sold, inherited or copied. The atlas sees blocks through
**imprints**, which name booksellers first and often omit the printer, so every
statement about "a house's blocks" is a statement about imprints. Glossary
entries *owner* and *borrower* now define *main house* and *other house* and
say what they do not prove. The findings section no longer says the imprint
page "looks for" transfers; it lists candidate cases.

### 13.3 Publications and people

Unchanged.

---

## 14. Fixes in v2.5

| Where | Problem | Fix |
|---|---|---|
| Class page, related clusters | Labels showed "-10231": template used an undefined `kind` | `spec.kind` |
| Ornament page, *Embedding* tab | Always offered (the table always exists), always empty | Shown only when the ornament has a vector; implemented with stored vectors |
| DI/FT superclass pages | Raw subclass codes used as designs → links to `/class/DI-ann/subclass/2000` (404); hierarchy similarity said "same subclass" | Designs only for sources with that level; subclass match only for HP |
| Book search | Page 2 of a typo-corrected search was empty | Pager uses the corrected words |
| Class filter, compare picker | Variant labels (`C067_01a`) not found; `_` acted as wildcard | Search the displayed label; escape LIKE |
| Subclass page with no images after filtering | Breadcrumb and sidebar lost the superclass | Superclass derived from the value |
| Time position | "no year" shown when the *main house* had no dated book | Text says "no year to compare" |
| Replay after `--replace` | Old cluster rejection re-applied to the new cluster | Rejected cluster recorded; replay compares it |
| `load_embeddings` | Duplicate rows in `emb_TP.npz` → one oid twice → COPY failed | One vector per ornament; exact match within kind |
| `link_clusters` | ≈ 2 GB peak | Blocks of 256 (`--block`) |
| `load_model` | torch ≥ 2.6 refused Lightning checkpoints | `weights_only=False` |
| Web model | Wrong key prefix → random network, no error | Refuse on missing keys |
| Query images | Colour and transparent uploads differed from training scans | White background, grayscale |
| `--places` | ESTC number formats differed → silent 0 matches | Normalise both sides; warn when nothing matches |
| Admin redirects | `back=//other.site` accepted | Only same-site paths |
| Upload | Whole file read before the 8 MB check; huge pixel counts decoded | Bounded read; 40 MP limit |
| Login, report form | Unlimited attempts; spam | Constant-time compare, per-address slow-down; honeypot field + 10 reports per hour per address |
| Annotated 900 px crops | Re-encoded from 1400 px on every request | Cached |
| gzip | JPEGs compressed for nothing | `/img/` skipped |
| Similar books | Very slow for books with very common blocks | Plates in > 500 books ignored |
| Workbench | 400 cards × 900 px crops | 300 px |
| Search page | Upload form shown although no embeddings exist | Sentence instead of the form |
| Class page house bar | Largest house vanished among hundreds of 2 px segments; colours repeated every 8; printer shares used the pooled denominator | Bars per house, own denominators (§5.6) |
