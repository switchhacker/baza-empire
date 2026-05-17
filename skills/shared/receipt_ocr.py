#!/usr/bin/env python3
"""
Baza Empire — Receipt OCR Analysis Skill
Extracts structured data from receipt images using Tesseract OCR and/or LLM vision.

Usage by agents:
    ##SKILL:receipt_ocr{"image_path": "/path/to/receipt.jpg", "mode": "full"}##

Modes:
    - "ocr_only"  — Tesseract OCR with regex parsing only
    - "llm_only"  — LLM vision analysis only (via LiteLLM proxy)
    - "full"      — Tesseract first, then LLM to enhance/fill gaps (default)
"""

import os
import sys
import json
import re
import base64
import io
import requests
from pathlib import Path

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "baza-litellm-internal")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# Local-only by Serge's rule (feedback_no_outside_apis): nothing in the OCR
# path may depend on a cloud service. Primary is qwen3-vl (best local
# accuracy); llava is a backup for when qwen3-vl errors out. Cloud models
# (gemma3:27b-cloud, gpt-4o) are still reachable for one-off opt-in by
# setting OLLAMA_VISION_MODEL or RECEIPT_OCR_ALLOW_CLOUD=1.
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen3-vl:latest")
OLLAMA_VISION_FALLBACK = os.environ.get("OLLAMA_VISION_FALLBACK", "llava:13b")
ALLOW_CLOUD = os.environ.get("RECEIPT_OCR_ALLOW_CLOUD", "0") in ("1", "true", "yes")

EMPTY_STRUCTURED = {
    "store_name": "",
    "store_location": "",
    "teller_name": "",
    "purchase_date": "",
    "purchase_time": "",
    "items": [],
    "subtotal": 0,
    "tax_amount": 0,
    "total": 0,
    "payment_method": "",
    "payment_details": "",
    "category": "",
}


# ── Tesseract OCR ──────────────────────────────────────────────────────────────

def run_tesseract(image_path: str) -> str:
    """Extract raw text from an image using pytesseract."""
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance

    img = Image.open(image_path)

    # Pre-process for better OCR: grayscale, contrast boost, sharpen
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)

    raw = pytesseract.image_to_string(img, config="--psm 6")
    return raw.strip()


def _fresh_structured() -> dict:
    """Deep-clone EMPTY_STRUCTURED so mutable defaults (items list) don't leak
    between parses. `dict(EMPTY_STRUCTURED)` was a shallow copy — items was
    shared across every call, so successive receipts accumulated each other's
    items and got mis-categorized."""
    import copy
    return copy.deepcopy(EMPTY_STRUCTURED)


def _looks_like_garbage(s: str) -> bool:
    """Heuristic — True if a string is OCR noise, not a real label.
    Bad photos (rotated, blurry, dim) make Tesseract emit lines like
    `>a , WFA wm a \\ SN fact?` — single chars and punctuation with no
    real vendor-shaped word. Refuse to use those as store_name so the
    review UI doesn't display garbage as the prefilled value."""
    if not s or len(s) < 3:
        return True
    tokens = re.findall(r"\S+", s)
    if not tokens:
        return True
    # Lots of 1-char tokens = junk (e.g. ">a , WFA wm a \ SN")
    short_tokens = sum(1 for t in tokens if len(t) <= 2)
    if len(tokens) >= 3 and short_tokens / len(tokens) >= 0.55:
        return True
    # At least one ≥4-char run of letters with a vowel — real words have these.
    long_words = re.findall(r"[A-Za-z]{4,}", s)
    has_vowel_word = any(re.search(r"[AEIOUaeiou]", w) for w in long_words)
    # Vendor names like "AT&T" survive (uppercase letters around & or ').
    looks_like_brand = bool(re.search(r"[A-Z]{2,}(?:\s*[&'\-]\s*[A-Z]+)+", s)) \
        or bool(re.search(r"[A-Z]{3,}", s))
    if not (has_vowel_word or looks_like_brand):
        return True
    # Mostly punctuation/whitespace
    alnum = sum(1 for c in s if c.isalnum())
    if alnum / len(s) < 0.5:
        return True
    return False


