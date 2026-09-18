# ECCO Ornament Atlas — Design Document

Version 2.0 · supersedes v1.0. Phase 1 now covers **all seven data sources**:
three human-annotation tables and four machine-prediction tables.

---

## 0. What changed from v1, and why

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
| `WE-pred` | `WETPPD.csv` | WE | machine, SimCLR | — | `HC0.015` | cluster |

`WE` covers woodcut engravings, tailpieces and printer's devices as one kind.
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
   (same kind first, else a `WE` row) → attach, and record the annotation's own
   key in `ornament_alias` so a link built from the annotation CSV still resolves;
3. otherwise → insert a new ornament that has a human label and no cluster.

The loader prints the three counts, so a drift between the annotation and
prediction boxes shows up immediately.

### 3.4 De-duplication of `WETPPD.csv`

The WE file contains many detections that are also in the HP (and other) files.
The loader applies your procedure, in order, after the other prediction files
are in the database:

1. drop exact duplicates **within** the file (`id + boxes`, keep first);
2. drop WE rows whose key already exists in **another** source;
3. drop WE rows with **IoU ≥ 0.9** against any other ornament on the same page
   (`--we-iou`, default 0.9).

Before step 3 it prints the IoU histogram (bins 0 / .5 / .8 / .9 / .95 / .99 / 1)
so the threshold can be chosen from the data. Across the non-WE files, a key
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

Unchanged behaviour (substring first, trigram fallback; filters for ornament
type and Tonson/Watts), faster (counts from `mv_book`, all strips in one query),
and:

- hover card shows the ornament's label (`C067_01a` / `DI-8944`) as well as the
  page;
- pager with previous / next and a window around the current page.

**Page strip** (kept): four rows DI / FT / HP / WE, colours
DI `#e8833a`, FT `#e3c41e`, HP `#4a9d5f`, WE `#3d7ea6`; a count above a dot when a
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
Machine prediction DI clusters (HC0.12) | FT clusters | HP clusters | WE clusters
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
[x] HP prediction  [x] DI prediction  [x] FT prediction  [x] WE prediction
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
  in the machine cluster. Drop marks `cluster_rejected`. WE clusters have no
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
GET  /books?q=&kind=&tonson=&p=               search
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

Other fixes: composition bar no longer overflows (credit share); report form
no longer shows current values as greyed placeholders that look like input;
long class names wrap at underscores instead of mid-word; visible keyboard focus
on every control; all multi-column layouts collapse below 980 px.

---

## 9. Phase 2 — model inference and embedding similarity

Unchanged decisions from v1: bulk embedding on LUMI (GPU), query-time embedding
of one uploaded image on Rahti CPU, **pgvector** in Postgres for search, the
embedder as a separate deployment so the web image stays small.

What v2 adds for it:
- the ornament page already has the method switch (embedding shown disabled);
- `db.similar_ornaments(oid, method)` is the only function to change;
- the Postgres deployment runs the stock `postgresql:15-el8`; the pgvector
  image in `deploy/postgres-pgvector/` must be rebuilt for **15** before the
  switch (it was written for 16). Switching before loading embeddings is the
  cheap moment.

When embeddings arrive, the cluster-versus-human view on class pages (§5.6) is
where model quality becomes visible to the team.

---

## 10. Deploying this version

1. **Rebuild the web image** the same way as before. No Containerfile change.
2. **Reset and load** (the schema changed; the current database holds only
   partial HP data):
   ```
   python -m etl.load --reset \
       --src HP-pred /tmp/data/HP.csv   --src DI-pred /tmp/data/DI.csv \
       --src FT-pred /tmp/data/FT.csv   --src WE-pred /tmp/data/WETPPD.csv \
       --src HP-ann  /tmp/data/HP_concat_annotation.csv \
       --src DI-ann  /tmp/data/DI_annotation.csv
   python -m etl.fetch_crops          # optional, fills crop_store
   ```
   `--reset` refuses to drop a database that holds reports or label changes
   unless `--force-reset` is given.
3. **Environment variables**: none required. Optional, in the `ecco-atlas`
   Deployment's environment settings:
   - `ADMIN_USERS` = `alice:pw1,bob:pw2` for personal admin passwords;
   - `CACHE_MAX_ITEMS` (default 1000);
   - `HERO_CREDIT` to change the caption on the front-page engraving.
4. **Logos**: put the official University of Helsinki and COMHIS files in
   `app/static/logos/` (SVG or PNG; shown in file-name order in the footer and
   under the hero) and rebuild. No code change.

---

## 11. Open questions

1. **Image server access** from the Rahti egress IP (unchanged).
2. **Book metadata for DI-/FT-only books** — is there an ESTC export with year
   and imprint we can load with `--books`?
3. **Pooling publishers and printers** for ownership (§4.1). Should ownership be
   computed on printers only for DI/FT, where the block owner is more plausibly
   the printer?
4. **Lending thresholds** — 0.70 / 0.05–0.20 / 5 books are starting values. The
   `/lending` page is the instrument for choosing them; the chosen values
   should go into `config.LENDING_DEFAULTS`.
5. **Cluster score** 0.95 for "same cluster" is a placeholder with no meaning
   beyond ordering.
6. **FT annotation** (85–90% accuracy) — load now as `FT-ann` with a warning in
   its status line, or wait for the review?
7. **WE IoU threshold** — choose from the histogram the loader prints.
8. **Agent disambiguation** — surname keys merge different people; an ESTC
   person authority list would fix this.
