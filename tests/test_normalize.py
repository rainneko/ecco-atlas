"""Run: python -m tests.test_normalize"""
from app import normalize as N


def keys(cell):
    return [k for k, _ in N.norm_agents(cell)]


def main():
    assert keys("Tonson, J.; Draper, S.") == ["tonson", "draper"]
    assert keys("J. and R. Tonson") == ["tonson"]
    assert keys("R. and J. Dodsley") == ["dodsley"]
    assert keys("J. Rivington & Sons") == ["rivington"]
    assert keys("Robinson and Roberts; Clarke and Collins; Baldwin, R.") == \
        ["robinson", "roberts", "clarke", "collins", "baldwin"]
    assert keys("Sword and Buckler Court; Huggonson, J.") == ["huggonson"]
    assert keys("Nutt, ; Gardner, T.; Charlton, ") == ["nutt", "gardner", "charlton"]
    assert keys("Caston, J:; Longman, J:") == ["caston", "longman"]
    assert keys("Lintot, Bernard") == ["lintot"]
    assert keys("Johnston, W. Strahan W") == ["johnston"]
    assert keys(float("nan")) == [] and keys(None) == [] and keys("") == []

    assert N.split_class("C067_01a", "C067") == ("C067", "C067_01", "a")
    assert N.split_class("C001_01", "C001") == ("C001", "C001_01", "")
    assert N.split_class("angel_face_big_vine_01b", "angel_face_big_vine") == \
        ("angel_face_big_vine", "angel_face_big_vine_01", "b")
    assert N.class_path_for("HP-ann", "C067", "C067_01", "a") == "C067/C067_01/a"
    assert N.class_path_for("HP-ann", "C067", "C067_01", "") == "C067/C067_01"
    assert N.class_path_for("DI-ann", "A_Heart", "2000", "") == "A_Heart"

    assert N.parse_label("HP-ann", "C067_05") == ("C067", "C067_05", "")
    assert N.parse_label("HP-ann", "C067_01b") == ("C067", "C067_01", "b")
    assert N.parse_label("HP-ann", "C067/C067_01/c") == ("C067", "C067_01", "c")
    assert N.parse_label("HP-ann", "c", "C067", "C067_01") == ("C067", "C067_01", "c")
    assert N.parse_label("HP-ann", "C200") == ("C200", None, "")
    assert N.parse_label("DI-ann", "A_Heart") == ("A_Heart", None, "")
    for bad in ("", "a b!", "x/y/z/w"):
        try:
            N.parse_label("HP-ann", bad)
            raise AssertionError(bad)
        except ValueError:
            pass

    assert N.norm_cluster("8944.0") == "8944" and N.norm_cluster("nan") is None
    assert N.norm_cluster(-1) is None and N.norm_cluster("0") == "0"
    assert N.plate_label("HP-ann:C067/C067_01/a") == "C067_01a"
    assert N.plate_label("DI-pred:8944") == "DI-8944"
    assert N.plate_label("DI-ann:A_Heart") == "A_Heart"
    assert N.plate_url("HP-ann:C067/C067_01") == "/class/HP-ann/subclass/C067_01"
    assert N.image_id_of("087910190000510.TIF") == "087910190000510"
    assert N.page_of("087910190000510") == 51 and N.book_id_of("087910190000510") == "0879101900"
    assert N.parse_box("[1, 2, 3, 4]") == (1.0, 2.0, 3.0, 4.0) and N.parse_box("[1,2]") is None
    assert abs(N.iou((0, 0, 10, 10), (0, 0, 10, 5)) - 0.5) < 1e-9
    assert sorted(["C067_10", "C067_2"], key=N.natural_key) == ["C067_2", "C067_10"]
    print("normalize: all tests passed")


if __name__ == "__main__":
    main()
