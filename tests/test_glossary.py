"""Every `?` term must land on its own entry on /about (DESIGN §13.2).

    python -m tests.test_glossary
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

about = (Path(__file__).resolve().parent.parent / "app/templates/about.html").read_text()
ids = set(re.findall(r'id="([^"]+)"', about))
missing = {k: v[1] for k, v in config.GLOSSARY.items() if v[1] not in ids}
toc = set(re.findall(r'<nav class="toc".*?</nav>', about, re.S)[0].split('href="#')[1:])
toc = {t.split('"')[0] for t in toc}
bad_toc = toc - ids
assert not missing, f"glossary anchors missing on /about: {missing}"
assert not bad_toc, f"sidebar links to missing anchors: {bad_toc}"
print(f"glossary: {len(config.GLOSSARY)} terms, sidebar: {len(toc)} links — all anchors present")
