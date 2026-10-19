# ECCO Ornament Atlas v2.6 — changes and deploy steps

DESIGN.md §0.00 gives the reasons. This file gives the steps.

## 1. What changed

- **Class page tabs**: Images · Who used it · Where and when · Designs ·
  Related. The tab is in the URL (`?tab=houses`). Each tab runs only its own
  database queries. A tab with nothing to show is not offered. Old links to
  `#lending`, `#map` and `#places` open the correct tab.
- **Lightbox** on every gallery of ornaments (class, book, publisher pair,
  compare, image search, the "most similar" row). Click an image; ← and →
  move through the gallery; Esc closes. Ctrl/⌘-click still opens the ornament
  page.
- **Ornament page in two columns**: image, actions and the three most similar
  images on the left; the record on the right.
- Links from the unusual-imprints page open the *Who used it* tab.

## 2. Package contents

The package is **cumulative**: it contains every file of v2.5 and the v2.6
changes. Unzip it in the repository root (the folder with `app/` and `etl/`)
and let it replace the files. It does not matter whether v2.5 is already
deployed.

Files changed or added in v2.6:

| File | Change |
|---|---|
| `DESIGN.md` | v2.6 |
| `CHANGES_v2.6.md` | new (this file) |
| `README.md` | layout list |
| `app/main.py` | `class_detail` with tabs; `contrast_rows()` |
| `app/templates/class.html` | tab bar and five tabs |
| `app/templates/ornament.html` | two columns |
| `app/templates/lending.html` | links open the *Who used it* tab |
| `app/templates/base.html` | loads `v26.js` |
| `app/static/v26.js` | new: lightbox; old section links to tabs |
| `app/static/v25.css` | v2.6 section at the end: tabs, two columns, lightbox |
| `tools/descriptive_stats.py` | new: descriptive statistics (Tonson §2.1, DES §2) |

## 3. Deploy

1. Unzip the package in the repository root. Let it replace the files.
2. Commit and push. Rahti builds the new image.
3. Open a class page. Check the tabs, and click an image (lightbox).
4. Open an old link with `#lending` at the end. It must open *Who used it*.

No database change. No new environment variable.

## 4. Open question

- Should *Who used it* be the default tab instead of *Images*? Change the
  default value of `tab` in `class_detail` (app/main.py) if the team prefers.
