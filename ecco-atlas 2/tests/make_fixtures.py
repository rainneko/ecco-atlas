"""Synthetic CSVs in the exact layouts of the seven real files.

    python -m tests.make_fixtures /tmp/fx

Each file starts with the literal sample rows from the data description, then
generated rows. Planted cases the smoke test checks:
  * plate HP-ann C005/C005_01/a: 10 books, Tonson on 8, one Lintot-only book
    inside Tonson's years (-> "Lending"), one Dodsley-only book 30 years later
    (-> "Later holder?")
  * WETPPD rows that duplicate HP rows exactly, by IoU ~0.95, and within file
  * HP_concat rows whose boxes are shifted by a pixel (-> attached by IoU)
"""
import csv
import random
import sys
from pathlib import Path

R = random.Random(7)
PUBS = ["Tonson, J.", "Watts, J.", "Draper, S.", "Lintot, Bernard", "Dodsley, R.",
        "Knapton, J.; Knapton, P.", "Rivington, C.", "Robinson and Roberts", "Cox, T.",
        "Longman, T.", "Millar, A.", "Osborne, T.", "Innys, W.", "Hitch, C."]
TITLES = ["The fable of the bees", "Poems on several occasions", "A new system of geography",
          "Sermons preached upon several occasions", "The works of Mr. Alexander Pope",
          "An essay on man", "The history of England", "A treatise of military discipline",
          "The spectator", "Letters written to and for particular friends"]


def book_id(i):
    return f"{100000 + i:07d}100"            # 10 digits


def image_id(bid, page):
    return f"{bid}{page:04d}0"


