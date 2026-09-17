# ECCO Ornament Atlas — Design Document

Version 1.0 · Phase 1 covers the DI / FT / HP annotation tables.

---

## 1. What this site is for

Three audiences, three different needs.

**The supervisor.** Wants to answer "what ornaments are in this book?" in under a
minute, starting from a half-remembered title. Needs: fuzzy title search → a table
where each row is a book → a visual index of which pages carry ornaments → click
through to the image. This drives the page-strip design in §5.2.

**The team.** Wants to compare the variants of one design, see who printed them,
and flag misclassifications. Needs: class pages at all three levels, publisher
composition, and a report → review loop.

**Everyone else.** A public project page that shows the work exists and is
serious. Plain, fast, readable — closer to a conference project page than a
product site. No carousels, no animation, one accent colour.

### Non-goals for Phase 1

- No user accounts. Reports are anonymous-with-optional-name; there is a single
  shared admin password.
- No editing of book metadata through the UI. Books come from the CSVs.
- No embedding similarity yet — see §9 for the plan and the hooks already in place.
- No IO (illustrations/other) data. `IO` is added to `KINDS` when it is ready.

---

## 2. Platform constraints that shaped the architecture

These are CSC Rahti (OpenShift 4) facts, and each one forced a decision:

| Constraint | Consequence |
|---|---|
| Containers run as a **random non-root UID** in **GID 0** | Every writable path must be `chgrp 0` + `chmod g=u`. Official Postgres/Docker images that assume root do not work; we use the OpenShift `postgresql` image or build our own. |
| Cannot bind ports below 1024 | App listens on **8080**. |
| **No GPU** on Rahti | Model inference must be CPU-only, and the bulk embedding job must run elsewhere (LUMI). See §9. |
| Container caps: **4 CPU, 16 GiB RAM**; limit/request ratio ≤ 5 | One modest web pod. No in-process 300k×512 float matrix. |
| Registry image limit **5 GB**; >1 GB is "very big" | A `torch` image is 2–3 GB. Keep the ML worker as a **separate deployment**, so the web image stays ~300 MB. |
| `standard-csi` is **ReadWriteOnce** | A PVC can only be mounted by one pod. The crop cache is RWO, so the web deployment stays at `replicas: 1` until the cache moves to object storage. |
| `*.2.rahtiapp.fi` gets free wildcard TLS | Use the auto-generated hostname; no cert work. |

**Architecture that follows from this:** a single small FastAPI container,
server-rendered HTML, Postgres beside it, and a disk cache for crops. No Node
build step, no SPA, no separate frontend deployment. The whole thing is one
image plus one database — which also happens to be the easiest thing to operate
for someone new to OpenShift.

### Why server-rendered rather than React

The heavy interactive element is the page strip, which is a few hundred absolutely
positioned dots. That is 60 lines of CSS and 40 lines of JS. A React toolchain
would add a build step, a second container or a static-asset route, and a
dependency tree — for no capability we need. The JSON API exists anyway
(`/api/...`), so if the team later wants a richer frontend, it can be bolted on
without touching the data layer.

---

## 3. Data model

### 3.1 The primary key problem

**An ornament is identified by `id + boxes`, not by `id`.** One page can carry
several ornaments, so `id` alone is not unique. `boxes` is a float list printed
with varying precision across files, so the raw string is not stable either.

Resolution, implemented in `app/normalize.py` and used identically by the loader
and the site:

```
image_id = id without ".TIF"                      # 15 digits
box_key  = "x1,y1,x2,y2" each rounded to 2 dp     # stable across files
oid      = sha1(image_id + "|" + box_key)[:16]    # short URL handle
```

`oid` is what appears in URLs. `(image_id, box_key)` is kept as a UNIQUE
constraint so the natural key stays visible and queryable, and so re-loading a
corrected CSV upserts rather than duplicates.

### 3.2 The image id is also the image URL

Verified against 100% of rows in the HP table: `id` minus `.TIF` equals
`docId + zero-padded page + "0"`, exactly what the image server wants.

```
https://oma-sivu.2.rahtiapp.fi/image/{image_id}
```

