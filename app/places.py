"""Places by design (DESIGN §12.3): for the designs of one group — the
subclasses of a superclass, the variants of a subclass, or a set of linked
machine clusters — the mix of places their books were printed in, and the
pairs whose mixes differ most.

Pure functions over (design, book_id, place_key, place) rows so the same
logic serves human classes and cluster groups.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from . import config


@dataclass
class Params:
    min_books: int = config.PLACE_DEFAULTS["min_books"]
    d: float = config.PLACE_DEFAULTS["d"]

    @classmethod
    def from_query(cls, qp):
        p = cls()
        try:
            p.min_books = min(max(int(qp.get("place_min", p.min_books)), 2), 200)
        except (TypeError, ValueError):
            pass
        try:
            p.d = min(max(float(qp.get("place_d", p.d)), 0.1), 1.0)
        except (TypeError, ValueError):
            pass
        return p

    def as_query(self):
        return f"place_min={self.min_books}&place_d={self.d:g}"


def tv_distance(p: dict, q: dict) -> float:
    """Total-variation distance between two place distributions (0..1)."""
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def analyse(rows, params: Params, top_places=6):
    """rows: dicts with design, book_id, place_key, place (one per book/design).

    Returns None when no book has a place; else
      designs: [{design, n_books, n_known, dist{place_key: share}, top}]
      places:  [(place_key, place)] — the columns of the table
      pairs:   [{a, b, d, flagged}] sorted by d desc (designs with >= min_books)
    """
    books = defaultdict(dict)          # design -> {book_id: place_key}
    names = {}
    for r in rows:
        books[r["design"]][r["book_id"]] = r.get("place_key")
        if r.get("place_key") and r.get("place"):
            names.setdefault(r["place_key"], r["place"])
    if not any(k for d in books.values() for k in d.values()):
        return None
    overall = Counter(k for d in books.values() for k in d.values() if k)
    places = [(k, names.get(k, k)) for k, _ in overall.most_common(top_places)]
    designs = []
    for design, bmap in books.items():
        known = [k for k in bmap.values() if k]
        c = Counter(known)
        dist = {k: n / len(known) for k, n in c.items()} if known else {}
        top = c.most_common(1)[0][0] if c else None
        designs.append(dict(design=design, n_books=len(bmap), n_known=len(known), dist=dist,
                            top=top, top_name=names.get(top, top) if top else None))
    designs.sort(key=lambda x: x["design"])
    eligible = [x for x in designs if x["n_known"] >= params.min_books]
    pairs = []
    for i in range(len(eligible)):
        for j in range(i + 1, len(eligible)):
            a, b = eligible[i], eligible[j]
            d = tv_distance(a["dist"], b["dist"])
            pairs.append(dict(a=a["design"], b=b["design"], d=d, flagged=d >= params.d,
                              top_a=a["top"], top_b=b["top"]))
    pairs.sort(key=lambda x: -x["d"])
    return dict(designs=designs, places=places, pairs=pairs,
                n_eligible=len(eligible), n_flagged=sum(1 for p in pairs if p["flagged"]))