def parse_ocr_text(raw: str) -> dict:
    """Parse common receipt patterns from raw OCR text."""
    result = _fresh_structured()
    lines = raw.splitlines()

    # Store name — first non-empty line that doesn't look like OCR garbage.
    # Previous version blindly grabbed line 1, so a junk row like
    # `>a , WFA wm a \ SN fact?` ended up displayed as the store name in the
    # review UI. If no line passes the sanity check, leave store_name empty
    # (the LLM step or user edit can fill it in).
    for line in lines:
        cleaned = line.strip()
        if cleaned and not _looks_like_garbage(cleaned):
            result["store_name"] = cleaned
            break

    # Store address — score each candidate line and pick the strongest.
    # Previous version grabbed "DUNKIN #340123" because bare 5-digit runs look
    # like zip codes. Tighten: require either (a) street suffix adjacent to
    # a number, or (b) a proper city/state/ZIP pattern.
    street_rx = re.compile(
        r'\b\d+\s+[\w\s]{2,40}\b(?:st|street|ave|avenue|blvd|boulevard|'
        r'rd|road|dr|drive|ln|lane|way|ct|court|pkwy|parkway|hwy|highway|'
        r'pl|place|ter|terrace|cir|circle|tpke|turnpike)\b',
        re.I,
    )
    citystate_rx = re.compile(
        r'\b[A-Z][A-Za-z\- ]{1,30},?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b'
    )
    store_num_rx = re.compile(r'#\s*\d+')  # "DUNKIN #340123"

    addr_candidates = []  # (score, line)
    for i, line in enumerate(lines[:15]):  # address is usually in first 15 lines
        stripped = line.strip()
        if not stripped or len(stripped) < 6:
            continue
        score = 0
        if citystate_rx.search(stripped):
            score += 5
        if street_rx.search(stripped):
            score += 4
        if re.search(r'\b\d{5}(?:-\d{4})?\b', stripped) and not store_num_rx.search(stripped):
            score += 2
        # Penalize lines that look like a store-name + store-number
        if store_num_rx.search(stripped) and not street_rx.search(stripped):
            score -= 3
        if score >= 3:
            addr_candidates.append((score, stripped))

    if addr_candidates:
        addr_candidates.sort(key=lambda x: x[0], reverse=True)
        result["store_location"] = addr_candidates[0][1]

    # Date patterns
    for line in lines:
        m = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', line)
        if m:
            result["purchase_date"] = m.group(1)
            break
    if not result["purchase_date"]:
        for line in lines:
            m = re.search(r'(\d{4}[/-]\d{2}[/-]\d{2})', line)
            if m:
                result["purchase_date"] = m.group(1)
                break

    # Time pattern
    for line in lines:
        m = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)', line)
        if m:
            result["purchase_time"] = m.group(1).strip()
            break

    # Subtotal (parse first so total-scoring can use it)
    for line in lines:
        if re.search(r'sub\s*-?\s*total', line, re.I):
            m = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
            if m:
                result["subtotal"] = float(m.group(1).replace(",", ""))
                break

    # Tax — sum all tax lines (handles "TAX1", "STATE TAX", "LOCAL TAX")
    tax_sum = 0.0
    for line in lines:
        if re.search(r'\btax\b', line, re.I) and not re.search(r'tax\s*id|tax\s*exempt', line, re.I):
            m = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
            if m:
                tax_sum += float(m.group(1).replace(",", ""))
    if tax_sum > 0:
        result["tax_amount"] = round(tax_sum, 2)

    # Total — score every money-line and pick the best candidate.
    # Positive keywords boost, negative keywords subtract. Bonus for bottom
    # of receipt (receipts put the grand total near the end) and for values
    # that are math-consistent with subtotal + tax.
    money_rx = re.compile(r'\$?\s*([\d,]+\.\d{2})')
    candidates = []  # (score, amount, line)
    n_lines = len(lines)
    for i, raw_line in enumerate(lines):
        line_strip = raw_line.strip()
        if not line_strip:
            continue
        m = money_rx.search(line_strip)
        if not m:
            continue
        try:
            amt = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        if amt <= 0:
            continue
        lower = line_strip.lower()
        score = 0

        # Negative signals — strongly exclude subtotals + pre-tax/tax-only lines
        if re.search(r'\bsub\s*-?\s*total\b|\bsub\b', lower):
            score -= 8
        if re.search(r'\bpre[- ]?tax\b', lower):
            score -= 8
        if re.search(r'^\s*tax\b', lower):
            score -= 4
        if re.search(r'\bchange\b|\bcash\s*back\b', lower):
            score -= 6
        if re.search(r'\btip\b|\bgratuity\b', lower):
            score -= 3

        # Positive signals
        if re.search(r'\bgrand\s*total\b|\bamount\s*due\b|\bbalance\s*due\b', lower):
            score += 7
        elif re.search(r'\btotal\b', lower) and score > -4:
            score += 5
        if re.search(r'\btotal\s*charged?\b|\bcharge\s*total\b|\bnew\s*balance\b', lower):
            score += 5
        if re.search(r'\bvisa\b|\bmaster\s*card\b|\bmastercard\b|\bdebit\b|\bcredit\b|\bamex\b', lower):
            # Card-tender line often carries the final charge
            score += 2

        # Math consistency bonus: if value >= subtotal + tax (within $0.05) it's
        # likely the final total.
        if result["subtotal"] > 0 and result["tax_amount"] >= 0:
            expected = result["subtotal"] + result["tax_amount"]
            if abs(amt - expected) < 0.05:
                score += 4
            elif amt < result["subtotal"] - 0.01:
                # Can't be a total if smaller than subtotal
                score -= 5

        # Position bonus: lower third of receipt
        if n_lines and i >= int(n_lines * 0.66):
            score += 1

        if score > -2:  # keep weakly-positive candidates
            candidates.append((score, amt, line_strip))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        result["total"] = candidates[0][1]

    # Payment method
    for line in lines:
        lower = line.lower()
        if "visa" in lower or "mastercard" in lower or "credit" in lower:
            result["payment_method"] = "Credit Card"
            m = re.search(r'(\d{4})\s*$', line.strip())
            if m:
                result["payment_details"] = m.group(1)
            break
        elif "debit" in lower:
            result["payment_method"] = "Debit Card"
            m = re.search(r'(\d{4})\s*$', line.strip())
            if m:
                result["payment_details"] = m.group(1)
            break
        elif "cash" in lower:
            result["payment_method"] = "Cash"
            break
        elif "check" in lower or "cheque" in lower:
            result["payment_method"] = "Check"
            break

    # Teller / cashier — scan both same-line labels and adjacent lines.
    teller_keywords = re.compile(
        r'(?:cashier|cshr|teller|server|clerk|associate|sales\s*person|salesperson|'
        r'by|op\b|operator|emp(?:loyee)?|checkout|rep)',
        re.I,
    )
    teller_inline = re.compile(
        r'(?:cashier|cshr|teller|server|clerk|associate|sales\s*person|salesperson|'
        r'by|op|operator|emp(?:loyee)?|checkout|rep)'
        r'\s*[:#.]?\s*(?:id\s*)?(?:\d+\s*[-:/])?\s*([A-Za-z][A-Za-z \'\-.]{1,40})',
        re.I,
    )
    cap_name_rx = re.compile(r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z\.]+){0,2})\b')

    def _clean_teller(raw: str) -> str:
        s = raw.strip(" :#-.")
        s = re.sub(r"^\d+\s+", "", s)  # strip leading numeric IDs
        s = re.sub(r"\s+\d+\s*$", "", s)  # strip trailing numeric IDs
        s = s.strip()
        # Reject false positives (all caps non-name words)
        if s.lower() in {"your", "the", "today", "thanks", "thank you",
                         "receipt", "customer", "welcome", "sale"}:
            return ""
        return s

    for i, line in enumerate(lines):
        m = teller_inline.search(line)
        if m:
            cleaned = _clean_teller(m.group(1))
            if cleaned and len(cleaned) >= 2:
                result["teller_name"] = cleaned
                break
        # Adjacent-line scan: keyword line followed by a name line
        if teller_keywords.search(line) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            nm = cap_name_rx.search(nxt)
            if nm:
                cleaned = _clean_teller(nm.group(1))
                if cleaned and len(cleaned) >= 2:
                    result["teller_name"] = cleaned
                    break

    # Items — lines with a price pattern that aren't total/tax/subtotal
    skip_keywords = {"total", "subtotal", "sub total", "tax", "change", "cash", "credit", "debit", "visa", "mastercard"}
    for line in lines:
        lower = line.lower().strip()
        if any(kw in lower for kw in skip_keywords):
            continue
        m = re.search(r'^(.+?)\s+\$?\s*([\d,]+\.\d{2})\s*$', line.strip())
        if m:
            name = m.group(1).strip()
            price = float(m.group(2).replace(",", ""))
            if name and price > 0:
                # Check for quantity prefix like "2 x" or "2@"
                qty_match = re.match(r'^(\d+)\s*[x@]\s*(.+)', name, re.I)
                if qty_match:
                    result["items"].append({
                        "name": qty_match.group(2).strip(),
                        "quantity": int(qty_match.group(1)),
                        "price": price,
                    })
                else:
                    result["items"].append({
                        "name": name,
                        "quantity": 1,
                        "price": price,
                    })

    return result


