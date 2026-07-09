# tests/test_nav_ahb_tabs.py — AHB123 tabs render from ONE shared list (spec A2)
import os, re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO_ROOT, "dashboard", "templates")

def read(name):
    with open(os.path.join(TPL, name), encoding="utf-8") as f:
        return f.read()

def test_shared_list_defines_priority_order():
    src = read("_ahb_tabs.html")
    keys = re.findall(r"\(\s*'([a-z]+)'", src)
    # Priority items first (spec A1)
    assert keys[:3] == ["email", "projects", "receipts"], keys[:3]
    expected = {"email","projects","receipts","dashboard","clients","treasury",
                "heavyeq","schedule","noted","voice","chatdept","photos",
                "social","reviews","leads","web"}
    assert expected == set(keys)

def test_both_surfaces_import_the_shared_list():
    nav, page = read("_nav.html"), read("ahb123.html")
    assert "_ahb_tabs.html" in nav and "dropdown_links" in nav
    assert "_ahb_tabs.html" in page and "subtab_bar" in page

def test_stale_hand_copied_entries_are_gone():
    nav = read("_nav.html")
    # InvoiceIT/Billing merged into Projects 2026-06-11; dropdown still had them
    for stale in ["InvoiceIT", "/ahb123/invoices", "/ahb123/billing",
                  "/ahb123/estimator", "/ahb123/receipts"]:
        assert stale not in nav, f"stale dropdown entry survived: {stale}"

def test_no_hardcoded_subtab_divs_left_in_ahb123():
    page = read("ahb123.html")
    # The old hand-written bar had ~15 of these; macro renders them now.
    assert page.count('class="sub-tab') <= 1  # only the macro's template string