This has two useful consequences: page number and book id are **derived from the
id**, not trusted from the CSV columns (which have lost their leading zeros), and
no separate image-path column is needed.

### 3.3 The three-level class hierarchy

Both naming schemes in the HP file encode the third level as a trailing letter:

```
C067_01a                 ->  superclass C067                 / subclass C067_01 / variant a
angel_face_big_vine_01b  ->  superclass angel_face_big_vine  / subclass ..._01  / variant b
C101_01                  ->  superclass C101                 / subclass C101_01 / variant ''
```

A subclass with no trailing letter has `variant = ''`. `class_path` is the
slash-joined form, used as the key for the variant-level pages.

Interpretation, which matters for how the UI is worded: a **superclass** groups
designs sharing a motif; a **subclass** is one design; a **variant** is our best
guess at a *separate physical block* of that design. That is why the variant level
gets its own page and why publisher composition is shown there.

### 3.4 Agent normalization

Imprints are wildly unstable: `Tonson, J.`, `Tonson, Jacob` and
`J. and R. Tonson` are one business. Names are split on `;`, stripped of
boilerplate (`printed for`, `and`, `sold by`), and reduced to the **surname** as
the key; the most common surface form is kept for display.

This is deliberately lossy. It merges different people with the same surname, and
that is a known limitation to state on the site rather than hide. The alternative —
exact string matching — fragments one publisher into a dozen entries and makes
every composition chart meaningless.

`is_tonson` uses the house rule: `publishers` **or** `printers` containing
`tonson|watts`, case-insensitive.

### 3.5 Tables

```
book        book_id PK, estc_id, full_title, title_norm, year, total_pages,
            is_tonson, publishers_raw, printers_raw
agent       agent_id PK, name (normalized key), display_name
book_agent  (book_id, agent_id, role)   role ∈ {publisher, printer}
ornament    oid PK, image_id, box_key, x1..y2, kind, book_id, page,
            superclass, subclass, variant, class_path, label_src,
            hc_cluster, hc_column        UNIQUE(image_id, box_key)
report      report_id PK, oid, reporter, cur_*, sug_*, note, status, timestamps
label_change change_id PK, oid, report_id, old_*, new_*, changed_by, changed_at
upload      [FUTURE] uploaded query images
```

`hc_cluster` / `hc_column` exist from day one because the machine-prediction
tables carry `HC0.12`, `HC0.04`, `HC0.015` etc. — the column name encodes the
clustering threshold and differs per file, so we store both the value and which
column it came from.

`label_change` is the reason admin corrections survive a data reload: `etl/load.py`
replays the latest accepted change for each oid after upserting. Without it, every
CSV refresh would silently undo the team's review work.

### 3.6 Indexes

- `gin (title_norm gin_trgm_ops)` — fuzzy title search
- `(book_id, page)` — page strip
- `superclass`, `subclass`, `(subclass, variant)`, `kind`, `image_id`
- view `v_subclass_df` — document frequency per subclass, used by both
  similarity queries

---

## 4. Similarity (Phase 1)

All three are placeholders in the sense that the *numbers* are provisional, but
the *shape* is what Phase 2 will keep.

**Ornament → ornament.** Rule-based on the hierarchy:
same variant `1.00`, same subclass `0.98`, same superclass `0.90`.
Implemented as a single SQL `CASE`; swapping in `ORDER BY embedding <=> target`
later changes only the body of `db.similar_ornaments`.

**Book → book.** Cosine over the set of subclasses each book carries, weighted by
`1/ln(1+df)`. Rationale: two books sharing a subclass that appears in 500 books
tells you almost nothing; sharing one that appears in 4 books is strong evidence
of a shared printing shop. Normalizing by the geometric mean of the two books'
weights stops thick books from dominating.

**Agent → agent.** Same idf-cosine, but over plate (`class_path`) sets, and df is
counted over *agents* rather than books. A high score means "drew on the same
stock of blocks", which is the historically interesting relation — shared,
borrowed or inherited material rather than mere contemporaneity.

---

## 5. Pages

### 5.1 `/` — Front page

Purpose: say what this is in two sentences, then get out of the way.

