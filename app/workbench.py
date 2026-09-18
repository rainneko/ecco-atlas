"""Annotation workbench (DESIGN §5.9).

Buckets are defined by each member's *current human label*:
  * on a human class (e.g. HP-ann superclass C067) every member has a label, so
    the buckets are its subclasses/variants;
  * on a machine cluster (e.g. DI-pred 8944) the first bucket is "no human
    label yet", followed by any human labels already given to its members.
Targets = existing buckets + five new, named buckets + Drop.
"""
from __future__ import annotations

import re
import uuid
from collections import OrderedDict

from . import config, db
from . import normalize as N

N_NEW = 5


def split_path(src, path):
    parts = (path or "").split("/")
    return parts[0], (parts[1] if len(parts) > 1 else None), (parts[2] if len(parts) > 2 else "")


def target_source(src, members):
    """Which human-label source new buckets write to."""
    spec = config.SOURCES[src]
    if spec["family"] == "ann":
        return src
    return config.ann_source_for_kind(spec["kind"])


def default_names(src, level, value, members, tsrc):
    if not tsrc:
        return []
    levels = config.SOURCES[tsrc]["levels"]
    if config.SOURCES[src]["family"] == "ann" and level == "superclass" and "subclass" in levels:
        nums = [int(m.group(1)) for s in db.existing_subclasses(src, value)
                if (m := re.search(r"_(\d+)$", s or ""))]
        start = max(nums, default=0) + 1
        return [f"{value}_{n:02d}" for n in range(start, start + N_NEW)]
    if level == "subclass":
        used = {m["variant"] for m in members if m["variant"]}
        letters = [c for c in "abcdefghijklmnopqrstuvwxyz" if c not in used]
        return [f"{value}{c}" for c in letters[:N_NEW]]
    kind = config.SOURCES[src]["kind"]
    if config.SOURCES[src]["family"] == "pred":
        if "subclass" in levels:
            return [f"{kind}{value}_{k:02d}" for k in range(1, N_NEW + 1)]
        return [f"{kind}-{value}-{k}" for k in range(1, N_NEW + 1)]
    return [f"{value}_new{k}" for k in range(1, N_NEW + 1)]


def build(src, level, value):
    spec = config.SOURCES[src]
    members = db.workbench_members(src, level, value)
    buckets = OrderedDict()
    if spec["family"] == "pred":
        buckets["none"] = dict(key="none", title="No human label yet",
                               note=f"stays in machine cluster {spec['kind']}-{value}", items=[])
    for m in members:
        if m["ann_src"] and m["class_path"]:
            key = f"h:{m['ann_src']}:{m['class_path']}"
            if key not in buckets:
                buckets[key] = dict(key=key, title=m["label"] or m["class_path"],
                                    note=(m["ann_src"] if spec["family"] == "pred" else ""),
                                    items=[])
        else:
            key = "none"
        buckets.setdefault(key, dict(key=key, title="No human label", note="", items=[]))
        m["bucket"] = key
        buckets[key]["items"].append(m)
    ordered = [buckets.pop("none")] if "none" in buckets else []
    ordered += sorted(buckets.values(), key=lambda b: N.natural_key(b["key"]))
    tsrc = target_source(src, members)
    names = default_names(src, level, value, members, tsrc)
    return dict(members=members, buckets=ordered, target_src=tsrc, new_names=names,
                drop_note=("remove the human label (the image keeps its machine cluster)"
                           if spec["family"] == "ann" else
                           "not this cluster: mark the machine assignment as rejected"))


def apply(src, level, value, moves: dict, names: dict, who: str):
    """moves: {oid: target}; names: {"n1": "C067_05", ...}. Returns (n, errors)."""
    spec = config.SOURCES[src]
    members = {m["oid"]: m for m in db.workbench_members(src, level, value)}
    tsrc = target_source(src, list(members.values()))
    ctx_sup = value if level == "superclass" else None
    ctx_sub = None
    if level == "subclass":
        any_m = next(iter(members.values()), None)
        ctx_sup, ctx_sub = (any_m["superclass"] if any_m else None), value
    parsed, errors = {}, []
    for k, name in (names or {}).items():
        if not str(name).strip():
            continue
        if not tsrc:
            errors.append("this source has no human-label level to create classes in")
            break
        try:
            parsed[k] = N.parse_label(tsrc, name, ctx_sup, ctx_sub)
        except ValueError as e:
            errors.append(f"{k}: {e}")
    changes = []
    for oid, target in (moves or {}).items():
        m = members.get(oid)
        if m is None:
            errors.append(f"{oid} is not in this class any more (reload the page)")
            continue
        cur = m["bucket"] if "bucket" in m else (
            f"h:{m['ann_src']}:{m['class_path']}" if m["ann_src"] else "none")
        if target == cur:
            continue
        if target == "drop":
            if spec["family"] == "ann":
                changes.append(dict(oid=oid, action="drop", ann_src=None))
            else:
                changes.append(dict(oid=oid, action="reject_cluster", ann_src=m["ann_src"],
                                    superclass=m["superclass"], subclass=m["subclass"],
                                    variant=m["variant"], rejected=True))
        elif target == "none":
            changes.append(dict(oid=oid, action="drop", ann_src=None))
        elif target.startswith("h:"):
            _, s, path = target.split(":", 2)
            if s not in config.SOURCES:
                errors.append(f"unknown target {target}")
                continue
            sup, sub, var = split_path(s, path)
            changes.append(dict(oid=oid, action="relabel", ann_src=s, superclass=sup,
                                subclass=sub, variant=var))
        elif target in parsed:
            sup, sub, var = parsed[target]
            changes.append(dict(oid=oid, action="relabel", ann_src=tsrc, superclass=sup,
                                subclass=sub, variant=var))
        else:
            errors.append(f"{oid}: bucket '{target}' has no name")
    if errors:
        return 0, errors
    n = db.apply_changes(changes, who, batch_id="wb-" + uuid.uuid4().hex[:10])
    return n, []
