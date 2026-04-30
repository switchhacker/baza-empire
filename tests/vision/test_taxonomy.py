from dashboard.vision.taxonomy import TAXONOMY, find_node, ancestor_filters


def test_root_paths_exist():
    paths = {n.path for n in TAXONOMY}
    assert "/Inbound" in paths
    assert "/Generated" in paths
    assert "/Scraped" in paths
    assert "/Catalogue" in paths


def test_find_node_returns_correct_node():
    n = find_node("/Catalogue/People/Female")
    assert n is not None
    assert n.path == "/Catalogue/People/Female"


def test_find_node_returns_none_for_unknown():
    assert find_node("/NoSuch/Folder") is None


def test_ancestor_filters_combines_query_dicts():
    # /Catalogue/People/Female should AND its query with parent /Catalogue/People.
    fl = ancestor_filters("/Catalogue/People/Female")
    # 'image_type':'person' from People + 'gender':'female' from Female.
    assert fl.get("image_type") == "person"
    assert fl.get("gender") == "female"
