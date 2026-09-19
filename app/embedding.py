"""Image search (DESIGN §9): embed an uploaded ornament with the project's own
model and rank the atlas's classes by similarity.

torch/timm are imported only inside the extractor, so the web app runs
without them; the search page then explains that the model is not installed.
With EMBED_FAKE=1 a deterministic random-projection "model" stands in for
torch, which is how the pipeline is tested without a GPU library.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
from collections import Counter, defaultdict

import numpy as np
from PIL import Image

from . import config, db

log = logging.getLogger("ecco.embedding")


# ---------------------------------------------------------------- vectors
def encode(vec) -> bytes:
    return np.asarray(vec, dtype=np.float16).tobytes()


def decode(blob, dim=None) -> np.ndarray:
    v = np.frombuffer(bytes(blob), dtype=np.float16).astype(np.float32)
    return v if dim is None else v[:dim]


def l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-8)


# ---------------------------------------------------------------- preprocessing
def crop_like_eval(im: Image.Image, size, crop="full") -> Image.Image:
    """Query preprocessing, identical to how the stored vectors were made.

    crop="full" (default): Resize(size) — the whole box stretched to `size`.
    crop="square": the old research eval transform RandomResizedCrop(size,
    scale=(1,1), ratio=(1,1)), which on a non-square image falls back to the
    centre square (a 4:1 headpiece keeps its middle quarter). Kept only so that
    vectors exported with the old transform can still be searched correctly;
    the mode is recorded per type in embedding_model."""
    h, w = size
    im = im.convert("RGB")
    if crop == "square" and im.width != im.height:
        s = min(im.width, im.height)
        i, j = (im.height - s) // 2, (im.width - s) // 2
        im = im.crop((j, i, j + s, i + s))
    return im.resize((w, h), Image.BILINEAR)


def to_array(im: Image.Image) -> np.ndarray:
    """ToTensor(): float32 in [0, 1], shape (3, H, W)."""
    return np.asarray(im, dtype=np.float32).transpose(2, 0, 1) / 255.0


# ---------------------------------------------------------------- extractors
class FakeExtractor:
    """Deterministic stand-in: random projection of a 32×32 grey thumbnail.
    Same image → same vector; similar images → similar vectors."""
    arch = "fake"

    def __init__(self, dim):
        self.dim = dim
        rng = np.random.default_rng(7)
        self.P = rng.standard_normal((32 * 32, dim)).astype(np.float32) / 32.0

    def embed(self, im: Image.Image, size, crop="full") -> np.ndarray:
        x = crop_like_eval(im, size, crop).convert("L").resize((32, 32), Image.BILINEAR)
        px = np.asarray(x, dtype=np.float32).ravel() / 255.0
        v = (px - px.mean()) @ self.P            # mean removed: background does not dominate
        return l2(v)


class TorchExtractor:
    """The frozen backbone of docret.eval.backbones, rebuilt from the state
    dict stored by `python -m etl.load_model`, with the same inference path as
    docret.train.main.build_byol_backbone.BackboneWrapper."""

    def __init__(self, arch: str, state: dict):
        import torch
        import torch.nn as nn
        self.torch = torch
        self.arch = arch
        if arch == "vit_s":
            import timm
            net = timm.create_model("vit_small_patch16_224", pretrained=False, num_classes=0,
                                    in_chans=3)
            self.dim, self.is_vit = 384, True
        elif arch in ("resnet18", "resnet50"):
            import torchvision
            r = getattr(torchvision.models, arch)(weights=None)
            net = nn.Sequential(*list(r.children())[:-1])
            self.dim, self.is_vit = (512 if arch == "resnet18" else 2048), False
        else:
            raise ValueError(f"unsupported architecture {arch!r}")
        missing, unexpected = net.load_state_dict(state, strict=False)
        if missing or unexpected:
            log.warning("model %s: %d missing, %d unexpected keys", arch, len(missing),
                        len(unexpected))
        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
        self.net = net.eval()

    def embed(self, im: Image.Image, size, crop="full") -> np.ndarray:
        torch = self.torch
        x = torch.from_numpy(to_array(crop_like_eval(im, size, crop))).unsqueeze(0)
        with torch.no_grad():
            if self.is_vit:
                if x.shape[-2:] != (224, 224):
                    x = torch.nn.functional.interpolate(x, size=(224, 224), mode="bilinear",
                                                        align_corners=False)
                out = self.net(x)
            else:
                out = self.net(x).flatten(start_dim=1)
        return l2(out[0].cpu().numpy().astype(np.float32))


def torch_available() -> bool:
    try:
        import torch, timm  # noqa: F401
        return True
    except ImportError:
        return False


def class_level(src, value):
    """(level, value-for-URL) of a centroid: clusters as they are; human plates
    by the depth of their class path (C014 / C014_01 / C014_01a)."""
    if config.SOURCES[src]["family"] == "pred":
        return "cluster", value
    parts = value.split("/")
    if len(parts) >= 3 and parts[2]:
        return "variant", value
    if len(parts) == 2:
        return "subclass", parts[1]
    return "superclass", parts[0]


# ---------------------------------------------------------------- engine
class Engine:
    """Holds the extractor and the in-memory matrices (type samples, centroids).
    Everything loads lazily on first use or in `warmup()`; `reload()` picks up
    a newly loaded model or embedding set."""

    def __init__(self):
        self.lock = threading.Lock()
        self.extractor = None
        self.model_name = None
        self.emb = None            # embedding_model row
        self.sizes = {}            # kind -> {"size": (h, w), "crop": "square"}
        self.samples = {}          # kind -> (matrix, oids)
        self.centroids = {}        # kind -> (matrix, rows)
        self.full = {}             # kind -> (matrix, oids) when EMBED_INDEX=full
        self.loaded_at = None
        self.error = None
        self.timings = {"embed": [], "match": []}
        self._loading = False

    # ---- status
    def status(self):
        return dict(model_loaded=self.extractor is not None, loading=self._loading,
                    model=self.model_name, dim=self.emb["dim"] if self.emb else None,
                    n_vectors=self.emb["n"] if self.emb else 0, error=self.error,
                    expected_ms={k: self.expected_ms(k) for k in ("embed", "match")},
                    torch=torch_available() or config.EMBED_FAKE)

    def expected_ms(self, step):
        t = self.timings[step][-20:]
        return int(sum(t) / len(t)) if t else {"embed": 2000, "match": 500}[step]

    def _record(self, step, ms):
        self.timings[step].append(ms)
        self.timings[step] = self.timings[step][-50:]

    # ---- loading
    def warmup(self):
        threading.Thread(target=self._safe_load, name="embed-warmup", daemon=True).start()

    def _safe_load(self):
        try:
            self.ensure_loaded()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.warning("image search unavailable: %s", e)

    def ensure_loaded(self):
        with self.lock:
            emb = db.embedding_model()
            if emb is None:
                self.error = "No embeddings have been loaded (python -m etl.load_embeddings)."
                return False
            if self.extractor is not None and self.loaded_at == emb["loaded_at"]:
                return True
            self._loading = True
            try:
                t = time.time()
                self.emb = emb
                self.sizes = {k: dict(size=tuple(v.get("size", config.EMBED_INPUT.get(k, (200, 200)))),
                                      crop=v.get("crop", config.EMBED_CROP))
                              for k, v in (emb.get("input_sizes") or {}).items()}
                for k in config.KINDS:
                    self.sizes.setdefault(k, dict(size=config.EMBED_INPUT.get(k, (200, 200)),
                                                  crop=config.EMBED_CROP))
                if config.EMBED_FAKE:
                    self.extractor, self.model_name = FakeExtractor(emb["dim"]), "fake"
                else:
                    row = db.model_row(config.EMBED_MODEL or None)
                    if row is None:
                        raise RuntimeError("No model in model_store (python -m etl.load_model).")
                    if not torch_available():
                        raise RuntimeError("torch/timm are not installed in this image.")
                    import torch
                    state = torch.load(io.BytesIO(bytes(row["state"])), map_location="cpu")
                    self.extractor = TorchExtractor(row["arch"], state)
                    self.model_name = row["name"]
                    if self.extractor.dim != emb["dim"]:
                        raise RuntimeError(
                            f"model {row['name']} outputs {self.extractor.dim}-d vectors but the "
                            f"loaded embeddings are {emb['dim']}-d ({emb['name']}). Reload one "
                            f"of them.")
                self.samples = {k: (np.stack([decode(v) for v in vecs]) if vecs else None, oids)
                                for k, (vecs, oids) in db.kind_samples().items()}
                self.centroids = {}
                for k, rows in db.centroids_by_kind().items():
                    self.centroids[k] = (np.stack([decode(r["vec"]) for r in rows]), rows)
                self.full = {}
                if config.EMBED_INDEX == "full":
                    for k in config.KINDS:
                        oids, vecs = db.all_embeddings(k)
                        if oids:
                            self.full[k] = (np.stack([decode(v) for v in vecs]), oids)
                self.loaded_at = emb["loaded_at"]
                self.error = None
                log.info("image search ready: model %s, %d vectors, %d centroids, %.1fs",
                         self.model_name, emb["n"],
                         sum(len(r) for _, r in self.centroids.values()), time.time() - t)
                return True
            finally:
                self._loading = False

    # ---- query
    def embed_all(self, im: Image.Image) -> dict:
        """One vector per distinct preprocessing (kinds sharing a size share a
        pass). Returns {kind: vec}."""
        if not self.ensure_loaded():
            raise RuntimeError(self.error or "image search is not ready")
        t = time.time()
        by_pre, out = {}, {}
        for k, pre in self.sizes.items():
            key = (tuple(pre["size"]), pre["crop"])
            if key not in by_pre:
                by_pre[key] = self.extractor.embed(im, pre["size"], pre["crop"])
            out[k] = by_pre[key]
        self._record("embed", (time.time() - t) * 1000)
        return out

    def kind_scores(self, vecs: dict) -> dict:
        """{kind: mean similarity of the KIND_KNN nearest type samples}."""
        scores = {}
        for k, v in vecs.items():
            M, _ = self.samples.get(k, (None, None))
            if M is None or not len(M):
                continue
            s = M @ v
            top = np.sort(s)[-config.KIND_KNN:]
            scores[k] = float(top.mean())
        return scores

    def match(self, vec: np.ndarray, kind: str, top=config.MATCH_TOP_CLASSES) -> dict:
        t = time.time()
        M, rows = self.centroids.get(kind, (None, []))
        cands = []
        if M is not None and len(rows):
            sims = M @ vec
            for fam in ("pred", "ann"):
                idx = [i for i, r in enumerate(rows) if config.SOURCES[r["src"]]["family"] == fam]
                idx.sort(key=lambda i: -sims[i])
                for i in idx[:top]:
                    r = rows[i]
                    level, uvalue = class_level(r["src"], r["value"])
                    cands.append(dict(src=r["src"], value=uvalue, path=r["value"], kind=r["kind"],
                                      level=level, plate=r["plate"], n=int(r["n"]),
                                      n_books=r["n_books"], y0=r["y0"], y1=r["y1"],
                                      exemplar=r["exemplar"], t_owned=bool(r["t_owned"]),
                                      centroid_sim=float(sims[i])))
        # exact pass on the members of the candidate classes
        members = db.members_with_vectors([(c["src"], c["path"]) for c in cands])
        images = []
        for c in cands:
            ms = members.get((c["src"], c["path"]), [])
            if ms:
                V = np.stack([decode(m["vec"]) for m in ms])
                s = V @ vec
                c["max"], c["mean"] = float(s.max()), float(s.mean())
                j = int(s.argmax())
                c["best_oid"] = ms[j]["oid"]
                for m, si in zip(ms, s):
                    images.append(dict(oid=m["oid"], sim=float(si), src=c["src"], value=c["path"]))
            else:
                c["max"], c["mean"], c["best_oid"] = c["centroid_sim"], c["centroid_sim"], None
        if kind in self.full:                            # exhaustive: every vector of the type
            F, oids = self.full[kind]
            s = F @ vec
            for j in np.argsort(-s)[:60]:
                images.append(dict(oid=oids[j], sim=float(s[j]), src=None, value=None))
        seen, uniq = set(), []
        for im in sorted(images, key=lambda x: -x["sim"]):
            if im["oid"] not in seen:
                seen.add(im["oid"]); uniq.append(im)
        cands.sort(key=lambda c: -c["max"])
        houses = self.house_estimate(cands)
        self._record("match", (time.time() - t) * 1000)
        return dict(classes=cands, images=uniq[:12], houses=houses)

    def house_estimate(self, cands):
        """Similarity-weighted coverage shares of the houses behind the top
        classes (DESIGN §9.4 step 6)."""
        voting = [c for c in cands if c["max"] >= config.MATCH_MIN_SIM][:10]
        if not voting:
            return dict(publisher=[], printer=[], n_classes=0)
        plates = [c["plate"] for c in voting if c.get("plate")]
        shares = db.plate_agent_shares(plates)
        acc = {"publisher": defaultdict(float), "printer": defaultdict(float)}
        names, wsum = {}, 0.0
        for c in voting:
            w = (c["max"] - config.MATCH_MIN_SIM) / (1 - config.MATCH_MIN_SIM)
            wsum += w
            for r in shares.get(c.get("plate"), []):
                acc[r["role"]][r["agent_id"]] += w * r["coverage"]
                names[r["agent_id"]] = r
        out = {}
        for role, d in acc.items():
            items = sorted(d.items(), key=lambda kv: -kv[1])[:8]
            out[role] = [dict(name=names[a]["name"], display=names[a]["display_name"],
                              p=v / wsum) for a, v in items]
        out["n_classes"] = len(voting)
        return out


engine = Engine()


# ---------------------------------------------------------------- queries on disk
def query_dir():
    d = config.CACHE_DIR / "queries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_token(data: bytes) -> str:
    return hashlib.sha256(data + str(time.time()).encode()).hexdigest()[:16]


def save_query_image(data: bytes) -> tuple[str, Image.Image]:
    im = Image.open(io.BytesIO(data))
    im.load()
    im = im.convert("RGB")
    if max(im.size) > config.QUERY_MAX_SIDE:
        im.thumbnail((config.QUERY_MAX_SIDE, config.QUERY_MAX_SIDE), Image.LANCZOS)
    tok = new_token(data)
    im.save(query_dir() / f"{tok}.jpg", "JPEG", quality=90)
    write_state(tok, dict(created=time.time(), step="uploaded", w=im.width, h=im.height))
    prune_queries()
    return tok, im


def load_query_image(tok: str) -> Image.Image | None:
    p = query_dir() / f"{tok}.jpg"
    return Image.open(p).convert("RGB") if p.exists() else None


def read_state(tok: str) -> dict | None:
    p = query_dir() / f"{tok}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def write_state(tok: str, state: dict):
    (query_dir() / f"{tok}.json").write_text(json.dumps(state))


def update_state(tok: str, **kw) -> dict:
    st = read_state(tok) or {}
    st.update(kw)
    write_state(tok, st)
    return st


def prune_queries():
    cutoff = time.time() - config.QUERY_TTL_HOURS * 3600
    try:
        for p in query_dir().iterdir():
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def vec_key(kind):
    return f"vec_{kind}"
