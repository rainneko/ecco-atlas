"""Image search (DESIGN §9): embed an uploaded ornament with the project's own
model and rank the atlas's classes by similarity.

v2.5: everything here is optional. With no embeddings loaded the engine says
so and every caller degrades to a sentence (§9.0). The engine has two parts:
the *index* (type samples, centroids — needs only stored vectors) and the
*extractor* (needs torch and a stored model). Searching with an ornament that
already has a stored vector needs only the index.

torch/timm are imported only inside the extractor. With EMBED_FAKE=1 a
deterministic random-projection "model" stands in for torch (tests).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
from collections import defaultdict

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
def flatten_white(im: Image.Image) -> Image.Image:
    """Transparent pixels become white paper, not black (v2.5)."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def crop_like_eval(im: Image.Image, size, crop="full") -> Image.Image:
    """Query preprocessing, identical to how the stored vectors were made:
    grayscale -> RGB repeat (the training scans are grayscale; v2.5 also turns
    colour uploads grey), then Resize(size) ("full") or the old centre-square
    crop ("square")."""
    h, w = size
    im = flatten_white(im).convert("L").convert("RGB")
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
    """Deterministic stand-in: random projection of a 32×32 grey thumbnail."""
    arch = "fake"

    def __init__(self, dim):
        self.dim = dim
        rng = np.random.default_rng(7)
        self.P = rng.standard_normal((32 * 32, dim)).astype(np.float32) / 32.0

    def embed(self, im: Image.Image, size, crop="full") -> np.ndarray:
        x = crop_like_eval(im, size, crop).convert("L").resize((32, 32), Image.BILINEAR)
        px = np.asarray(x, dtype=np.float32).ravel() / 255.0
        v = (px - px.mean()) @ self.P
        return l2(v)


class TorchExtractor:
    """The frozen backbone, rebuilt from the state dict stored by
    `python -m etl.load_model`."""

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
        if missing:
            # v2.5: a wrong key prefix used to give a random network silently
            raise RuntimeError(
                f"stored model {arch} does not fit the backbone: {len(missing)} weights missing "
                f"(e.g. {missing[:3]}). Reload it with python -m etl.load_model.")
        if unexpected:
            log.info("model %s: %d unused keys ignored (e.g. heads)", arch, len(unexpected))
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
    """(level, value-for-URL) of a centroid."""
    if config.SOURCES[src]["family"] == "pred":
        return "cluster", value
    parts = value.split("/")
    if len(parts) >= 3 and parts[2]:
        return "variant", value
    if len(parts) == 2:
        return "subclass", parts[1]
    return "superclass", parts[0]


