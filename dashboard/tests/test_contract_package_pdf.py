"""Tests for the contract application-package cover sheet:

A curated CONTRACT (e.g. "Ritz water damage remediation") must include
BOTH a contractor and a client/customer signature field, plus an explicit
deposit block (50% of total by default, or the project's first payment-terms
milestone). Permit/COI/change-order packages keep the contractor-only block.

Mirrors test_invoice_terms.py: import the real app.py and exercise the
module-level helpers directly (the PDF route renders to opaque PDF bytes, so
the HTML-building helpers are the unit-testable seam).
"""
import os
import sys

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as appmod


# ── deposit percentage resolution ───────────────────────────────────────────
def test_deposit_pct_defaults_to_50_when_no_terms():
    assert appmod._contract_deposit_pct(None) == 50.0
    assert appmod._contract_deposit_pct({}) == 50.0
    assert appmod._contract_deposit_pct({"milestones": []}) == 50.0


def test_deposit_pct_uses_first_milestone():
    terms = {"preset": "30_30_40", "milestones": [
        {"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30},
        {"label": "Final", "pct": 40}]}
    assert appmod._contract_deposit_pct(terms) == 30.0


# ── deposit block math + rendering ───────────────────────────────────────────
def test_deposit_block_computes_half_of_total():
    html = appmod._contract_deposit_block(10000, 50)
    assert "Deposit Required" in html
    assert "50%" in html
    assert "$5,000.00" in html        # deposit
    assert "$10,000.00" in html       # contract total
    assert "$5,000.00" in html        # balance == deposit here
    assert "Balance" in html


def test_deposit_block_non_half_percentage():
    html = appmod._contract_deposit_block(20000, 30)
    assert "$6,000.00" in html        # 30% deposit
    assert "$14,000.00" in html       # balance


def test_deposit_block_empty_without_amount():
    assert appmod._contract_deposit_block(0, 50) == ""
    assert appmod._contract_deposit_block(None, 50) == ""
    assert appmod._contract_deposit_block("", 50) == ""


# ── signature block: contractor + client for contracts ──────────────────────
def test_signature_block_contract_has_both_parties():
    html = appmod._package_signature_block(
        {"contractor_name": "Sergey Tkach", "client_name": "Ritz LLC"},
        include_client=True)
    assert "Contractor Signature:" in html
    assert "Client Signature:" in html
    assert "Sergey Tkach" in html
    assert "Ritz LLC" in html


def test_signature_block_noncontract_is_contractor_only():
    html = appmod._package_signature_block(
        {"contractor_name": "Sergey Tkach"}, include_client=False)
    assert "Contractor Signature:" in html
    assert "Client Signature:" not in html


def test_signature_block_client_falls_back_to_blank_line():
    html = appmod._package_signature_block(
        {"contractor_name": "Sergey Tkach"}, include_client=True)
    assert "Client Signature:" in html  # present even when client_name unknown


# ── contract scope sourced from the chosen invoice's line items ─────────────
def test_invoice_scope_text_joins_descriptions_in_order():
    items = [
        {"description": "Demo", "qty": 1},
        {"description": "1. Remove baseboard", "qty": 1},
        {"description": "", "qty": 1},           # blank — dropped
        {"description": "  ", "qty": 1},          # whitespace — dropped
        {"description": "2. Strip drywall", "qty": 1},
    ]
    txt = appmod._invoice_scope_text(items)
    assert txt == "Demo\n1. Remove baseboard\n2. Strip drywall"


def test_invoice_scope_text_empty():
    assert appmod._invoice_scope_text([]) == ""
    assert appmod._invoice_scope_text(None) == ""


def test_contract_scope_block_renders_and_escapes():
    html = appmod._contract_scope_block("Install <b>floor</b>\nClean up")
    assert "Scope of Work" in html
    assert "&lt;b&gt;floor&lt;/b&gt;" in html   # html-escaped
    assert "white-space:pre-wrap" in html        # preserves line structure


def test_contract_scope_block_empty():
    assert appmod._contract_scope_block("") == ""
    assert appmod._contract_scope_block(None) == ""


# ── invoice amount-due self-heal (fixes "AMOUNT DUE NOW $0.00") ──────────────
def test_amount_due_recomputed_from_live_total_when_stale():
    inv = {"total": 31599.12, "subtotal": 31599.12, "amount_due": 0.0,
           "milestone_index": 0,
           "terms_snapshot": '{"preset":"50_50","milestones":['
                             '{"label":"Deposit","pct":50},'
                             '{"label":"Completion","pct":50}]}'}
    assert appmod._invoice_amount_due(inv, paid=0.0) == 15799.56


def test_amount_due_subtracts_payments():
    inv = {"total": 31599.12, "amount_due": 0.0, "milestone_index": 0,
           "terms_snapshot": '{"milestones":[{"label":"Deposit","pct":50},'
                             '{"label":"Completion","pct":50}]}'}
    assert appmod._invoice_amount_due(inv, paid=5000.0) == 10799.56


def test_amount_due_final_milestone_clears_balance():
    inv = {"total": 31599.12, "amount_due": 0.0, "milestone_index": 1,
           "terms_snapshot": '{"milestones":[{"label":"Deposit","pct":50},'
                             '{"label":"Completion","pct":50}]}'}
    assert appmod._invoice_amount_due(inv, paid=15799.56) == round(31599.12 - 15799.56, 2)


def test_amount_due_no_terms_uses_stored_value():
    inv = {"total": 1000, "amount_due": 250.0, "milestone_index": -1,
           "terms_snapshot": ""}
    assert appmod._invoice_amount_due(inv) == 250.0
