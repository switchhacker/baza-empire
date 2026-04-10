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
import requests
from pathlib import Path

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "baza-litellm")

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


def parse_ocr_text(raw: str) -> dict:
    """Parse common receipt patterns from raw OCR text."""
    result = dict(EMPTY_STRUCTURED)
    lines = raw.splitlines()

    # Store name — typically the first non-empty line
    for line in lines:
        cleaned = line.strip()
        if cleaned and len(cleaned) > 2:
            result["store_name"] = cleaned
            break

    # Store address — look for lines with common address patterns
    for line in lines:
        stripped = line.strip()
        if re.search(r'\d{5}', stripped) or re.search(r'\d+\s+\w+\s+(st|ave|blvd|rd|dr|ln|way|ct|pkwy|hwy)', stripped, re.I):
            result["store_location"] = stripped
            break

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

    # Total — look for "TOTAL" line (not subtotal, not tax)
    for line in lines:
        if re.search(r'\btotal\b', line, re.I) and not re.search(r'sub\s*total|tax', line, re.I):
            m = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
            if m:
                result["total"] = float(m.group(1).replace(",", ""))
                break

    # Subtotal
    for line in lines:
        if re.search(r'sub\s*total', line, re.I):
            m = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
            if m:
                result["subtotal"] = float(m.group(1).replace(",", ""))
                break

    # Tax
    for line in lines:
        if re.search(r'\btax\b', line, re.I):
            m = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
            if m:
                result["tax_amount"] = float(m.group(1).replace(",", ""))
                break

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

    # Teller / cashier
    for line in lines:
        m = re.search(r'(?:cashier|teller|server|clerk|associate|emp)[:\s#]*(.+)', line, re.I)
        if m:
            result["teller_name"] = m.group(1).strip()
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

def run_llm_analysis(image_path: str) -> dict:
    """Send receipt image to a vision-capable LLM via LiteLLM proxy."""
    img_path = Path(image_path)
    suffix = img_path.suffix.lower().lstrip(".")
    mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp", "bmp": "bmp"}
    mime_type = f"image/{mime_map.get(suffix, 'jpeg')}"

    img_b64 = base64.b64encode(img_path.read_bytes()).decode("utf-8")

    prompt = (
        "Analyze this receipt image. Extract ALL data as JSON with these fields: "
        "store_name, store_location (full address), teller_name (cashier/server), "
        "purchase_date (YYYY-MM-DD), purchase_time (HH:MM), "
        "items (array of {name, quantity, price}), subtotal, tax_amount, total, "
        "payment_method (Cash/Credit Card/Debit Card/Check), "
        "payment_details (last 4 digits if card), "
        "category (Materials/Tools/Fuel/Food/Office supplies/Clothes). "
        "Return ONLY valid JSON."
    )

    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{img_b64}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }

    resp = requests.post(
        f"{LITELLM_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {LITELLM_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown code fences if present
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'```\s*$', '', content)
        content = content.strip()

    parsed = json.loads(content)
    return parsed


def merge_results(ocr_data: dict, llm_data: dict) -> dict:
    """Merge OCR and LLM results, preferring LLM values when OCR is empty/zero."""
    merged = dict(EMPTY_STRUCTURED)

    for key in EMPTY_STRUCTURED:
        ocr_val = ocr_data.get(key, EMPTY_STRUCTURED[key])
        llm_val = llm_data.get(key, EMPTY_STRUCTURED[key])

        if key == "items":
            # Prefer LLM items if it found more, or if OCR found none
            ocr_items = ocr_val if isinstance(ocr_val, list) else []
            llm_items = llm_val if isinstance(llm_val, list) else []
            merged[key] = llm_items if len(llm_items) >= len(ocr_items) else ocr_items
        elif isinstance(EMPTY_STRUCTURED[key], (int, float)):
            # Prefer non-zero value; if both non-zero prefer LLM
            if llm_val and llm_val != 0:
                merged[key] = llm_val
            else:
                merged[key] = ocr_val
        else:
            # Prefer non-empty string; if both non-empty prefer LLM
            if llm_val:
                merged[key] = llm_val
            else:
                merged[key] = ocr_val

    return merged


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
    ocr_data = dict(EMPTY_STRUCTURED)
    llm_data = dict(EMPTY_STRUCTURED)
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
        structured = merge_results(ocr_data, llm_data)
    elif mode == "llm_only":
        structured = llm_data
    else:
        structured = ocr_data

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