# ── LLM Vision Analysis ────────────────────────────────────────────────────────

def _top_vendors_hint(limit: int = 10) -> str:
    """Pull the most-filed vendors from ahb_receipts for prompt grounding.
    Falls back to a seed list if the DB is unreachable."""
    try:
        import sqlite3
        from pathlib import Path as _P
        db = _P(__file__).resolve().parent.parent.parent / "dashboard" / "baza_projects.db"
        if not db.exists():
            raise RuntimeError("db missing")
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            """SELECT COALESCE(NULLIF(store_name,''), vendor) AS name, COUNT(*) n
                 FROM ahb_receipts
                WHERE COALESCE(NULLIF(store_name,''), vendor) IS NOT NULL
                  AND COALESCE(NULLIF(store_name,''), vendor) != ''
                GROUP BY LOWER(COALESCE(NULLIF(store_name,''), vendor))
                ORDER BY n DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        names = [r[0] for r in rows if r[0]]
        if names:
            return ", ".join(names)
    except Exception:
        pass
    return "Home Depot, Lowe's, Sherwin-Williams, Harbor Freight, Wawa, Exxon, Redner's"


def _parse_vision_json(content: str) -> dict | None:
    """Strip code fences + try to parse a JSON object out of a vision model's
    response. Returns None if there's no plausible JSON in there.

    Local vision models often pad JSON with prose ("Here's the data: { ... }"),
    so we also try to extract the first {...} block as a last resort."""
    if not content:
        return None
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'```\s*$', '', content)
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Heuristic: pull out the first balanced JSON object.
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end > start:
        snippet = content[start:end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


def _coerce_money(v) -> float:
    """qwen3-vl occasionally returns money fields as '$56.19' or '1,234.50'
    despite the prompt asking for plain numbers. Normalize before merge."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('$', '').replace(',', '').replace(' ', '')
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _normalize_vision_numbers(parsed: dict) -> dict:
    """In-place coercion of numeric fields to floats. Also self-corrects when
    subtotal+tax ≠ total within a sensible tolerance:
      - If the math doesn't match and subtotal+tax looks like a plausible
        receipt total ($1+), trust subtotal+tax (model often reads total
        from an adjacent line).
      - If subtotal == total and tax > 0, model collapsed two fields —
        subtract tax from subtotal."""
    if not isinstance(parsed, dict):
        return parsed
    for k in ('total', 'subtotal', 'tax_amount'):
        if k in parsed:
            parsed[k] = _coerce_money(parsed[k])
    items = parsed.get('items')
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and 'price' in it:
                it['price'] = _coerce_money(it['price'])

    total = float(parsed.get('total') or 0)
    sub = float(parsed.get('subtotal') or 0)
    tax = float(parsed.get('tax_amount') or 0)
    if sub > 0 and tax >= 0 and total > 0:
        derived = round(sub + tax, 2)
        if abs(derived - total) > 0.02:
            # Common case: model read subtotal twice (subtotal == total, tax separate).
            if abs(sub - total) <= 0.02 and tax > 0:
                parsed['subtotal'] = round(total - tax, 2)
            # Otherwise: subtotal + tax is the more reliable signal (the model
            # often picks up an unrelated line as "total"). Trust the math.
            else:
                parsed['total'] = derived
    return parsed


