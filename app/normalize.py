"""Normalization helpers shared by the ETL loader and the web app.

Everything that turns messy CSV text into a stable key lives here, so the
loader and the app can never disagree about what an oid or an agent name is.
"""
from __future__ import annotations

import ast
import hashlib
import re

# ---------------------------------------------------------------- ornament key

BOX_ROUND = 2  # decimals kept when building the primary key


def parse_box(raw) -> tuple[float, float, float, float] | None:
    """'[x1, y1, x2, y2]' -> tuple of floats. Returns None if unparseable."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        return tuple(float(v) for v in raw)
    try:
        v = ast.literal_eval(str(raw))
        if isinstance(v, (list, tuple)) and len(v) == 4:
            return tuple(float(x) for x in v)
    except (ValueError, SyntaxError):
        pass
    return None


def box_key(box) -> str:
    """Canonical string form of a box. Rounding makes the key stable across
    files that print floats with different precision."""
    return ",".join(f"{round(float(v), BOX_ROUND):.2f}" for v in box)


def image_id_of(raw_id: str) -> str:
    """'087910190000510.TIF' -> '087910190000510'.

    This is also docId + zero-padded page + '0', i.e. exactly what the image
    server expects. Verified to match the `url` column on 100% of rows.
    """
    return re.sub(r"\.tif$", "", str(raw_id).strip(), flags=re.I)


def make_oid(raw_id: str, box) -> str:
    """Stable 16-hex handle for the (id + boxes) primary key."""
    return hashlib.sha1(f"{image_id_of(raw_id)}|{box_key(box)}".encode()).hexdigest()[:16]


def page_of(image_id: str) -> int | None:
    """Digits 11-14 of the image id are the page number."""
    return int(image_id[10:14]) if re.fullmatch(r"\d{15}", image_id) else None


def book_id_of(image_id: str) -> str | None:
    """First 10 digits are the ECCO documentID (leading zeros preserved)."""
    return image_id[:10] if re.fullmatch(r"\d{15}", image_id) else None


# ---------------------------------------------------------------- class levels

def split_class(subclass: str, superclass: str) -> tuple[str, str, str]:
    """Return (superclass, subclass, variant).

    Two naming schemes exist in the annotation files and both encode the third
    level as a trailing letter on the subclass:
        C067_01a                  -> C067 / C067_01 / a
        angel_face_big_vine_01b   -> angel_face_big_vine / ..._01 / b
    A subclass with no trailing letter simply has variant ''.
    """
    sup = (superclass or "").strip()
    sub = (subclass or "").strip()
    if sup and sub.startswith(sup + "_"):
        m = re.fullmatch(r"(\d+)([A-Za-z]*)", sub[len(sup) + 1:])
        if m:
            return sup, f"{sup}_{m.group(1)}", m.group(2)
    m = re.fullmatch(r"(.*?)([A-Za-z])", sub)   # fallback: trailing letter
    if sup and m and m.group(1).rstrip("_"):
        return sup, m.group(1), m.group(2)
    return sup, sub, ""


def class_path(sup: str, sub: str, var: str) -> str:
    return "/".join(p for p in (sup, sub, var) if p)


# ---------------------------------------------------------------- agent names

_NOISE = re.compile(
    r"\b(and|&|sold by|printed for|printed by|printed|for|the|company|co|others|etc)\b", re.I)
_STOP = {"esq", "sen", "jun", "late", "his", "her"}


def norm_agents(cell) -> list[tuple[str, str]]:
    """'Tonson, J.; Draper, S.' -> [('tonson', 'Tonson, J.'), ('draper', 'Draper, S.')].

    Returns (normalized key, display form). The key is the surname only,
    because imprint spelling is wildly unstable across the corpus
    ('Tonson, J.' / 'Tonson, Jacob' / 'J. and R. Tonson' are one business).
    """
    if cell is None:
        return []
    s = str(cell)
    if not s.strip() or s.strip().lower() in {"nan", "none"}:
        return []
    out, seen = [], set()
    for part in s.split(";"):
        display = part.strip().strip(",").strip()
        if not display:
            continue
        cleaned = _NOISE.sub(" ", part)
        cleaned = re.sub(r"[^A-Za-z\s,]", " ", cleaned).strip().strip(",").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            continue
        key = re.sub(r"\s+", " ", cleaned.split(",")[0].strip().lower())
        if len(key) < 3 or key in _STOP or key in seen:
            continue
        seen.add(key)
        out.append((key, display))
    return out


TONSON_RE = re.compile(r"tonson|watts", re.I)


def is_tonson(publishers, printers) -> bool:
    """House rule: publishers OR printers containing Tonson/Watts (case-insensitive)."""
    return any(TONSON_RE.search(str(v)) for v in (publishers, printers)
               if v is not None and str(v).strip().lower() not in {"", "nan", "none"})


# ---------------------------------------------------------------- text search

def norm_title(t) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(t or "").lower())).strip()