def box(w=1200, h=400):
    x1, y1 = R.uniform(20, 200), R.uniform(20, 1500)
    return [round(x1, 6), round(y1, 6), round(x1 + R.uniform(200, w), 6),
            round(y1 + R.uniform(150, h), 6)]


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    books = {}
    for i in range(240):
        pub = R.choice(PUBS)
        books[book_id(i)] = dict(title=f"{R.choice(TITLES)}. Vol. {i % 7 + 1}",
                                 year=R.randint(1705, 1790), pubs=pub,
                                 prns=R.choice(["", "", "Bowyer, W.", "Hughs, J."]),
                                 estc=f"T{100000 + i}", pages=R.randint(80, 600))
    bids = list(books)
    # planted lending plate: C005_01a in 10 books
    lend = bids[:10]
    for j, b in enumerate(lend):
        books[b].update(year=1730 + j, pubs="Tonson, J.", prns="")
    books[lend[8]].update(pubs="Lintot, Bernard", year=1734)
    books[lend[9]].update(pubs="Dodsley, R.", year=1770)
    books[bids[20]].update(title="The fable of the bees: or, private vices, publick benefits")

    # ---------------------------------------------------------------- HP.csv (pred)
    hp = []                      # (image_id, box, cluster, sup, sub)
    for b in bids:
        for _ in range(R.randint(3, 14)):
            p = R.randint(1, books[b]["pages"])
            sup = f"C{R.choice([i for i in range(1, 21) if i != 5]):03d}"
            sub = f"{sup}_{R.randint(1, 3):02d}{R.choice(['', 'a', 'b'])}"
            hp.append((image_id(b, p), box(), R.randint(0, 150), sup, sub))
    for j, b in enumerate(lend):
        hp.append((image_id(b, 3), box(), 999, "C005", "C005_01a"))
    head = ["", "id", "labels", "boxes", "path", "HC0.04", "url", "book_id", "ESTCID",
            "fullTitle", "year", "publishers", "printers", "is_tonson"]
    rows = [["57272", "082720180002240.TIF", "7",
             "[147.89393615722656, 24.646053314208984, 918.4766845703125, 196.8603057861328]",
             "/scratch/x.jpg", "0", "https://onko-sivu.2.rahtiapp.fi/ecco?docId=0827201800&page=0224",
             "827201800", "T131919", "A new manual of devotions, in three parts.", "1713",
             "Clements, H.; Taylor, W.; King, C.; Barker, B.", "", "0"]]
    for k, (iid, bx, c, _, _) in enumerate(hp):
        bk = books[iid[:10]]
        rows.append([k, iid + ".TIF", 7, str(bx), "/scratch/p.jpg", c, "u", int(iid[:10]),
                     bk["estc"], bk["title"], bk["year"], bk["pubs"], bk["prns"], 0])
    write(out / "HP.csv", head, rows)

    # ------------------------------------------------ HP_concat_annotation.csv (ann)
    head = ["", "id", "boxes", "path", "book_id", "url", "ESTCID", "fullTitle", "year",
            "is_tonson", "publishers", "printers", "subclass", "superclass", "source",
            "page_hit", "book_hit", "year_span", "is_tonson_superclass", "work_id",
            "annotation_name"]
    rows = [[0, "087910190000510.TIF",
             "[51.36695861816406, 285.26824951171875, 1150.2264404296875, 599.1310424804688]",
             "/scratch/y.jpg", "879101900", "u", "N020711",
             "The principles of the Muggletonians asserted", "1735", "0", "Cox, T.", "",
             "C001_01", "C001", "hp", 83, 58, "1735-1790", 0, "",
             "45_degree_wing_bird_facing_right_podium"]]
    ann_hp = [h for h in hp if h[2] == 999] + R.sample([h for h in hp if h[2] != 999], 500)
    for k, (iid, bx, _, sup, sub) in enumerate(ann_hp):
        bk = books[iid[:10]]
        if k % 10 == 3:                                   # shifted box -> IoU attach
            bx = [bx[0] + 1.3, bx[1] - 0.7, bx[2] + 0.9, bx[3] + 1.1]
        rows.append([k + 1, iid + ".TIF", str(bx), "/p.jpg", int(iid[:10]), "u", bk["estc"],
                     bk["title"], bk["year"], 0, bk["pubs"], bk["prns"], sub, sup,
                     "tonson" if k % 3 else "hp", 1, 1, "", 0, "", "old_name"])
    for k in range(30):                                   # new detections, no prediction
        b = R.choice(bids)
        bk = books[b]
        rows.append([900 + k, image_id(b, 999 - k) + ".TIF", str(box()), "/p.jpg", int(b), "u",
                     bk["estc"], bk["title"], bk["year"], 0, bk["pubs"], bk["prns"],
                     "C019_01", "C019", "tonson", 1, 1, "", 0, "", "x"])
    write(out / "HP_concat_annotation.csv", head, rows)

    # ---------------------------------------------------------------- DI.csv (pred)
    di = []
    for b in bids:
        for _ in range(R.randint(2, 8)):
            di.append((image_id(b, R.randint(1, books[b]["pages"])), box(300, 300),
                       R.randint(1, 400)))
    extra_di_books = [f"{900000 + i:07d}100" for i in range(15)]    # DI-only books, no metadata
    for b in extra_di_books:
        for _ in range(4):
            di.append((image_id(b, R.randint(1, 200)), box(300, 300), R.randint(1, 400)))
    head = ["", "id", "labels", "boxes", "path", "url", "book_id", "ESTCID", "fullTitle",
            "is_tonson", "HC0.12", "img_path", "page", "totalPages", "page_loc%", "img_path(ECCO)"]
    rows = [[0, "031980040000060.TIF", 14,
             "[26.451858520507812, 1090.013427734375, 296.2374267578125, 1359.228515625]",
             "/p.jpg", "u", 319800400, "T144132", "Memoirs and reflections upon the reign", 0,
             2076, "/c.jpg", 6, 461, 1.3, "/c2.jpg"]]
    for k, (iid, bx, c) in enumerate(di):
        bk = books.get(iid[:10], dict(estc="", title="An untitled DI-only book", pages=300))
        rows.append([k + 1, iid + ".TIF", 14, str(bx), "/p.jpg", "u", int(iid[:10]), bk["estc"],
                     bk["title"], 0, f"{c}.0" if k % 5 == 0 else c, "/c.jpg",
                     int(iid[10:14]), bk["pages"], 1.0, "/c2.jpg"])
    write(out / "DI.csv", head, rows)

    # -------------------------------------------------------- DI_annotation.csv (ann)
    head = ["", "superclass", "subclass", "url", "type_", "id", "path", "Init_cluster", "book_id",
            "ESTCID", "fullTitle", "year", "publishers", "printers", "labels", "page_labels",
            "scores", "boxes", "folder", "index", "crop_path"]
    rows = [[0, "A_Heart", 2000, "https://onko-sivu.2.rahtiapp.fi/ecco?docId=0206101100&page=0009",
             "DI", "020610110000090.TIF", "/p.jpg", 5555, 206101100, "T036235",
             "Hippocrates upon air, water, and situation", 1734, "Watts, J.", "", 14, "[7, 14]",
             0.997, "[105.13931274414062, 869.4332275390625, 341.73590087890625, 1120.4564208984375]",
             753, 49, "/c.jpg"]]
    for k, (iid, bx, c) in enumerate(R.sample(di, 200)):
        bk = books.get(iid[:10], dict(estc="", title="An untitled DI-only book", year="",
                                      pubs="", prns=""))
        rows.append([k + 1, R.choice(["A_Heart", "B_Lion", "T_Vase", "S_Angel"]), 2000, "u", "DI",
                     iid + ".TIF", "/p.jpg", 5555, int(iid[:10]), bk["estc"], bk["title"],
                     bk.get("year", ""), bk.get("pubs", ""), bk.get("prns", ""), 14, "[14]",
                     0.99, str(bx), 1, k, "/c.jpg"])
    write(out / "DI_annotation.csv", head, rows)

    # ---------------------------------------------------------------- FT.csv (pred)
    head = ["Unnamed: 0", "id", "labels", "boxes", "path", "url", "HC0.03", "book_id", "ESTCID",
            "fullTitle", "is_tonson"]
    rows = [[0, "147110010002570.TIF", 15,
             "[76.46778869628906, 2093.472900390625, 538.1490478515625, 2563.308837890625]",
             "/p.jpg", "u", 1957, 1471100100, "N61392", "A new directory for the East-Indies", 0]]
    for k in range(700):
        b = R.choice(bids)
        rows.append([k + 1, image_id(b, R.randint(1, books[b]["pages"])) + ".TIF", 15,
                     str(box(400, 400)), "/p.jpg", "u", R.randint(1, 300), int(b),
                     books[b]["estc"], books[b]["title"], 0])
    write(out / "FT.csv", head, rows)

    # -------------------------------------------------------- FT_annotation.csv (ann)
    head = ["superclass", "subclass?", "url", "type_", "id", "path_ann", "book_id", "ESTCID",
            "fullTitle", "year", "publishers", "printers", "_ann", "n_ann_on_page", "labels",
            "page_labels", "scores", "boxes", "folder", "index", "path_det", "img_path", "group",
            "index_emb", "category", "_row", "n_box_on_page", "assign_method", "assign_score",
            "assign_margin", "n_candidates"]
    ft_rows = [r for r in rows[1:]][:60]
    out_rows = []
    for r in ft_rows:
        b = r[1][:10]
        out_rows.append([10100, 10100, "u", "FT", r[1], "/p.jpg", int(b), books[b]["estc"],
                         books[b]["title"], books[b]["year"], books[b]["pubs"], books[b]["prns"],
                         1, 1, 15, "[15]", 0.99, r[3], 1, 1, "/p", "/i", 1, 1, "FT", 1, 1,
                         "unique", "", "", ""])
    write(out / "FT_annotation.csv", head, out_rows)

    # ---------------------------------------------------------------- WETPPD.csv (pred)
    head = ["", "id", "labels", "boxes", "path", "HC0.015", "url", "book_id", "ESTCID",
            "fullTitle", "year", "is_tonson", "publishers", "printers", "ornament_path"]
    rows = []
    k = 0
    for b in R.sample(bids, 120):                              # genuine WE ornaments
        bk = books[b]
        for _ in range(2):
            rows.append([k, image_id(b, R.randint(1, bk["pages"])) + ".TIF", 12,
                         str(box(1500, 1500)), "/p.jpg", R.randint(0, 60), "u", int(b),
                         bk["estc"], bk["title"], bk["year"], 0, bk["pubs"], bk["prns"], "/o.jpg"])
            k += 1
    for iid, bx, *_ in R.sample(hp, 150):                      # exact duplicates of HP
        bk = books[iid[:10]]
        rows.append([k, iid + ".TIF", 10, str(bx), "/p.jpg", 5, "u", int(iid[:10]), bk["estc"],
                     bk["title"], bk["year"], 0, bk["pubs"], bk["prns"], "/o.jpg"])
        k += 1
    for iid, bx, *_ in R.sample(hp, 150):                      # IoU ~0.95 duplicates of HP
        bk = books[iid[:10]]
        w, h = bx[2] - bx[0], bx[3] - bx[1]
        nb = [bx[0] + 0.01 * w, bx[1], bx[2] + 0.01 * w, bx[3] - 0.02 * h]
        rows.append([k, iid + ".TIF", 10, str(nb), "/p.jpg", 6, "u", int(iid[:10]), bk["estc"],
                     bk["title"], bk["year"], 0, bk["pubs"], bk["prns"], "/o.jpg"])
        k += 1
    rows += [list(r) for r in rows[:100]]                      # within-file duplicates
    write(out / "WETPPD.csv", head, rows)
    print(f"fixtures written to {out}: HP {len(hp)}, HP_concat {len(ann_hp) + 31}, DI {len(di)}, "
          f"WE {len(rows)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fx")
