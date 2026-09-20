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

KINDS = ["DI", "FT", "HP", "TP"]
KIND_LABEL = {"DI": "Decorated initial", "FT": "Factotum", "HP": "Headpiece",
              "TP": "Tailpiece or printer's device"}
KIND_COLOR = {"DI": "#e8833a", "FT": "#e3c41e", "HP": "#4a9d5f", "TP": "#3d7ea6"}

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
    "TP-pred": dict(kind="TP", family="pred", label="TP prediction",
                    levels=["cluster"], file="TPPD.csv",
                    status="SimCLR (ResNet-18); tailpieces and printer's devices."),
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

# ------------------------------------------------------------------ v2.1: text
# Front-page scope sentence (DESIGN §13.1). Numbers are added from the database.
SCOPE_SENTENCE = os.environ.get("SCOPE_SENTENCE") or (
    "Every page of Eighteenth Century Collections Online — some 200,000 books and "
    "33 million pages, the most complete collection of eighteenth-century printing in "
    "English — was searched for printers' ornaments.")

# Tooltip glossary (DESIGN §13.2): key -> (short text for the hover card, /about anchor)
GLOSSARY = {
    # key -> (the hover text: what you are looking at, in plain words; anchor on /about)
    "DI": ("Decorative initial: a large decorated letter that opens a section of text.", "term-DI"),
    "FT": ("Factotum: a decorative frame with a blank centre, into which any letter could "
           "be set.", "term-FT"),
    "HP": ("Headpiece: an ornament at the head of a chapter or section. It decorates; it "
           "does not illustrate the text.", "term-HP"),
    "TP": ("Tailpieces and printers' devices, counted as one type here. A tailpiece closes "
           "a chapter; a device is the printer's emblem, usually on the title page.",
           "term-TP"),
    "plate": ("One physical woodblock, as far as we can tell: a human variant such as "
              "C067_01a, or one machine cluster. Ownership and lending are worked out per "
              "plate.", "term-plate"),
    "cluster": ("A group of images the model judged to come from the same design. Machine "
                "output, not checked by a person.", "term-cluster"),
    "superclass": ("Designs that share a motif, e.g. C067. One superclass usually holds "
                   "several blocks.", "term-superclass"),
    "subclass": ("One design, e.g. C067_01. Its variants (a, b, …) are separate blocks of "
                 "that design.", "term-subclass"),
    "coverage": ("Percentages: of this class's books with a known imprint, how many name "
                 "the house. A book can name several houses, so they can add up to more "
                 "than 100%.", "term-coverage"),
    "credit": ("The bar: each house's share of all names on the imprints; it adds up to "
               "100%.", "term-credit"),
    "owner": ("The house named in most of a plate's books (by default at least 70%).",
              "term-owner"),
    "borrower": ("A house named in only a few of a plate's books, and never together with "
                 "the owner — the block may have been lent to it.", "term-borrower"),
    "imprint": ("Who published and printed the book, as the title page says (from ESTC). "
                "Names are reduced to surnames, so 'Tonson, J.' and 'J. and R. Tonson' are "
                "one house.", "term-imprint"),
    "strip": ("Each dot is a page with an ornament of that type; hover to preview, click to "
              "open.", "term-strip"),
    "T": ("Tonson or Watts hold the largest share of this plate (or, on a book, the "
          "imprint names them).", "term-T"),
    "place_share": ("Where this class's books were printed. A book has one place, so the "
                    "slices add up to 100%. Click a place to compare its images with the "
                    "others.", "term-place_share"),
    "contrast": ("How differently two designs are spread over cities: 0 = the same cities in "
                 "the same proportions, 1 = no city in common. A high value suggests two "
                 "separate blocks, such as a London original and a Dublin copy.",
                 "term-contrast"),
    "similarity": ("How alike two images look to the model, from 0 to 1. A similar design is "
                   "not proof of the same block.", "term-similarity"),
}

