"""Unusual imprints — formerly "lending" (DESIGN §4.5).

A block appears almost always in books that name one house (the main house) and
occasionally in books that name another house and not the main one. This module
finds those books and places them in time. It does NOT say why: trade-publisher
imprints, a printer working for several booksellers, sale of stock, reissues,
copied blocks and data errors all produce the same pattern.

Pure functions over (book, year, agents) rows so the same logic serves the
class page and the /lending list. Thresholds are parameters, never constants
in SQL. Internal names (owner, borrower, verdict keys) are kept for
compatibility with stored reviews and URLs; the interface wording is neutral.
"""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import config, db
from . import normalize as N


@dataclass
class Params:
    owner: float = config.LENDING_DEFAULTS["owner"]
    lo: float = config.LENDING_DEFAULTS["lo"]
    hi: float = config.LENDING_DEFAULTS["hi"]
    min_books: int = config.LENDING_DEFAULTS["min_books"]
    gap: int = config.LENDING_DEFAULTS["gap"]

    @classmethod
    def from_query(cls, qp):
        def num(k, typ, lo_, hi_):
            try:
                v = typ(qp.get(k, getattr(cls, k)))
            except (TypeError, ValueError):
                v = getattr(cls, k)
            return min(max(v, lo_), hi_)
        p = cls(owner=num("owner", float, 0.3, 1.0), lo=num("lo", float, 0.0, 0.5),
                hi=num("hi", float, 0.01, 0.6), min_books=num("min_books", int, 2, 200),
                gap=num("gap", int, 0, 50))
        if p.lo > p.hi:
            p.lo, p.hi = p.hi, p.lo
        return p

    def as_query(self):
        return (f"owner={self.owner:g}&lo={self.lo:g}&hi={self.hi:g}"
                f"&min_books={self.min_books}&gap={self.gap}")

    def is_default(self):
        return self == Params()


# Time position of a case relative to the main house's dated use. Descriptive
# only; the keys are unchanged from v2.0 so stored links and CSVs keep working.
VERDICTS = {
    "lending": ("Within its years", "inside the years the main house used this block"),
    "near": ("Close to its years", "within the year gap of the main house's use"),
    "later": ("After its years", "after the main house's last dated use"),
    "earlier": ("Before its years", "before the main house's first dated use"),
    "undated": ("Undated", "no year to compare"),
}


def verdict(year, y0, y1, gap):
    if year is None or y0 is None:
        return "undated", None
    if y0 <= year <= y1:
        return "lending", 0
    d = y0 - year if year < y0 else year - y1
    if d <= gap:
        return "near", d
    return ("earlier" if year < y0 else "later"), d


def trade_publisher(name, year=None) -> bool:
    """True when the surname key is a known trade publisher in that year."""
    span = config.TRADE_PUBLISHERS.get(name or "")
    if span is None:
        return False
    y0, y1 = (tuple(span) + (None, None))[:2]
    if year is None:
        return True
    return (y0 is None or year >= y0) and (y1 is None or year <= y1)


@dataclass
class PlateResult:
    n_known: int = 0
    share: dict = field(default_factory=dict)      # agent_id -> coverage
    owners: list = field(default_factory=list)      # main house(s)
    borrowers: list = field(default_factory=list)   # other houses with cases
    events: list = field(default_factory=list)      # cases
    owner_span: tuple = (None, None)
    owner_median: float | None = None
    eligible: bool = False


def analyse(books: dict, agents_by_book: dict, p: Params) -> PlateResult:
    """books: {book_id: (year, title)}; agents_by_book: {book_id: set(agent_id)}."""
    r = PlateResult()
    known = [b for b in books if agents_by_book.get(b)]
    r.n_known = len(known)
    if not known:
        return r
    cover = Counter(a for b in known for a in agents_by_book[b])
    r.share = {a: c / r.n_known for a, c in cover.items()}
    r.eligible = r.n_known >= p.min_books
    r.owners = sorted([a for a, s in r.share.items() if s >= p.owner],
                      key=lambda a: (-r.share[a], a))
    if not r.eligible or not r.owners:
        return r
    own = set(r.owners)
    oy = sorted(books[b][0] for b in known if agents_by_book[b] & own and books[b][0])
    if oy:
        r.owner_span, r.owner_median = (oy[0], oy[-1]), statistics.median(oy)
    for a, s in sorted(r.share.items(), key=lambda kv: -kv[1]):
        if a in own or not (p.lo <= s <= p.hi):
            continue
        bb = [b for b in known if a in agents_by_book[b] and not (agents_by_book[b] & own)]
        if not bb:
            continue                       # only ever co-signed with the main house
        r.borrowers.append(a)
        for b in sorted(bb, key=lambda b: (books[b][0] or 9999, b)):
            y = books[b][0]
            v, d = verdict(y, *r.owner_span, p.gap)
            r.events.append(dict(borrower=a, book_id=b, year=y, title=books[b][1],
                                 verdict=v, gap=d,
                                 gap_median=(abs(y - r.owner_median)
                                             if y and r.owner_median else None)))
    return r


def analyse_rows(rows, p: Params) -> PlateResult:
    """rows from db.class_book_agents: one per (book, agent, role)."""
    books, abb = {}, defaultdict(set)
    for x in rows:
        books[x["book_id"]] = (x["year"], x["full_title"])
        if x["agent_id"]:
            abb[x["book_id"]].add(x["agent_id"])
    return analyse(books, abb, p)


_memo: dict = {}


def find_events(p: Params, srcs, agent_id=None, plate=None):
    """All unusual-imprint cases for the selected sources. Memoised for two
    minutes per parameter set; the candidate plates are pre-filtered in SQL."""
    key = (p.owner, p.lo, p.hi, p.min_books, p.gap, tuple(sorted(srcs)), agent_id, plate,
           db.meta().get("derived_at"))
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    plates = db.lending_plates(srcs, p.owner, p.lo, p.hi, p.min_books, plate, agent_id)
    books, abb = defaultdict(dict), defaultdict(lambda: defaultdict(set))
    for x in db.plate_book_agents(plates):
        books[x["plate"]][x["book_id"]] = (x["year"], x["full_title"])
        if x["agent_id"]:
            abb[x["plate"]][x["book_id"]].add(x["agent_id"])
    info = db.plate_info(plates)
    out = []
    for pl in sorted(plates, key=N.natural_key):
        r = analyse(books[pl], abb[pl], p)
        if not r.events:
            continue
        for e in r.events:
            if agent_id and agent_id != e["borrower"] and agent_id not in r.owners:
                continue
            out.append(dict(e, plate=pl, owners=r.owners,
                            owner_shares=[r.share[a] for a in r.owners],
                            borrower_share=r.share[e["borrower"]],
                            owner_span=r.owner_span, owner_median=r.owner_median,
                            n_known=r.n_known, info=info.get(pl, {}),
                            owner_ids=",".join(str(a) for a in sorted(r.owners))))
    if len(_memo) > 64:
        _memo.clear()
    _memo[key] = (time.time(), out)
    return out
