"""Classifier JSON parsing — robust to extra text, code fences, missing keys."""
import pytest

from dashboard.vision.classifier import parse_classifier_response


def test_parses_clean_json():
    raw = '{"image_type":"person","gender":"female","mood":"smiling",' \
          '"pose":"standing","setting":"beach","parts_visible":["face","eyes"],' \
          '"nsfw":"safe","person_count":"1","caption":"a woman smiling at the beach",' \
          '"tags":"woman,smile,beach"}'
    out = parse_classifier_response(raw)
    assert out["image_type"] == "person"
    assert out["gender"] == "female"
    assert out["parts_visible"] == "face,eyes"
    assert out["caption"].startswith("a woman")


def test_strips_code_fences_and_preamble():
    raw = "Here is the JSON:\n```json\n" \
          '{"image_type":"object","person_count":"0","gender":"unknown",' \
          '"pose":"unknown","mood":"neutral","setting":"indoor",' \
          '"parts_visible":[],"nsfw":"safe","caption":"a chair","tags":"chair"}' \
          "\n```\nDone."
    out = parse_classifier_response(raw)
    assert out["image_type"] == "object"
    assert out["parts_visible"] == ""


def test_missing_required_key_raises():
    raw = '{"image_type":"person"}'
    with pytest.raises(ValueError):
        parse_classifier_response(raw)


def test_invalid_value_coerces_to_unknown():
    raw = '{"image_type":"person","person_count":"1","gender":"alien",' \
          '"pose":"floating","mood":"neutral","setting":"indoor",' \
          '"parts_visible":["face"],"nsfw":"safe","caption":"x","tags":"y"}'
    out = parse_classifier_response(raw)
    assert out["gender"] == "unknown"     # 'alien' not in VOCAB
    assert out["pose"] == "unknown"        # 'floating' not in VOCAB


def test_garbage_input_raises_value_error():
    with pytest.raises(ValueError):
        parse_classifier_response("not json at all")