def _vision_result_is_useful(parsed: dict | None) -> bool:
    """True if the parsed JSON contains *anything* worth keeping (a date, a
    non-zero total, or a non-empty store name). Filters out 'I cannot see
    images' style hallucinations from text-only fallbacks."""
    if not parsed or not isinstance(parsed, dict):
        return False
    if (parsed.get("store_name") or "").strip():
        return True
    if (parsed.get("purchase_date") or "").strip():
        return True
    try:
        if float(parsed.get("total") or 0) != 0:
            return True
        if float(parsed.get("subtotal") or 0) != 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _preprocess_for_vision(image_path: str) -> tuple[bytes, str]:
    """Upscale small/blurry receipts and boost contrast before sending to a
    vision model. Returns (jpeg_bytes, mime_type).

    Many queue images arrive at ~480x640 (Telegram-compressed) where small text
    like dates and totals is only a handful of pixels per glyph — the vision
    model then misreads digits ('3' vs '0', '8' vs '3') or picks up unrelated
    numbers like the return-policy expiration date instead of the purchase
    date. Upscaling with Lanczos + unsharp mask + a mild contrast bump gives
    the model more patches per character.

    Falls back to the raw file bytes on any error — preprocessing must never
    block analysis."""
    try:
        from PIL import Image, ImageOps, ImageEnhance, ImageFilter
        im = Image.open(image_path)
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        im = im.convert('RGB')
        w, h = im.size
        target_long = 1800
        long_edge = max(w, h)
        if long_edge < target_long:
            scale = target_long / long_edge
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            im = im.filter(ImageFilter.UnsharpMask(radius=1.5, percent=130, threshold=2))
            im = ImageEnhance.Contrast(im).enhance(1.15)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=95, subsampling=0)
        return buf.getvalue(), 'image/jpeg'
    except Exception:
        img_path = Path(image_path)
        suffix = img_path.suffix.lower().lstrip(".")
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "webp": "webp", "bmp": "bmp"}
        return img_path.read_bytes(), f"image/{mime_map.get(suffix, 'jpeg')}"


