import os
from pathlib import Path

# ---------------------------------------------------------------- database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost/ecco")
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))

# ---------------------------------------------------------------- images
# Page image server; the 15-digit image id is appended directly.
IMAGE_BASE = os.environ.get("IMAGE_BASE", "https://oma-sivu.2.rahtiapp.fi/image/")
PAGE_VIEWER = os.environ.get(
    "PAGE_VIEWER", "https://onko-sivu.2.rahtiapp.fi/ecco?docId={book_id}&page={page:04d}")

# Non-annotated crops: small LRU disk cache, counted in files (see DESIGN §6).
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/tmp/ecco-cache"))
CACHE_MAX_ITEMS = int(os.environ.get("CACHE_MAX_ITEMS", "1000"))
CROP_PAD = float(os.environ.get("CROP_PAD", "0.04"))   # keep 4% margin: chips and cracks
                                                       # at the box edge identify a block
WIDTHS = (300, 900, 1400)          # every requested width snaps up to one of these
STORE_FULL_W = 1400                # annotated crops stored at native size up to this
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "30"))
HERO_IMAGE = "hero.jpg"            # optional, app/static/hero.jpg
HERO_CREDIT = os.environ.get(
    "HERO_CREDIT", "Printing house. Diderot & d'Alembert, Encyclopédie, Recueil de planches, vol. VII")

# ---------------------------------------------------------------- site
SITE_TITLE = os.environ.get("SITE_TITLE", "ECCO Ornament Atlas")
SITE_SUBTITLE = os.environ.get(
    "SITE_SUBTITLE", "Printers' ornaments in Eighteenth Century Collections Online")

KINDS = ["DI", "FT", "HP", "WE"]
KIND_LABEL = {"DI": "Decorated initial", "FT": "Factotum", "HP": "Headpiece",
              "WE": "Woodcut, tailpiece or device"}
KIND_COLOR = {"DI": "#e8833a", "FT": "#e3c41e", "HP": "#4a9d5f", "WE": "#3d7ea6"}

# ---------------------------------------------------------------- data sources
# One entry per CSV family. Order = display order and load order within a family.
#   family: "ann" (human annotation) or "pred" (machine cluster)
#   levels: class levels this source exposes; pred sources always have ["cluster"]
SOURCES = {
    "HP-ann": dict(kind="HP", family="ann", label="HP annotation",
                   levels=["superclass", "subclass", "variant"],
                   file="HP_concat_annotation.csv",
                   status="DHQ-paper subset + Tonson variants; checked in 3 rounds."),
    "DI-ann": dict(kind="DI", family="ann", label="DI annotation",
                   levels=["superclass"], file="DI_annotation.csv",
                   status="Student annotation, checked in 2 rounds. Superclass only."),
    "FT-ann": dict(kind="FT", family="ann", label="FT annotation",
                   levels=["superclass"], file="FT_annotation.csv",
                   status="Awaiting review; estimated 85–90% accurate."),
    "HP-pred": dict(kind="HP", family="pred", label="HP prediction",
                    levels=["cluster"], file="HP.csv",
                    status="SimCLR (ResNet-18), broad/wide headpieces (group 1)."),
    "DI-pred": dict(kind="DI", family="pred", label="DI prediction",
                    levels=["cluster"], file="DI.csv",
                    status="MoCo-v3; 94% ARI on held-out test data."),
    "FT-pred": dict(kind="FT", family="pred", label="FT prediction",
                    levels=["cluster"], file="FT.csv",
                    status="SimCLR (ResNet-18); re-prediction with MoCo-v3 planned."),
    "WE-pred": dict(kind="WE", family="pred", label="WE / TP / PD prediction",
                    levels=["cluster"], file="WETPPD.csv",
                    status="SimCLR (ResNet-18); woodcuts, tailpieces, printer's devices."),
}
ANN_SOURCES = [k for k, v in SOURCES.items() if v["family"] == "ann"]
PRED_SOURCES = [k for k, v in SOURCES.items() if v["family"] == "pred"]
LEVEL_LABEL = {"superclass": "superclass", "subclass": "subclass",
               "variant": "variant", "cluster": "cluster"}


def ann_source_for_kind(kind):
    """Human-label source that a correction of a `kind` ornament is filed under."""
    return f"{kind}-ann" if f"{kind}-ann" in SOURCES else None


# ---------------------------------------------------------------- analysis defaults
OWNER_MIN_BOOKS = int(os.environ.get("OWNER_MIN_BOOKS", "3"))
COHOLD_MIN_SHARE = 0.20
LENDING_DEFAULTS = dict(owner=0.70, lo=0.05, hi=0.20, min_books=5, gap=5)
SIM_SCORES = {"variant": 1.00, "subclass": 0.98, "superclass": 0.90, "cluster": 0.95}
TONSON_KEYS = ("tonson", "watts")
NOISE_CLUSTERS = {"-1", ""}        # cluster ids that mean "not clustered"

# ---------------------------------------------------------------- admin
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")   # empty disables the shared password
ADMIN_USERS = os.environ.get("ADMIN_USERS", "")         # optional "alice:pw1,bob:pw2"
SESSION_COOKIE = "ecco_admin"
SESSION_MAX_AGE = 8 * 3600

BASE_DIR = Path(__file__).resolve().parent
