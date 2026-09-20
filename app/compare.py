"""Compare two sets of ornaments (DESIGN §12.7).

A side = class (SRC:LEVEL:VALUE) + optional place + optional house (+ role).
`-x` negates a place or a house. When both sides are the same class the
designs below it are aligned row by row; otherwise listed side by side.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from . import config, db
from . import normalize as N


@dataclass
class Side:
    src: str
    level: str
    value: str
    place: str = ""
    agent: str = ""
    role: str = ""
    book: str = ""

    @property
    def key(self):
        return f"{self.src}:{self.level}:{self.value}"

    def params(self, p):
        d = {p: self.key}
        if self.place:
            d[f"{p}_place"] = self.place
        if self.agent:
            d[f"{p}_agent"] = self.agent
        if self.role:
            d[f"{p}_role"] = self.role
        if self.book:
            d[f"{p}_book"] = self.book
        return d


def parse_key(key):
    try:
        src, level, value = key.split(":", 2)
    except (AttributeError, ValueError):
        return None
    if src not in config.SOURCES or level not in config.SOURCES[src]["levels"] or not value:
        return None
    return src, level, value


def resolve(text):
    """A full key, or free text resolved through the class picker's search."""
    t = (text or "").strip()
    if parse_key(t):
        return parse_key(t)
    hits = db.suggest_classes(t, 1)
    return (hits[0]["src"], hits[0]["level"], hits[0]["value"]) if hits else None


def parse_side(qp, p):
    k = resolve(qp.get(p) or qp.get(f"{p}_q"))
    if not k:
        return None
    place = (qp.get(f"{p}_place") or "").strip()
    if place and qp.get(f"{p}_place_not") and not place.startswith("-"):
        place = "-" + place
    agent = (qp.get(f"{p}_agent") or "").strip()
    if agent and qp.get(f"{p}_agent_not") and not agent.startswith("-"):
        agent = "-" + agent
    role = qp.get(f"{p}_role") or ""
    book = (qp.get(f"{p}_book") or "").strip()
    return Side(*k, place=place, agent=agent,
                role=role if role in ("publisher", "printer") else "",
                book=book if book.isalnum() else "")


def url(a: Side, b: Side | None = None):
    d = a.params("a")
    if b:
        d.update(b.params("b"))
    return "/compare?" + urlencode(d)


# ---------------------------------------------------------------- designs
def design_exprs(side: Side):
    """(SQL for the design value, SQL for its level) one level below the class."""
    levels = config.SOURCES[side.src]["levels"]
    if side.level == "superclass" and "subclass" in levels:
        return ("coalesce(o.subclass, o.superclass)",
                "CASE WHEN o.subclass IS NULL THEN 'superclass' ELSE 'subclass' END")
    if side.level == "subclass" and "variant" in levels:
        return ("CASE WHEN o.variant <> '' THEN o.class_path ELSE o.subclass END",
                "CASE WHEN o.variant <> '' THEN 'variant' ELSE 'subclass' END")
    if side.level == "cluster":
        return "o.hc_cluster", "'cluster'"
    col = {"superclass": "o.superclass", "subclass": "o.subclass",
           "variant": "o.class_path"}[side.level]
    return col, f"'{side.level}'"


def cluster_values(side: Side):
    """A cluster side includes its related clusters (§12.6), except when it is
    limited to one book: then the question is about this plate's impressions."""
    if side.level != "cluster" or side.book:
        return [side.value]
    return db.cluster_group(side.src, side.value)


def build(side: Side):
    dsql, lsql = design_exprs(side)
    values = cluster_values(side)
    r = db.side_rows(side.src, side.level, side.value, values, side.place, side.agent,
                     side.role, dsql, lsql, side.book)
    for d in r["designs"]:
        d["label"] = N.class_label(side.src, d["dlevel"], d["design"])
    r["designs"].sort(key=lambda d: N.natural_key(d["design"]))
    r["n_related"] = len(values) - 1
    r["label"] = describe(side, r["n_related"])
    return r