NO_EMBEDDINGS = ("No embeddings have been loaded yet (python -m etl.load_model, then "
                 "etl.load_embeddings).")


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.extractor = None
        self.model_name = None
        self.emb = None            # embedding_model row
        self.sizes = {}            # kind -> {"size": (h, w), "crop": "full"}
        self.samples = {}          # kind -> (matrix, oids)
        self.centroids = {}        # kind -> (matrix, rows)
        self.full = {}             # kind -> (matrix, oids) when EMBED_INDEX=full
        self.index_at = None       # embedding_model.loaded_at of the loaded index
        self.loaded_at = None      # ... of the index the extractor was checked against
        self.error = None
        self.timings = {"embed": [], "match": []}
        self._loading = False
        self._last_try = 0.0

    # ---- status
    def status(self):
        if (self.extractor is None or self.index_at is None) and not self._loading and \
                time.time() - self._last_try > 30:
            self._last_try = time.time()
            self.warmup()
        return dict(model_loaded=self.extractor is not None,
                    index_ready=self.index_at is not None,
                    loading=self._loading,
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
        self._last_try = time.time()
        try:
            self.ensure_loaded()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "does not exist" in msg and "relation" in msg:
                msg = NO_EMBEDDINGS
            self.error = msg
            log.info("image search unavailable: %s", msg.splitlines()[0])

    def _ensure_index_locked(self):
        emb = db.embedding_model()
        if emb is None or not emb.get("n"):
            self.error = NO_EMBEDDINGS
            self.index_at = None
            return False
        if self.index_at == emb["loaded_at"]:
            return True
        t = time.time()
        self.emb = emb
        self.sizes = {k: dict(size=tuple(v.get("size", config.EMBED_INPUT.get(k, (200, 200)))),
                              crop=v.get("crop", config.EMBED_CROP))
                      for k, v in (emb.get("input_sizes") or {}).items()}
        for k in config.KINDS:
            self.sizes.setdefault(k, dict(size=config.EMBED_INPUT.get(k, (200, 200)),
                                          crop=config.EMBED_CROP))
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
        self.index_at = emb["loaded_at"]
        log.info("embedding index ready: %d vectors, %d centroids, %.1fs", emb["n"],
                 sum(len(r) for _, r in self.centroids.values()), time.time() - t)
        return True

    def ensure_index(self):
        """Samples and centroids only; enough to search with a stored vector."""
        with self.lock:
            try:
                return self._ensure_index_locked()
            except Exception as e:  # noqa: BLE001
                self.error = NO_EMBEDDINGS if "does not exist" in str(e) else str(e)
                return False

    def ensure_loaded(self):
        """Index and extractor; needed to embed a new image."""
        with self.lock:
            if not self._ensure_index_locked():
                return False
            if self.extractor is not None and self.loaded_at == self.index_at:
                return True
            self._loading = True
            try:
                if config.EMBED_FAKE:
                    self.extractor, self.model_name = FakeExtractor(self.emb["dim"]), "fake"
                else:
                    row = db.model_row(config.EMBED_MODEL or None)
                    if row is None:
                        raise RuntimeError("No model in model_store (python -m etl.load_model).")
                    if not torch_available():
                        raise RuntimeError("torch/timm are not installed in this image.")
                    import torch
                    state = torch.load(io.BytesIO(bytes(row["state"])), map_location="cpu")
                    ext = TorchExtractor(row["arch"], state)
                    if ext.dim != self.emb["dim"]:
                        raise RuntimeError(
                            f"model {row['name']} outputs {ext.dim}-d vectors but the loaded "
                            f"embeddings are {self.emb['dim']}-d ({self.emb['name']}). "
                            f"Reload one of them.")
                    self.extractor, self.model_name = ext, row["name"]
                self.loaded_at = self.index_at
                self.error = None
                log.info("image search ready: model %s", self.model_name)
                return True
            finally:
                self._loading = False

    # ---- query
    def embed_all(self, im: Image.Image) -> dict:
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
            fam = np.array([config.SOURCES[r["src"]]["family"] == "pred" for r in rows])
            for want in (True, False):
                idx = np.nonzero(fam == want)[0]
                if not len(idx):
                    continue
                k = min(top, len(idx))
                best = idx[np.argpartition(-sims[idx], k - 1)[:k]]
                for i in best[np.argsort(-sims[best])]:
                    r = rows[i]
                    level, uvalue = class_level(r["src"], r["value"])
                    cands.append(dict(src=r["src"], value=uvalue, path=r["value"], kind=r["kind"],
                                      level=level, plate=r["plate"], n=int(r["n"]),
                                      n_books=r["n_books"], y0=r["y0"], y1=r["y1"],
                                      exemplar=r["exemplar"], t_owned=bool(r["t_owned"]),
                                      centroid_sim=float(sims[i])))
        members = db.members_with_vectors([(c["src"], c["path"]) for c in cands])
        images = []
        for c in cands:
            ms = members.get((c["src"], c["path"]), [])
            if ms:
                V = np.stack([decode(m["vec"]) for m in ms])
                s = V @ vec
                c["max"], c["mean"] = float(s.max()), float(s.mean())
                c["best_oid"] = ms[int(s.argmax())]["oid"]
                for m, si in zip(ms, s):
                    images.append(dict(oid=m["oid"], sim=float(si), src=c["src"], value=c["path"]))
            else:
                c["max"], c["mean"], c["best_oid"] = c["centroid_sim"], c["centroid_sim"], None
        if kind in self.full:
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
        return dict(classes=cands, images=uniq[:13], houses=houses)

    def house_estimate(self, cands):
        voting = [c for c in cands if c["max"] > config.MATCH_MIN_SIM][:10]
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
                              p=v / wsum) for a, v in items] if wsum else []
        out["n_classes"] = len(voting)
        return out


engine = Engine()


def similar_to_ornament(oid, kind, k=3):
    """Nearest ornaments by stored vector (ornament page, 'Embedding' tab).
    Returns [] when anything is missing; never raises."""
    try:
        blob = db.ornament_vector(oid)
        if blob is None or not engine.ensure_index():
            return []
        res = engine.match(decode(blob), kind)
        hits = [i for i in res["images"] if i["oid"] != oid][:k]
        brief = db.ornaments_brief([h["oid"] for h in hits])
        out = []
        for h in hits:
            b = brief.get(h["oid"], {})
            out.append(dict(oid=h["oid"], label=b.get("label"), kind=b.get("kind"),
                            book_id=b.get("book_id"), year=b.get("year"),
                            full_title=b.get("full_title"), score=h["sim"],
                            reason="image embedding"))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("embedding similarity failed for %s: %s", oid, e)
        return []


# ---------------------------------------------------------------- queries on disk
def query_dir():
    d = config.CACHE_DIR / "queries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_token(data: bytes) -> str:
    return hashlib.sha256(data + str(time.time()).encode()).hexdigest()[:16]


def save_query_image(data: bytes) -> tuple[str, Image.Image]:
    im = Image.open(io.BytesIO(data))
    if im.width * im.height > config.QUERY_MAX_PIXELS:      # before decoding (v2.5)
        raise ValueError("image has too many pixels")
    im.load()
    im = flatten_white(im)
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