def run_llm_analysis(image_path: str) -> dict:
    """Extract structured fields from a receipt image.

    Order of preference:
      1. Local Ollama vision (qwen3-vl by default — fast, free, reliable).
      2. Llava fallback if the primary local vision model errors out.
      3. LiteLLM cloud proxy (gpt-4o) only as a last resort, because the
         current fallback chain in litellm.yaml can land on a text-only
         model that silently returns garbage like "I cannot see images".

    Raises if every path returns useless output, so the caller surfaces the
    error in `warnings` instead of writing zeros into the receipt."""
    img_bytes, mime_type = _preprocess_for_vision(image_path)
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    vendor_hint = _top_vendors_hint()

    prompt = (
        "Extract structured data from this receipt image as JSON.\n"
        f"Likely vendors (we see these often): {vendor_hint}. "
        "If the store matches one of these, use its canonical name; "
        "OCR typos like 'The homedepot' → 'Home Depot' are expected.\n\n"
        "Fields to extract:\n"
        "- store_name: vendor name, canonicalized\n"
        "- store_location: full street address + city/state if printed\n"
        "- teller_name: cashier/server name; often labeled Cashier/CSHR/Server/Op/By; "
        "  strip leading IDs; leave empty if not clearly a person's name\n"
        "- purchase_date: YYYY-MM-DD. The date the customer PAID — usually printed\n"
        "  near the top, next to the time/register number, OR adjacent to the\n"
        "  total/payment line at the bottom. NEVER use a 'RETURN POLICY EXPIRES',\n"
        "  'WARRANTY UNTIL', 'GOOD THROUGH', 'VALID UNTIL', or any future-dated\n"
        "  expiration as the purchase_date. If you only see an expiration date,\n"
        "  return empty. Prefer dates printed with the time-of-day (08:40 AM\n"
        "  next to 03/27/26 is the purchase). Read each digit carefully: 0/3/8\n"
        "  and 1/7 look alike on faded thermal paper.\n"
        "- purchase_time: HH:MM (24h) if printed\n"
        "- items: [{name, quantity, price}]\n"
        "- subtotal: pre-tax subtotal as number\n"
        "- tax_amount: total tax as number (sum state+local if multiple tax lines)\n"
        "- total: GRAND TOTAL / Amount Due / Balance Due — the final charge, "
        "  NEVER the subtotal. If subtotal+tax is printed, the total equals that.\n"
        "- payment_method: Cash/Credit Card/Debit Card/Check\n"
        "- payment_details: last 4 digits if card\n"
        "- category: one of Materials, Tools, Fuel, Food, Office supplies, Clothes. "
        "  Rules: Home Depot / Lowe's / Sherwin-Williams / Ace / 84 Lumber / "
        "  hardware stores = Materials. Gallons/unleaded/premium/diesel = Fuel "
        "  (even at Wawa/Sheetz/7-Eleven). Grocery or coffee/food chains = Food. "
        "  Specialty tool stores (Harbor Freight / Snap-On / Matco) = Tools.\n\n"
        "Return ONLY valid JSON. Use empty string for unknown text fields and 0 for unknown numbers."
    )

    last_err: Exception | None = None

    # 1) Primary local vision model.
    try:
        content = _ollama_vision_analyze(prompt, img_b64, mime_type, model=OLLAMA_VISION_MODEL)
        parsed = _parse_vision_json(content)
        if _vision_result_is_useful(parsed):
            return _normalize_vision_numbers(parsed)
    except Exception as e:
        last_err = e

    # 2) Local secondary fallback (older but reliable for cases qwen3-vl misses).
    if OLLAMA_VISION_FALLBACK and OLLAMA_VISION_FALLBACK != OLLAMA_VISION_MODEL:
        try:
            content = _ollama_vision_analyze(prompt, img_b64, mime_type, model=OLLAMA_VISION_FALLBACK)
            parsed = _parse_vision_json(content)
            if _vision_result_is_useful(parsed):
                return _normalize_vision_numbers(parsed)
        except Exception as e:
            last_err = e

    # 3) Cloud as opt-in last resort. Off by default — Serge's rule is
    # local-only for the empire's own tools. Set RECEIPT_OCR_ALLOW_CLOUD=1
    # to re-enable the LiteLLM gpt-4o fallback.
    if not ALLOW_CLOUD:
        if last_err:
            raise last_err
        raise RuntimeError("local vision returned no usable data (cloud disabled)")
    try:
        payload = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_b64}"}},
                    ],
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.1,
        }
        resp = requests.post(
            f"{LITELLM_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = _parse_vision_json(content)
        if _vision_result_is_useful(parsed):
            return _normalize_vision_numbers(parsed)
    except Exception as e:
        last_err = e

    if last_err:
        raise last_err
    raise RuntimeError("vision analysis returned no usable data")


def _ollama_vision_analyze(prompt: str, img_b64: str, mime_type: str, model: str | None = None) -> str:
    """Local vision call to Ollama. Returns the model's raw text response,
    or '' on failure. `model` defaults to OLLAMA_VISION_MODEL."""
    mdl = model or OLLAMA_VISION_MODEL
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": mdl,
                "prompt": prompt + "\n\nReturn ONLY a JSON object — no commentary.",
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 1500},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return (resp.json() or {}).get("response", "") or ""
    except Exception:
        return ""


