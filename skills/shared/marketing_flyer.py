#!/usr/bin/env python3
"""marketing_flyer — branded flyer/ad from an offer/service brief.

Fills copy gaps via local Ollama; base = real photo if given, else SD background,
else brand-color panel. Renders at requested print/digital sizes; saves artifacts.
Usage: ##SKILL:marketing_flyer{"headline":"Spring Roofing Special","cta":"Call (555) 123-4567"}##
"""
import os, sys, json, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
from PIL import ImageDraw  # noqa: E402

if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])

DEFAULT_SIZES = ["flyer_portrait", "ad_square", "ad_landscape"]


def _base(size, photo, headline, brand):
    w, h = size
    if photo and Path(photo).exists():
        img = media_kit.load_photo(photo, (w, h))
    else:
        bg = media_kit.gen_background(
            f"home services background, {headline[:50]}, clean, professional", w, h)
        if bg and Path(bg).exists():
            img = media_kit.load_photo(bg, (w, h))
        else:
            img = media_kit.new_canvas_size(w, h,
                                            media_kit.hex_to_rgb(brand["colors"]["primary"]))
    media_kit.scrim(img, side="top", height_frac=0.55, max_alpha=200)
    return img


def _compose(size_name, headline, subhead, bullets, cta, photo, brand):
    w, h = media_kit.PLATFORMS[size_name]
    img = _base((w, h), photo, headline, brand)
    draw = ImageDraw.Draw(img)
    mx = int(w * 0.07)
    media_kit.draw_headline(img, headline, (mx, int(h * 0.06), w - mx, int(h * 0.3)),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"],
                            max_size=int(h * 0.085))
    y = int(h * 0.32)
    if subhead:
        sf = media_kit._font(brand["fonts"]["headline"], int(h * 0.04))
        draw.text((mx, y), subhead[:60], font=sf,
                  fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
        y += int(h * 0.07)
    bf = media_kit._font(brand["fonts"]["body"], int(h * 0.028))
    cta_top = int(h * 0.86)
    col_w = w - mx * 2
    for b in (bullets or [])[:6]:
        for line in media_kit._wrap(draw, f"✓  {b}", bf, col_w):
            if y >= cta_top - int(h * 0.05):
                break
            draw.text((mx, y), line, font=bf, fill=(255, 255, 255))
            y += int(h * 0.045)
    if cta:
        cf = media_kit._font(brand["fonts"]["headline"], int(h * 0.038))
        ctw = draw.textlength(cta, font=cf)
        bx0, by0 = mx, min(int(h * 0.88), h - int(h * 0.17))
        draw.rectangle([bx0, by0, bx0 + ctw + 60, by0 + int(h * 0.06)],
                       fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
        draw.text((bx0 + 30, by0 + int(h * 0.013)), cta, font=cf,
                  fill=media_kit.hex_to_rgb(brand["colors"]["dark"]))
    media_kit.place_logo(img, brand, corner="br", max_w=int(w * 0.22))
    return img


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    headline = args.get("headline", "")
    offer = args.get("offer", "")
    if not headline and not offer:
        print(json.dumps({"error": "headline or offer required"})); return

    brand = media_kit.load_brand()
    subhead = args.get("subhead", "")
    bullets = args.get("bullets", [])
    cta = args.get("cta", "")
    photo = args.get("photo", "")
    sizes = args.get("sizes") or DEFAULT_SIZES

    # fill copy gaps from offer if needed
    if offer and (not headline or not bullets):
        copy = media_kit.write_copy(offer, brand, kind="flyer")
        headline = headline or copy["caption"].split(".")[0][:60]
        if not bullets:
            bullets = [t.lstrip("#") for t in copy["hashtags"][:3]]
        cta = cta or copy.get("first_comment", "")

    artifacts, warnings = [], []
    for sz in sizes:
        if sz not in media_kit.PLATFORMS:
            warnings.append(f"unknown size {sz}"); continue
        try:
            img = _compose(sz, headline, subhead, bullets, cta, photo, brand)
        except Exception as e:
            warnings.append(f"compose failed {sz}: {e}"); continue
        safe = re.sub(r"[^\w\-]+", "_", headline or "flyer")[:30].strip("_") or "flyer"
        fname = f"flyer_{safe}_{sz}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(args.get("project_id") or "shared"),
            description=f"Marketing flyer: {headline}",
            tags=["flyer", "ad", sz])
        if not saved.get("success"):
            warnings.append(f"save failed {sz}: {saved.get('error')}"); continue
        artifacts.append(saved)

    print(json.dumps({"skill": "marketing_flyer", "headline": headline,
                      "artifacts": artifacts, "warnings": warnings}))


if __name__ == "__main__":
    main()
