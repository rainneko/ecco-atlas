"""Parsing and key rules shared by the loader and the web app.

Everything that turns messy CSV text into a stable key lives here, so the
loader, the admin tools and the site can never disagree about what an oid,
a class path or an agent name is.
"""
from __future__ import annotations

import ast
import hashlib
import math
import re

from . import config

# ================================================================ ornament key
BOX_ROUND = 2


def parse_box(raw) -> tuple[float, float, float, float] | None:
    """'[x1, y1, x2, y2]' -> tuple of floats, or None if unparseable."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        vals = raw
    else:
        try:
            vals = ast.literal_eval(str(raw))
        except (ValueError, SyntaxError):
            return None
        if not (isinstance(vals, (list, tuple)) and len(vals) == 4):
            return None
    try:
        out = tuple(float(v) for v in vals)
    except (TypeError, ValueError):
        return None
    return out if all(math.isfinite(v) for v in out) else None


def box_key(box) -> str:
    """Canonical string form of a box; rounding makes it stable across files
    that print floats with different precision."""
    return ",".join(f"{round(float(v), BOX_ROUND):.2f}" for v in box)


def image_id_of(raw_id) -> str:
    """'087910190000510.TIF' -> '087910190000510' (= docId + page(4) + '0')."""
    return re.sub(r"\.tif$", "", str(raw_id).strip(), flags=re.I)


def make_oid(image_id: str, box) -> str:
    """Stable 16-hex handle for the (id + boxes) primary key."""
    return hashlib.sha1(f"{image_id_of(image_id)}|{box_key(box)}".encode()).hexdigest()[:16]


def page_of(image_id: str) -> int | None:
    return int(image_id[10:14]) if re.fullmatch(r"\d{15}", image_id or "") else None


def book_id_of(image_id: str) -> str | None:
    return image_id[:10] if re.fullmatch(r"\d{15}", image_id or "") else None


def iou(a, b) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ================================================================ clusters
def norm_cluster(v) -> str | None:
    """HC* cell -> cluster id string. '8944.0' -> '8944' (pandas reads an int
    column containing blanks as float); blanks and noise labels -> None."""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in {"nan", "none", "null"}:
        return None
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    return None if s in config.NOISE_CLUSTERS else s


# ================================================================ class levels
def split_class(subclass, superclass) -> tuple[str, str, str]:
    """(superclass, subclass, variant) from CSV cells.

    Both HP naming schemes encode the variant as a trailing letter:
        C067_01a                -> C067 / C067_01 / a
        angel_face_big_vine_01b -> angel_face_big_vine / ..._01 / b
    """
    sup = (superclass or "").strip()
    sub = (subclass or "").strip()
    if sup and sub.startswith(sup + "_"):
        m = re.fullmatch(r"(\d+)([A-Za-z]*)", sub[len(sup) + 1:])
        if m:
            return sup, f"{sup}_{m.group(1)}", m.group(2).lower()
    m = re.fullmatch(r"(.*?\d)([A-Za-z])", sub)          # fallback: digit + letter tail
    if sup and m:
        return sup, m.group(1), m.group(2).lower()
    return sup, sub, ""


def class_path_for(src: str, sup, sub, var) -> str | None:
    """Join the levels this source exposes. DI/FT annotation expose only the
    superclass, so their class_path is the superclass alone."""
    levels = config.SOURCES[src]["levels"]
    parts = [sup or ""]
    if "subclass" in levels and sub:
        parts.append(sub)
        if "variant" in levels and var:
            parts.append(var)
    path = "/".join(p for p in parts if p)
    return path or None


_LABEL_OK = re.compile(r"^[A-Za-z0-9_.\-/]+$")


def parse_label(src: str, text: str, ctx_sup: str | None = None,
                ctx_sub: str | None = None) -> tuple[str, str | None, str]:
    """Free text typed by a reviewer -> (superclass, subclass, variant).

    Accepts 'C067_01b', 'C067/C067_01/b', 'C200' (superclass only), and — when
    a subclass context is given — a bare variant letter 'c'. Raises ValueError
    with a readable message on anything else.
    """
    t = (text or "").strip().replace(" ", "_")
    if not t:
        raise ValueError("empty label")
    if not _LABEL_OK.match(t):
        raise ValueError(f"'{text}': use letters, digits, _ - . and /")
    levels = config.SOURCES[src]["levels"]
    if "subclass" not in levels:
        if "/" in t:
            raise ValueError(f"{src} has only a superclass level; '/' is not allowed")
        return t, None, ""
    if "/" in t:
        parts = t.split("/")
        if len(parts) > 3 or not parts[0]:
            raise ValueError(f"'{text}': expected superclass/subclass/variant")
        return parts[0], (parts[1] or None) if len(parts) > 1 else None, \
            (parts[2].lower() if len(parts) > 2 else "")
    if ctx_sub and re.fullmatch(r"[a-z]{1,2}", t):
        return ctx_sup, ctx_sub, t
    m = re.fullmatch(r"(.+?)_(\d+)([A-Za-z]*)", t)
    if m:
        return m.group(1), f"{m.group(1)}_{m.group(2)}", m.group(3).lower()
    return t, None, ""


def plate_label(plate: str) -> str:
    """'HP-ann:C067/C067_01/a' -> 'C067_01a'; 'DI-pred:8944' -> 'DI-8944'."""
    src, _, rest = plate.partition(":")
    s = config.SOURCES.get(src, {})
    if s.get("family") == "pred":
        return f"{s['kind']}-{rest}"
    parts = rest.split("/")
    if len(parts) >= 2:
        return parts[1] + (parts[2] if len(parts) > 2 else "")
    return parts[0]


def plate_class(plate: str):
    """(src, level, value) of a plate, the same mapping as plate_url."""
    src, _, rest = plate.partition(":")
    s = config.SOURCES.get(src, {})
    if s.get("family") == "pred":
        return src, "cluster", rest
    parts = rest.split("/")
    if len(parts) == 3:
        return src, "variant", rest
    if len(parts) == 2:
        return src, "subclass", parts[1]
    return src, "superclass", parts[0]


def plate_url(plate: str) -> str:
    src, _, rest = plate.partition(":")
    s = config.SOURCES.get(src, {})
    if s.get("family") == "pred":
        return f"/class/{src}/cluster/{rest}"
    parts = rest.split("/")
    if len(parts) == 3:
        return f"/class/{src}/variant/{rest}"
    if len(parts) == 2:
        return f"/class/{src}/subclass/{parts[1]}"
    return f"/class/{src}/superclass/{parts[0]}"


def class_label(src: str, level: str, value: str) -> str:
    s = config.SOURCES.get(src, {})
    if level == "cluster":
        return f"{s.get('kind', '?')}-{value}"
    if level == "variant":
        parts = value.split("/")
        return parts[1] + parts[2] if len(parts) == 3 else value
    return value


def humanize(name) -> str:
    """'eagle_with_floral_border' -> 'eagle with floral border'; merged old
    names 'a_AND_b' -> 'a + b'."""
    if not name:
        return ""
    return str(name).replace("_AND_", " + ").replace("_", " ").strip()


def natural_key(s):
    """Sort 'C067_2' before 'C067_10' and cluster '9' before '10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s or ""))]