- Title, one-paragraph description, a single large search box that goes to `/books`.
- A row of stat chips: ornaments, books, publishers/printers, superclasses,
  subclasses, how many have a variant, year range.
- Three cards linking to Books / Classes / Publishers.
- A legend of the four ornament types with their colours and counts.

### 5.2 `/books` — Book search (the supervisor's page)

**Search.** Substring match on a normalized title first (so `fable of` feels
exact), then trigram similarity as a fallback (so typos and word order still
work). Ordering: exact-substring hits, then trigram score, then ornament count.
Filters: ornament type, Tonson/Watts only.

**The table.** One row per book:

| Column | Content |
|---|---|
| Book | Title (link), year, ESTC id, ornament count, Tonson tag |
| Imprint | Publishers and printers, truncated |
| Ornaments by page | **The page strip** |

**The page strip.** This is the core widget and the thing the supervisor actually
uses.

- A grey dashed baseline spans the full width; x-position is
  `page / total_pages × 100%`, so every book is normalized to the same width.
- **Four rows, one per ornament type**, labelled DI / FT / HP / WE on the left.
  A page with 2 DI, 1 FT, 1 HP shows a dot on the DI row, a dot on the FT row, a
  dot on the HP row, and nothing on the WE row. Colours:
  DI orange `#e8833a`, FT yellow `#e3c41e`, HP green `#4a9d5f`, WE blue `#3d7ea6`.
- A dot with more than one ornament on that page shows a small count above it.
- Each dot is an `<a>` to `/ornament/{oid}` — so it is keyboard-reachable and
  middle-clickable, not a div with a click handler.
- **Hover preview.** One shared floating card, positioned next to the cursor and
  flipped when it would leave the viewport. The image is fetched on first hover
  only. A results page with 30 books and ~1000 dots therefore costs **zero** image
  requests until someone actually hovers. Focus (keyboard) triggers the same
  preview.

Endpoint: `GET /api/book/{id}/strip` returns the same data as JSON for anyone who
wants to build on it.

### 5.3 `/book/{book_id}` — Book detail

- Full title, year, ESTC, page count, ECCO id, Tonson tag.
- Imprint as raw strings **plus** normalized agent chips linking to agent pages.
  Showing both matters: the raw string is the evidence, the chips are the index.
- The same page strip, full width.
- A gallery of every ornament in the book, each captioned with type dot, page
  number, and subclass/variant links.
- **Ten most similar books**, with the score shown and the method explained in one
  line under the heading. Explaining it inline is not decoration — an unexplained
  similarity number invites over-reading.

### 5.4 `/ornament/{oid}` — Ornament detail

Three-column layout:

**Left — the image.** Server-side crop at 900px with a 4% margin. The margin is
deliberate: chips, cracks and worn corners sit *on* the bounding-box edge, and
those are exactly the features that identify a physical block. Two buttons: full
page image, and open in the ECCO viewer.

**Middle — metadata.** Type, the three class levels (each linked), book, year,
page, ESTC, publishers, printers, image id, machine cluster if present, and the
`id + boxes` key spelled out. Box coordinates are shown but de-emphasised — the
team said they mostly do not care about them, but they are the primary key so
hiding them entirely would be wrong.

**Right — most similar.** Three thumbnails with score and reason
("same variant" / "same subclass" / "same superclass"), then a prominent
**"See all images of this kind →"** button going to the variant page if a variant
exists, otherwise the subclass page.

**Below — report form.** Name (optional), suggested superclass / subclass /
variant (pre-filled as placeholders with the current values), and a free-text
reason. Submits to `POST /ornament/{oid}/report`, redirects back with a
confirmation.

### 5.5 `/classes` and `/class/{level}/{value}` — Class pages

`/classes` is a browse/filter list at a chosen level with a thumbnail, image
count, book count and year range per class.

`/class/{level}/{value}` is the workhorse:

1. **Publisher composition** and **printer composition**, side by side. A stacked
   share bar plus a ranked list with percentages, each linking to the agent page.
   Counted over **distinct books**, not ornament occurrences — otherwise one
   1000-page folio would swamp the chart.