PUBLICATIONS = [
    dict(
        key="dhq2025",
        authors="Ruilin Wang, Lidia Pivovarova, Yann Ryan, Enes Yılandiloğlu, Mikko Tolonen",
        title="Image Reuse in Eighteenth-Century Book History: Large-Scale Data-Driven Study "
              "of Headpiece Ornament Variants",
        venue="Digital Humanities Quarterly 19(4)", year=2025,
        url="https://researchportal.helsinki.fi/en/publications/b3596d5a-ec7f-4577-a588-710c47cd95d1",
        doi="",
        summary="Traces the reuse of headpieces across ECCO and shows that variants of one "
                "design reveal networks of printers and publishers beyond one-to-one "
                "ownership.",
        bibtex="""@article{wang2025imagereuse,
  title   = {Image Reuse in Eighteenth-Century Book History: Large-Scale Data-Driven Study of Headpiece Ornament Variants},
  author  = {Wang, Ruilin and Pivovarova, Lidia and Ryan, Yann and Y{\\i}landilo{\\u{g}}lu, Enes and Tolonen, Mikko},
  journal = {Digital Humanities Quarterly},
  volume  = {19},
  number  = {4},
  year    = {2025},
  issn    = {1938-4122},
  publisher = {Alliance of Digital Humanities Organisations}
}"""),
    dict(
        key="scia2025",
        authors="Ruilin Wang, Lidia Pivovarova, Yann Ciarán Ryan, Mikko Tolonen",
        title="Semi-Supervised Contrastive Training for Similar Image Identification in a "
              "Large Collection of Historical Books",
        venue="Image Analysis (SCIA 2025), Lecture Notes in Computer Science, Springer, "
              "pp. 401–414", year=2025,
        url="https://doi.org/10.1007/978-3-031-95911-0_28",
        doi="10.1007/978-3-031-95911-0_28",
        summary="The image model behind the machine clusters: contrastive representation "
                "learning with a semi-supervised fine-tuning step, 94% precision on "
                "headpieces.",
        bibtex="""@inproceedings{wang2025semisupervised,
  title     = {Semi-Supervised Contrastive Training for Similar Image Identification in a Large Collection of Historical Books},
  author    = {Wang, Ruilin and Pivovarova, Lidia and Ryan, Yann Ciar{\\'a}n and Tolonen, Mikko},
  booktitle = {Image Analysis. SCIA 2025},
  series    = {Lecture Notes in Computer Science},
  editor    = {Petersen, J. and Dahl, V. A.},
  publisher = {Springer},
  pages     = {401--414},
  year      = {2025},
  doi       = {10.1007/978-3-031-95911-0_28},
  isbn      = {978-3-031-95910-3}
}"""),
]

# Fill in the addresses (DESIGN §13.3). An empty email renders the name without a link.
PEOPLE = [
    dict(name="Ruilin Wang", role="doctoral researcher, computer vision and the atlas", email=""),
    dict(name="Lidia Pivovarova", role="supervisor, machine learning", email=""),
    dict(name="Yann Ryan", role="book history", email=""),
    dict(name="Mikko Tolonen", role="principal investigator", email=""),
]
GROUP = "Helsinki Computational History Group (COMHIS), University of Helsinki"

# ------------------------------------------------------------------ v2.1: image search (§9)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "")        # "" = the newest in model_store
EMBED_INDEX = os.environ.get("EMBED_INDEX", "centroid")  # or "full" (exhaustive)
EMBED_FAKE = os.environ.get("EMBED_FAKE", "") == "1"    # tests only: no torch needed
# Query preprocessing per type, overridden by what embedding_model recorded at load time.
EMBED_INPUT = {"HP": (100, 400), "DI": (200, 200), "FT": (200, 200), "TP": (200, 200)}
EMBED_CROP = "full"          # "full" = plain resize to the input size (default since 2026-09-19);
                             # "square" = the old docret eval transform (centre square crop)
KIND_SAMPLE_N = 300          # ornaments per type for the type check
KIND_KNN = 10                # neighbours averaged per type
MATCH_TOP_CLASSES = 25       # centroids kept per family before the exact pass
MATCH_MIN_SIM = 0.50         # classes below this do not vote for houses
QUERY_TTL_HOURS = 24
QUERY_MAX_BYTES = 8 * 1024 * 1024
QUERY_MAX_SIDE = 1600

# ------------------------------------------------------------------ v2.1: places and reprints (§12)
PLACE_DEFAULTS = {"min_books": 5, "d": 0.6}   # books with a known place per design; contrast flag
PAIR_MIN_SHARED = 3          # plates two books must share to be a pair (§12.4)
PAIR_MAX_PLATE_BOOKS = 30    # plates in more books than this do not generate pairs
LINK_MIN_SIM = 0.85          # default threshold of etl.link_clusters (§12.6)
LINK_MAX_GROUP = 40          # clusters shown as one linked group at most
