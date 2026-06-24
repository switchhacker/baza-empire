#!/usr/bin/env python3
"""brand_kit — establish/refresh the AHBCO brand source of truth (brand.json).

Modes:
  show   -> return current brand
  set    -> deep-merge a patch into brand.json (bumps version)
  detect -> scrape the site for logo + dominant colors; fallback to defaults
Usage: ##SKILL:brand_kit{"mode":"detect"}##
"""
import os, sys, json, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
import requests   # noqa: E402

# allow tests / ops to redirect the brand file
_override = os.environ.get("BAZA_BRAND_PATH")
if _override:
    media_kit.BRAND_PATH = Path(_override)


def _detect(site):
    """Best-effort scrape: og:image/logo img -> assets/logo.png; colors via Sam tool."""
    brand = media_kit.load_brand()
    brand["site"] = site
    try:
        html = requests.get(site, timeout=8).text
    except Exception:
        return None  # signal fallback
    # find a logo candidate
    logo_url = ""
    # match og:image meta tag in EITHER attribute order (Squarespace emits content-first)
    for m in re.finditer(r'<meta[^>]+>', html, re.I):
        tag = m.group(0)
        if re.search(r'(property|name)=["\']og:image["\']', tag, re.I):
            cm = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
            if cm:
                logo_url = cm.group(1)
                break
    if not logo_url:
        m = re.search(r'<img[^>]+(?:logo|brand)[^>]*src=["\']([^"\']+)', html, re.I)
        if m:
            logo_url = m.group(1)
    if logo_url:
        if logo_url.startswith("//"):
            logo_url = "https:" + logo_url
        elif logo_url.startswith("/"):
            logo_url = site.rstrip("/") + logo_url
        try:
            import io
            from PIL import Image as _PILImage
            raw = requests.get(logo_url, timeout=10).content
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError("logo too large")
            img = _PILImage.open(io.BytesIO(raw)).convert("RGBA")  # raises if not a decodable raster
            media_kit.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            dest = media_kit.ASSETS_DIR / "logo.png"
            img.save(dest, "PNG")
            brand["logo"] = str(dest)
            # extract colors from the logo via Sam's color-palette tool
            try:
                r = requests.post(f"{media_kit.TOOL_SERVER}/tools/sam/color-palette",
                                  json={"input": {"image_path": str(dest), "colors": 5}},
                                  timeout=20)
                pal = [c["hex"] for c in r.json().get("output", {}).get("palette", [])]
                if len(pal) >= 3:
                    brand["colors"]["primary"] = pal[0]
                    brand["colors"]["secondary"] = pal[1]
                    brand["colors"]["accent"] = pal[2]
            except Exception:
                pass
        except Exception:
            pass
    return brand


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    mode = args.get("mode", "show")

    if mode == "show":
        print(json.dumps({"brand": media_kit.load_brand(), "skill": "brand_kit"}))
        return

    if mode == "set":
        brand = media_kit.load_brand()
        brand = media_kit._deep_merge(brand, args.get("patch", {}))
        brand["version"] = int(brand.get("version", 1)) + 1
        media_kit.save_brand(brand)
        print(json.dumps({"brand": brand, "skill": "brand_kit"}))
        return

    if mode == "detect":
        site = args.get("site", media_kit.load_brand()["site"])
        detected = _detect(site)
        if detected is None:
            brand = media_kit.load_brand()
            media_kit.save_brand(brand)
            print(json.dumps({"brand": brand, "source": "fallback",
                              "skill": "brand_kit"}))
            return
        detected["version"] = int(detected.get("version", 1)) + 1
        media_kit.save_brand(detected)
        print(json.dumps({"brand": detected, "source": "detected",
                          "skill": "brand_kit"}))
        return

    print(json.dumps({"error": f"unknown mode {mode}"}))


if __name__ == "__main__":
    main()