2. **Other branches of the same design.** For a superclass, its subclasses; for a
   subclass or variant, the sibling variants. Thumbnail + image count + year
   range each, with the current one highlighted. This is what lets someone see at
   a glance what a/b/c actually look like.
3. **All images, sorted by year.** This ordering is fixed and deliberate: a
   physical block accumulates damage over time, so year order is the sequence in
   which wear should appear. Each thumbnail captions year, variant, book title,
   Tonson tag, and links to the ornament page.

### 5.6 `/agents` and `/agent/{name}` — Publishers and printers

Agent page shows book count, ornament count, distinct plate count, active year
range, and whether the name appears as publisher, printer, or both.

- **Plates used**, sorted by class id (so `C067_01a` and `C067_01b` sit together),
  each a thumbnail linking to that class page, captioned with image count, book
  count and year range.
- **Houses with the most similar stock**, ten entries with scores, plus the
  one-line method note.

### 5.7 `/admin` — Review queue

Login is a single shared password (`ADMIN_PASSWORD`) producing a signed,
HttpOnly, 8-hour session cookie. This is proportionate: the site is public, the
only privileged action is relabelling, and every relabel is logged.

Queue tabs: open / accepted / rejected / all. Each entry shows the crop, the book,
a coloured `current → suggested` diff, the reporter's note, and Accept / Reject
buttons.

**Accept** writes a `label_change` row *then* updates the ornament, inside one
transaction. Order matters: the old label must be captured before it is
overwritten, and the pair must be atomic so a crash cannot leave a relabelled
ornament with no history.

---

## 6. Images

| Concern | Decision |
|---|---|
| Where cropping happens | **Server-side.** A gallery shows 100+ ornaments; shipping 100 full ECCO page scans to the browser would be tens of MB. |
| Cache | Disk, sharded by hash prefix, LRU-pruned by a background thread to `CACHE_MAX_MB`. |
| Cache is optional | If the directory is missing or read-only, every request refetches. The cache must never be a dependency. |
| Failure | A failed fetch returns a grey SVG placeholder, never a 500. One broken page must not break a gallery. |
| Sizes | `?w=` parameter; 260px thumbnails, 900px detail, 270px hover. |
| Full pages | Redirect to the image server rather than proxying — no reason to spend our bandwidth. |

**Known issue:** during development the image server returned `403 Forbidden` to
requests from outside. Before launch, confirm that the Rahti egress IP
(`86.50.229.150`, shared by all Rahti projects) is allowed, or that the service is
open. If it needs a token, add it as a header in `imaging.client()` and a Secret
key. This is worth checking early — it is the one external dependency that can
make the whole site look broken.

---

## 7. URL map

```
GET  /                                    front page
GET  /books?q=&kind=&tonson=&p=           search
GET  /book/{book_id}                      book detail
GET  /ornament/{oid}                      ornament detail
POST /ornament/{oid}/report               submit correction
GET  /classes?level=&q=                   browse classes
GET  /class/{level}/{value}?p=            class detail  (level ∈ superclass|subclass|variant)
GET  /agents?q=                           browse agents
GET  /agent/{name}                        agent detail
GET  /admin, /admin/login, /admin/logout  review queue
POST /admin/report/{id}                   accept | reject

GET  /img/crop/{oid}.jpg?w=               cropped ornament
GET  /img/class/{level}/{value}.jpg       class exemplar thumbnail
GET  /img/page/{oid}                      redirect to full page

GET  /api/book/{id}/strip                 page-strip data
GET  /api/ornament/{oid}                  ornament + image urls
GET  /api/search/books?q=&limit=          search
GET  /api/docs                            OpenAPI
GET  /healthz                             readiness/liveness
```

---

## 8. Visual design

