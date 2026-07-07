from browser.gate import is_gated_click, is_gated_press, is_gated_goto


def test_submit_button_gated():
    assert is_gated_click({"tag": "button", "type": "submit", "text": "Go",
                           "in_form": True, "form_method": "post"})


def test_texty_verbs_gated():
    for text in ("Send message", "Buy now", "Delete account", "Confirm order",
                 "Publish post", "Pay $50"):
        assert is_gated_click({"tag": "a", "type": "", "text": text,
                               "in_form": False, "form_method": ""}), text


def test_plain_link_not_gated():
    assert not is_gated_click({"tag": "a", "type": "", "text": "Next page",
                               "in_form": False, "form_method": ""})
    assert not is_gated_click({"tag": "a", "type": "", "text": "Documentation",
                               "in_form": False, "form_method": ""})


def test_unknown_element_gated():
    assert is_gated_click(None)


def test_press_enter_in_post_form_gated():
    active = {"tag": "input", "type": "text", "text": "", "in_form": True,
              "form_method": "post"}
    assert is_gated_press("Enter", active)


def test_press_enter_in_get_form_free():
    active = {"tag": "input", "type": "text", "text": "", "in_form": True,
              "form_method": "get"}
    assert not is_gated_press("Enter", active)      # search boxes stay free
    assert not is_gated_press("Enter", None)
    assert not is_gated_press("Tab", active)


def test_goto_with_query_string_gated():
    assert is_gated_goto("https://x.test/cart?action=delete")
    assert is_gated_goto("https://mail.test/unsubscribe?token=abc")


def test_goto_with_mutation_verb_no_query_gated():
    assert is_gated_goto("https://x.test/cart/checkout/confirm")
    assert is_gated_goto("https://x.test/account/delete")


def test_goto_plain_navigation_not_gated():
    assert not is_gated_goto("https://example.com")
    assert not is_gated_goto("https://example.com/docs/page")
    assert not is_gated_goto(None)
    assert not is_gated_goto("")