def merge_results(ocr_data: dict, llm_data: dict, raw_text: str = "") -> dict:
    """Merge OCR and LLM results, preferring LLM values when OCR is empty/zero."""
    merged = _fresh_structured()

    for key in EMPTY_STRUCTURED:
        ocr_val = ocr_data.get(key, EMPTY_STRUCTURED[key])
        llm_val = llm_data.get(key, EMPTY_STRUCTURED[key])

        if key == "items":
            ocr_items = ocr_val if isinstance(ocr_val, list) else []
            llm_items = llm_val if isinstance(llm_val, list) else []
            merged[key] = llm_items if len(llm_items) >= len(ocr_items) else ocr_items
        elif isinstance(EMPTY_STRUCTURED[key], (int, float)):
            if llm_val and llm_val != 0:
                merged[key] = llm_val
            else:
                merged[key] = ocr_val
        else:
            if llm_val:
                merged[key] = llm_val
            else:
                merged[key] = ocr_val

    _normalize_vendor_and_category(merged, raw_text)
    return merged


# ── Vendor + category post-processing ─────────────────────────────────────────

def _normalize_vendor_and_category(merged: dict, raw_text: str = "") -> None:
    """In-place: normalize store_name via vendor_kb, apply fuel-detection override."""
    try:
        from vendor_kb import match_vendor, suggest_category_from_items
    except Exception:
        return

    # Normalize vendor (store_name) using the knowledge base
    raw_name = merged.get("store_name") or ""
    if raw_name:
        canon, cat_hint, conf = match_vendor(raw_name)
        if canon and conf >= 0.85:
            merged["store_name"] = canon
            if not merged.get("category") and cat_hint:
                merged["category"] = cat_hint

    # Fuel detection — if OCR text shows "X.XX gal" / "gallons" / pump keywords,
    # force category=Fuel regardless of what LLM said. Wawa/Sheetz/7-Eleven
    # are mixed food+fuel; the pump signal disambiguates the purchase type.
    fuel_signals = re.compile(
        r'(\b\d+\.\d{2,3}\s*(?:gal|gallon|gallons|g\b))|'
        r'(\b(?:regular|unleaded|premium|diesel|pump|fuel|gas)\b[^\n]{0,30}\$?\s*[\d,]+\.\d{2})|'
        r'(\bprice\s*/\s*gal\b)|(\bgal\s*price\b)',
        re.I,
    )
    hay = "\n".join(filter(None, [raw_text, raw_name, merged.get("store_location", "")]))
    if fuel_signals.search(hay):
        merged["category"] = "Fuel"

    # Item-based tiebreaker (food keywords in items → Food) if still empty
    if not merged.get("category"):
        item_cat = suggest_category_from_items(merged.get("items") or [])
        if item_cat:
            merged["category"] = item_cat