Plain by intent. Georgia for headings, system sans for body, one accent
(`#8a3324`, a muted printer's red), warm off-white background, 1px hairline
borders, 4px radii. No shadows except the hover card. Max width 1180px.

The reasoning: the images are the content. Every bit of visual weight spent on
chrome competes with a 300-year-old woodcut. A restrained frame also ages better
than a trendy one, and this site needs to still look credible in three years.

Responsive: the three-column ornament layout and two-column composition panels
collapse to one column below 980px.

---

## 9. Phase 2 — model inference and embedding similarity

This is the part that must be decided now, because it constrains the deployment.

**The constraint: Rahti has no GPU, and a container is capped at 4 CPU / 16 GiB.**

So the work splits in two:

**Bulk embedding — runs on LUMI, not Rahti.** All ~400k ornament crops are
embedded offline with the MoCo-v3 model, as a batch job. Output is a Parquet or
`.npy` file of `(oid, vector)` which is loaded into Postgres. This is the same
place the model is trained, so no new infrastructure.

**Query-time embedding — runs on Rahti, CPU.** A user uploads one image; we embed
one image. A ResNet-18 or ViT-S forward pass on CPU is on the order of 0.1–1s,
which is fine for an interactive request. This is why "upload an image and find
similar ones" is feasible on Rahti even without a GPU — **the expensive half never
runs here.**

**Vector search: pgvector, decided now.** 400k × 512 float32 is ~800 MB. Holding
that in the web pod's memory would eat most of its budget and be recomputed on
every restart. Postgres with `pgvector` and an HNSW index does the search in the
database, keeps it on disk, and costs nothing when idle. The schema already
reserves `ornament.embedding vector(512)` as a commented line, and
`deploy/postgres-pgvector/Containerfile` builds the required image. **Switching
the database image is far easier before there is production data than after**,
which is the argument for doing it at the start of Phase 2 rather than the middle.

**Deployment shape:**

```
ecco-web   (~300 MB)  ── HTTP ──>  ecco-embed  (~2.5 GB, torch CPU, replicas 0-1)
     │                                  │
     └──────────> ecco-db (postgres + pgvector, PVC) <──┘
```

Keeping the embedder separate is what keeps the web image small enough to pull
quickly and stay well under the 5 GB registry limit. Model weights go on a PVC or
are pulled from Allas at startup, **not** baked into the image.

**Where the machine-predicted tables fit.** The `HC*` columns already load. The
natural next page is a cluster view — "show me everything in cluster `HC0.12 = 47`"
— which is the same gallery component as a class page with a different filter. The
interesting comparison is cluster versus human class, and that view will want a
confusion-style display; worth designing once real predictions are in.

---

## 10. Deployment

```bash
oc login https://api.2.rahti.csc.fi:6443
oc project <your-project>

# 1. edit the Secret values in deploy/rahti.yaml first
oc apply -f deploy/rahti.yaml

# 2. build (BuildConfig points at your git repo)
oc start-build ecco-web --follow

# 3. load the data
oc get pods
oc rsync ./data <ecco-web-pod>:/tmp/
oc rsh <ecco-web-pod> python -m etl.load \
      --kind HP /tmp/data/HP_concat.csv \
      --kind DI /tmp/data/DI.csv \
      --kind FT /tmp/data/FT.csv

# 4. find the URL
oc get route ecco-web
```

Operational notes:

- `replicas: 1` for both deployments while the PVCs are RWO. Raising the replica
  count without first moving the crop cache off the PVC will fail to schedule.
- `strategy: Recreate`, not RollingUpdate — a rolling update would try to attach
  the RWO volume to two pods at once.
- Resource limits keep the limit/request ratio at ≤ 5, which Rahti enforces.
- Back up with `pg_dump`; `label_change` and `report` are the only tables that
  cannot be regenerated from the CSVs, so they are the ones that actually matter.

---

## 11. Open questions

1. **Image server access.** Does `oma-sivu` allow requests from the Rahti egress
   IP? The 403 seen in development needs resolving before launch.
2. **Agent disambiguation.** Surname-only keys merge distinct people. Is there an
   authority list (ESTC person ids) we could map onto instead?
3. **`total_pages` reliability.** Currently taken from the CSV, falling back to the
   highest page with an ornament. The strip's x-axis depends on it, so books with
   a wrong value will render with a compressed or stretched strip.
4. **FT accuracy.** The FT annotations are noted as 85–90% accurate. Should FT
   dots be visually marked as provisional?
5. **Licensing and credit.** What appears in the footer, and can the crops be
   shown publicly?
