#!/usr/bin/env python3
"""before_after_showcase — branded BEFORE/AFTER comparison graphic from REAL photos.

Photo-first and required: never fabricates the work with AI. Saves an artifact
per requested platform; optional Social Studio draft queue.
Usage: ##SKILL:before_after_showcase{"before":"/path/b.jpg","after":"/path/a.jpg","title":"Kitchen Remodel"}##
"""
import os, sys, json, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
from PIL import ImageDraw  # noqa: E402

if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])


def _label(img, text, box, brand):
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    pad = 16
    font = media_kit._font(brand["fonts"]["headline"], 40)
    tw = draw.textlength(text, font=font)
    draw.rectangle([x0, y0, x0 + tw + pad * 2, y0 + 60],
                   fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
    draw.text((x0 + pad, y0 + 8), text, font=font,
              fill=media_kit.hex_to_rgb(brand["colors"]["dark"]))


def _compose(platform, before, after, title, details, brand):
    w, h = media_kit.PLATFORMS[platform]
    canvas = media_kit.new_canvas(platform,
                                  bg=media_kit.hex_to_rgb(brand["colors"]["dark"]))
    half = w // 2
    img_h = int(h * 0.78)
    b_img = media_kit.load_photo(before, (half - 4, img_h))
    a_img = media_kit.load_photo(after, (half - 4, img_h))
    canvas.paste(b_img, (0, 0))
    canvas.paste(a_img, (half + 4, 0))
    ImageDraw.Draw(canvas).rectangle([half - 4, 0, half + 4, img_h],
                                     fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
    _label(canvas, "BEFORE", (24, 24, half, 84), brand)
    _label(canvas, "AFTER", (half + 28, 24, w, 84), brand)
    media_kit.draw_headline(canvas, title or brand["name"],
                            (int(w * 0.05), img_h + 16, int(w * 0.95), h - 20),
                            color=media_kit.hex_to_rgb(brand["colors"]["light"]),
                            font_path=brand["fonts"]["headline"], max_size=64)
    if details:
        d = ImageDraw.Draw(canvas)
        f = media_kit._font(brand["fonts"]["body"], 28)
        d.text((int(w * 0.05), h - 48), details[:90], font=f,
               fill=media_kit.hex_to_rgb(brand["colors"]["secondary"]))
    media_kit.place_logo(canvas, brand, corner="br", max_w=220)
    return canvas


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    before, after = args.get("before", ""), args.get("after", "")
    if not (before and after and Path(before).exists() and Path(after).exists()):
        print(json.dumps({"error": "before and after photo paths are required "
                                   "(photo-first: AI does not fabricate the work)"}))
        return
    title = args.get("title", "")
    details = args.get("details", "")
    project_id = args.get("project_id")
    platforms = args.get("platforms") or ["ig_square"]
    do_queue = bool(args.get("queue", False))

    brand = media_kit.load_brand()
    artifacts, queued, warnings = [], [], []
    for plat in platforms:
        if plat not in media_kit.PLATFORMS:
            warnings.append(f"unknown platform {plat}"); continue
        img = _compose(plat, before, after, title, details, brand)
        safe = re.sub(r"[^\w\-]+", "_", title or "project")[:30].strip("_") or "project"
        fname = f"showcase_{safe}_{plat}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(project_id or "shared"),
            description=f"Before/After showcase: {title}",
            tags=["showcase", "before-after", plat])
        if not saved.get("success"):
            warnings.append(f"save failed {plat}: {saved.get('error')}"); continue
        artifacts.append(saved)
        if do_queue:
            try:
                pid = media_kit.queue_social_post(
                    platform=plat, variant="showcase", asset_path=saved["path"],
                    caption=f"{title} — see the transformation. {brand['tagline']}",
                    hashtags=["#BeforeAndAfter", "#AHBCO", "#Remodel"],
                    project_id=project_id, ai_meta={"kind": "showcase"})
                queued.append({"platform": plat, "post_id": pid})
            except Exception as e:
                warnings.append(f"queue failed {plat}: {e}")

    print(json.dumps({"skill": "before_after_showcase", "artifacts": artifacts,
                      "queued": queued, "warnings": warnings}))


if __name__ == "__main__":
    main()