def place_name(key):
    r = db.q("SELECT min(place) AS name FROM book WHERE place_key = %s", (key,), one=True)
    return (r and r["name"]) or key


def house_name(name):
    a = db.get_agent(name)
    return (a and (a["display_name"] or a["name"])) or name


def describe(side: Side, n_related=0):
    """Human-readable definition of a side, e.g.
    'C014 · printed in Dublin · Tonson, J. as printer'."""
    parts = [N.class_label(side.src, side.level, side.value)
             + (f" and {n_related} related cluster{'s' if n_related != 1 else ''}"
                if n_related else "")]
    if side.place:
        pn = place_name(side.place.lstrip("-"))
        parts.append(f"printed outside {pn}" if side.place.startswith("-") else f"printed in {pn}")
    if side.agent:
        hn = house_name(side.agent.lstrip("-"))
        role = f" as {side.role}" if side.role else ""
        parts.append(f"not naming {hn}{role}" if side.agent.startswith("-") else f"{hn}{role}")
    if side.book:
        b = db.q("SELECT full_title, year FROM book WHERE book_id = %s", (side.book,), one=True)
        t = (b and (b["full_title"] or "")) or side.book
        parts.append(f"in {t[:60]}{'…' if len(t) > 60 else ''}" + (f" ({b['year']})" if b and b["year"] else ""))
    return " · ".join(parts)


def align(a, b):
    """Rows for the aligned design table: both sides first, then A only, B only."""
    da = {(d["dlevel"], d["design"]): d for d in a["designs"]}
    db_ = {(d["dlevel"], d["design"]): d for d in b["designs"]}
    keys = set(da) | set(db_)

    def order(k):
        grp = 0 if (k in da and k in db_) else (1 if k in da else 2)
        return grp, N.natural_key(k[1])
    rows = [dict(key=k, label=(da.get(k) or db_.get(k))["label"], a=da.get(k), b=db_.get(k))
            for k in sorted(keys, key=order)]
    counts = dict(both=sum(1 for r in rows if r["a"] and r["b"]),
                  only_a=sum(1 for r in rows if r["a"] and not r["b"]),
                  only_b=sum(1 for r in rows if r["b"] and not r["a"]))
    return rows, counts


# ---------------------------------------------------------------- click menus (§12.7.4)
def place_menu(cls_url, src, level, value, key, name, main_key, main_name):
    if key == main_key:
        a, b = Side(src, level, value, place=key), Side(src, level, value, place="-" + key)
        primary = (f"Compare {name} with everywhere else", url(a, b))
    else:
        a, b = Side(src, level, value, place=main_key), Side(src, level, value, place=key)
        primary = (f"Compare {name} with {main_name}", url(a, b))
    return dict(title=name, primary=primary,
                secondary=(f"Only the {name} images of this class",
                           f"{cls_url}?{urlencode({'place': key})}#images"),
                more=(f"Learn more about {name}", f"/books?{urlencode({'place': key})}"))


def house_menu(cls_url, src, level, value, name, display):
    a = Side(src, level, value, agent=name)
    b = Side(src, level, value, agent="-" + name)
    return dict(title=display,
                primary=(f"Compare {display} with the other houses", url(a, b)),
                secondary=(f"Only {display}'s images of this class",
                           f"{cls_url}?{urlencode({'agent': name})}#images"),
                more=(f"Learn more about {display}", f"/agent/{name}"))


def plate_pair_menu(plate, a_book, b_book):
    """Click menu for a shared plate on /reprints (§12.4): the plate on both
    sides, each side limited to one of the two books."""
    src, level, value = N.plate_class(plate)
    a = Side(src, level, value, book=a_book)
    b = Side(src, level, value, book=b_book)
    lab = N.plate_label(plate)
    return dict(title=lab,
                primary=(f"Compare the two books on {lab}", url(a, b)),
                secondary=(f"Open {lab}", N.plate_url(plate)),
                more=("The other pairs of the first book", f"/book/{a_book}#pairs"))