# ── Main Skill Entry ────────────────────────────────────────────────────────────

def analyze_receipt(image_path: str, mode: str = "full") -> dict:
    """
    Analyze a receipt image and return structured data.

    Args:
        image_path: Path to the receipt image file
        mode: "ocr_only", "llm_only", or "full" (both)

    Returns:
        dict with success, ocr_raw, and structured fields
    """
    if not Path(image_path).is_file():
        return {"success": False, "error": f"Image not found: {image_path}"}

    ocr_raw = ""
    ocr_data = _fresh_structured()
    llm_data = _fresh_structured()
    errors = []

    # Step 1: Tesseract OCR
    if mode in ("ocr_only", "full"):
        try:
            ocr_raw = run_tesseract(image_path)
            ocr_data = parse_ocr_text(ocr_raw)
        except Exception as e:
            errors.append(f"Tesseract error: {e}")
            # If full mode, we'll still try LLM
            if mode == "ocr_only":
                # Try LLM as emergency fallback even in ocr_only mode
                try:
                    llm_data = run_llm_analysis(image_path)
                    return {
                        "success": True,
                        "ocr_raw": "",
                        "structured": llm_data,
                        "warnings": errors + ["Fell back to LLM due to Tesseract failure"],
                    }
                except Exception as llm_err:
                    return {"success": False, "error": f"Both methods failed: {e}; LLM: {llm_err}"}

    # Step 2: LLM Vision
    if mode in ("llm_only", "full"):
        try:
            llm_data = run_llm_analysis(image_path)
        except Exception as e:
            errors.append(f"LLM error: {e}")
            if mode == "llm_only":
                return {"success": False, "error": f"LLM analysis failed: {e}"}

    # Step 3: Combine results
    if mode == "full":
        structured = merge_results(ocr_data, llm_data, raw_text=ocr_raw)
    elif mode == "llm_only":
        structured = llm_data
        _normalize_vendor_and_category(structured, raw_text="")
    else:
        structured = ocr_data
        _normalize_vendor_and_category(structured, raw_text=ocr_raw)

    result = {
        "success": True,
        "ocr_raw": ocr_raw,
        "structured": structured,
    }
    if errors:
        result["warnings"] = errors

    return result


# ── Skill execution (called by SkillsEngine via SKILL_ARGS env var) ────────────
if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

    image_path = args.get("image_path", "")
    mode = args.get("mode", "full")

    if not image_path:
        print(json.dumps({"success": False, "error": "image_path is required"}))
        sys.exit(1)

    if mode not in ("ocr_only", "llm_only", "full"):
        mode = "full"

    result = analyze_receipt(image_path, mode)
    print(json.dumps(result, indent=2))
