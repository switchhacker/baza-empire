from dashboard.vision.prompt_map import prompt_for_path


def test_blonde_female():
    p = prompt_for_path("/Catalogue/People/Female/Blonde")
    assert "blonde" in p["prompt"]
    assert "woman" in p["prompt"]
    assert "photorealistic" in p["prompt"]
    assert p["negative"]


def test_face_eye_crop():
    p = prompt_for_path("/Catalogue/Faces/Female/Eyes")
    assert "eye" in p["prompt"]
    assert "close-up" in p["prompt"] or "macro" in p["prompt"]


def test_scene_beach():
    p = prompt_for_path("/Catalogue/Scenes/Beach")
    assert "beach" in p["prompt"]
    assert "no people" in p["prompt"] or "empty" in p["prompt"]
