import os
from pathlib import Path

# ---------------------------------------------------------------- database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost/ecco")

# ---------------------------------------------------------------- images
# The page image server. id (15 digits, no .TIF) is appended directly:
#   https://oma-sivu.2.rahtiapp.fi/image/025650100000260
IMAGE_BASE = os.environ.get("IMAGE_BASE", "https://oma-sivu.2.rahtiapp.fi/image/")
# Human-facing viewer for a whole page, used for the "see this page in ECCO" link.
PAGE_VIEWER = os.environ.get(
    "PAGE_VIEWER", "https://onko-sivu.2.rahtiapp.fi/ecco?docId={book_id}&page={page:04d}")

# Crop cache. On Rahti this is a PVC mount; if it is missing or read-only the
# app still works, it just refetches every time.
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/tmp/ecco-cache"))
CACHE_MAX_MB = int(os.environ.get("CACHE_MAX_MB", "4096"))
CROP_PAD = float(os.environ.get("CROP_PAD", "0.04"))   # keep 4% margin: chips and
                                                       # cracks at the box edge are
                                                       # what identifies a physical block
THUMB_W = int(os.environ.get("THUMB_W", "260"))
FULL_W = int(os.environ.get("FULL_W", "1200"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "30"))

# ---------------------------------------------------------------- app
SITE_TITLE = os.environ.get("SITE_TITLE", "ECCO Ornament Atlas")
SITE_SUBTITLE = os.environ.get(
    "SITE_SUBTITLE", "Printers' ornaments in Eighteenth Century Collections Online")
KINDS = ["DI", "FT", "HP", "WE"]
KIND_LABEL = {"DI": "Decorated initial", "FT": "Factotum",
              "HP": "Headpiece", "WE": "Woodcut / tailpiece / device"}
KIND_COLOR = {"DI": "#e8833a", "FT": "#e3c41e", "HP": "#4a9d5f", "WE": "#3d7ea6"}

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")   # empty disables admin login
SESSION_COOKIE = "ecco_admin"
SESSION_MAX_AGE = 8 * 3600

BASE_DIR = Path(__file__).resolve().parent