# ================================================================ agent names
_ADDRESS = re.compile(
    r"\b(court|street|lane|yard|square|church-?yard|row|exchange|alley|corner|"
    r"near|at the|sign of|over against|without|within)\b", re.I)
_NOISE = {"and", "sold", "by", "printed", "for", "the", "company", "co", "others", "etc",
          "son", "sons", "brothers", "bros", "widow", "executors", "assigns", "successors",
          "bookseller", "booksellers", "printer", "printers", "junior", "senior", "jun",
          "sen", "esq", "mr", "mrs", "messrs", "of", "in", "at", "with", "late", "his",
          "her", "their", "partners", "author", "authors"}
_SPLIT_AND = re.compile(r"\s+(?:and|&)\s+", re.I)


def _surname_key(word: str) -> str | None:
    k = re.sub(r"[^a-z\-']", "", word.lower()).strip("-'")
    return k if len(k) >= 3 and k not in _NOISE else None


def _is_initial(tok: str) -> bool:
    """'J.', 'R', 'Tho.', 'Wm.', 'J:' — a forename abbreviation, not a surname."""
    t = tok.strip(",")
    return bool(re.fullmatch(r"[A-Za-z]{1,3}[.:]?", t)) and (
        len(t.rstrip(".:")) == 1 or t.endswith((".", ":")))


def norm_agents(cell) -> list[tuple[str, str]]:
    """Imprint cell -> [(surname key, display form)].

        'Tonson, J.; Draper, S.'  -> [('tonson','Tonson, J.'), ('draper','Draper, S.')]
        'J. and R. Tonson'        -> [('tonson','J. and R. Tonson')]
        'Robinson and Roberts'    -> [('robinson','Robinson'), ('roberts','Roberts')]
        'Sword and Buckler Court' -> []   (an address, not a person)

    Surname-only on purpose: imprint spelling is unstable ('Tonson, J.',
    'Tonson, Jacob', 'J. and R. Tonson' are one business). Lossy by design.
    """
    if cell is None:
        return []
    s = str(cell)
    if not s.strip() or s.strip().lower() in {"nan", "none"}:
        return []
    out, seen = [], set()

    def add(key, disp):
        if key and key not in seen:
            seen.add(key)
            out.append((key, disp))

    for part in s.split(";"):
        disp = part.strip().strip(",").strip()
        if not disp or _ADDRESS.search(disp):
            continue
        if "," in disp:
            head = disp.split(",")[0]
            words = [w for w in re.split(r"\s+", head) if w and w.lower() not in _NOISE]
            if words:
                add(_surname_key(words[-1]), disp)
            continue
        chunks = _SPLIT_AND.split(disp)
        named = []
        for ch in chunks:
            words = [w for w in re.split(r"\s+", ch.strip())
                     if w and w.lower().strip(".,") not in _NOISE and not _is_initial(w)]
            if words:
                named.append((words[-1], ch.strip()))
        for word, ch in named:
            add(_surname_key(word), disp if len(named) == 1 else ch)
    return out


TONSON_RE = re.compile(r"tonson|watts", re.I)


def is_tonson(publishers, printers) -> bool:
    """House rule: publishers OR printers mention Tonson/Watts (case-insensitive)."""
    return any(TONSON_RE.search(str(v)) for v in (publishers, printers)
               if v is not None and str(v).strip().lower() not in {"", "nan", "none"})


# ================================================================ text search
def norm_title(t) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(t or "").lower())).strip()
