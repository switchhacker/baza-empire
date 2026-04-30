from dashboard.vision.vocab import VOCAB, normalize, REQUIRED_KEYS


def test_required_keys_subset_of_vocab():
    for k in REQUIRED_KEYS:
        assert k in VOCAB, k


def test_normalize_lowercases_and_validates():
    assert normalize("gender", "Female") == "female"
    assert normalize("gender", "MALE") == "male"


def test_normalize_unknown_value_returns_unknown():
    # We never raise on an unexpected value — we coerce to "unknown" so a
    # chatty model doesn't crash the classifier loop.
    assert normalize("gender", "non-binary-ish") == "unknown"


def test_normalize_unknown_key_passthrough_ok():
    # Keys outside the vocab are passed through (e.g. classifier emitted
    # extra keys) — no crash, just lowercase trim.
    assert normalize("custom_key", "  Some Value  ") == "some value"


def test_parts_visible_normalized_to_csv_lowercase():
    assert normalize("parts_visible", ["Face", "Eyes", "Hands"]) == "face,eyes,hands"
    assert normalize("parts_visible", "face, eyes ,hands") == "face,eyes,hands"
