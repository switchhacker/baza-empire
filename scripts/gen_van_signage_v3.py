#!/usr/bin/env python3
"""
AHBCO Vehicle Signage Mockup — v3

Fix from v2: drop the giant translucent paste-on card. Real vinyl wrap
goes directly on the body panel within the actual panel bounds — between
the window line and the rocker, between the wheel arches, on the door
panels — not as a sticker covering sky and ground.

This version:
  * NO white card behind the text
  * Text drawn directly on the white van body in navy + red
  * Subtle white halo outline for legibility on busy backgrounds (trees etc.)
  * Hand-picked panel rectangles per source photo so text sits inside the
    real panel bounds, not floating across the frame
  * NO caption strip, NO red border bars — just the lettering as it would
    appear on the wrap

Outputs (replaces v2 in dashboard/artifacts/proj-ahb123/):
  van_actual_side_left.png            — passenger-side profile
  van_actual_side_rearquarter.png     — rear-quarter view
  van_actual_rear.png                 — rear doors
"""
from __future__ import annotations

import datetime
import json
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = "/home/switchhacker/baza-empire/agent-framework-v3"
SOURCE_DIR = os.path.join(ROOT, "dashboard", "artifacts", "data-hub")
OUT_DIR = os.path.join(ROOT, "dashboard", "artifacts", "proj-ahb123")

NAVY = (13, 43, 94)
RED = (233, 69, 96)
WHITE = (255, 255, 255)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

BRAND = {
    "name": "ALL HOME BUILDING CO LLC",
    "url": "AHB123.COM",
    "phone": "800-484-6404",
    "city": "BENSALEM, PA 19020",
    "tag": "RENOVATION  •  CONSTRUCTION  •  ROOFING",
}


# ── Panel rectangles in NORMALIZED coords (x0, y0, x1, y1) ────────────────────
#
# Hand-picked from each source photo so text seats inside the actual van's
# panel bounds — i.e. below the side window line, above the rocker, between
# the wheel arches; or for the rear: on the door panel between the upper
# vent strip and the lower trim.
#
# Numbers come from looking at where the white body panel actually sits in
# each photo. Tweak here if a future photo crops differently.
PANEL: dict[str, tuple[float, float, float, float]] = {
    # v4 placement: text shifted FORWARD (toward the front of the van) and
    # UP closer to the high-roof extension line right beneath the side
    # windows. Wrap professionals call this the "primary brand band" — it
    # reads at eye-level for pedestrians and at headlight level for cars
    # behind. Numbers are tighter than v3 so glyphs stop hanging over
    # door seams, wheel arches, or rear corner lights.
    #
    # v5 placement: anchored at the FORWARD half of the cargo body (just
    # behind the driver door / B-pillar) and pushed up so the band starts
    # right at the high-roof extension crease, ending in the upper third
    # of the side panel — not the middle. Door areas, rear corner pillars,
    # and the lower rocker stay clean.
    #
    # van side 2.jpeg — passenger profile (front is to the LEFT in the photo)
    # Cargo body in this photo runs roughly x=0.40-0.95; we put text in
    # the front portion of that band.
    "side_passenger":   (0.34, 0.28, 0.74, 0.46),
    # van side.jpeg — rear-quarter from passenger side (front is to the LEFT)
    "side_rearquarter": (0.30, 0.30, 0.70, 0.48),
    # van rear.jpeg — recentered on the van. Doors span x=0.345-0.727
    # (center 0.536). Panel x=0.34-0.74 → mid 0.54, matched. Pushed up
    # so text sits in the upper door panel just under the roof spoiler.
    "rear":             (0.34, 0.20, 0.74, 0.56),
}


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def measure(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_font(path: str, text: str, max_w: int, max_h: int,
             start_size: int = 240, min_size: int = 12) -> ImageFont.FreeTypeFont:
    """Largest font that fits both width and height bounds."""
    size = start_size
    while size > min_size:
        f = load_font(path, size)
        w, h = measure(f, text)
        if w <= max_w and h <= max_h:
            return f
        size -= 2
    return load_font(path, min_size)


def upscale(img: Image.Image, target_w: int = 1400) -> Image.Image:
    w, h = img.size
    if w >= target_w:
        return img.convert("RGB")
    ratio = target_w / w
    return img.resize((target_w, int(h * ratio)), Image.LANCZOS).convert("RGB")


def draw_text_with_halo(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                         font: ImageFont.FreeTypeFont, fill: tuple,
                         halo: tuple = (255, 255, 255), halo_w: int = 2):
    """Subtle white halo for legibility against varied backgrounds. We keep
    halo_w small (1-3px) so it reads as crisp paint, not as a glow."""
    x, y = xy
    if halo_w > 0:
        for dx in range(-halo_w, halo_w + 1):
            for dy in range(-halo_w, halo_w + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=halo)
    draw.text(xy, text, font=font, fill=fill)


def draw_side(canvas: Image.Image, panel: tuple[float, float, float, float]) -> None:
    """Draw the side-panel layout: company name, URL+phone row, tag, city."""
    W, H = canvas.size
    x0, y0, x1, y1 = panel
    px0, py0 = int(W * x0), int(H * y0)
    px1, py1 = int(W * x1), int(H * y1)
    pw, ph = px1 - px0, py1 - py0
    draw = ImageDraw.Draw(canvas)

    # 4-line stack: name, url+phone, tag, city
    # Allocate vertical space proportionally
    # 38% name, 26% url+phone, 18% tag, 14% city, with 4% gaps
    name_h = int(ph * 0.34)
    up_h   = int(ph * 0.24)
    tag_h  = int(ph * 0.16)
    city_h = int(ph * 0.13)
    gap    = int(ph * 0.04)

    # COMPANY NAME — biggest, navy
    f_name = fit_font(FONT_BOLD, BRAND["name"], pw, name_h)
    nw, nh = measure(f_name, BRAND["name"])
    name_x = px0 + (pw - nw) // 2
    name_y = py0
    draw_text_with_halo(draw, (name_x, name_y), BRAND["name"], f_name, NAVY, halo_w=2)

    # Optional thin red rule under name
    rule_y = name_y + nh + gap // 2
    rule_thickness = max(2, ph // 80)
    rule_pad = int(pw * 0.12)
    draw.rectangle(
        [px0 + rule_pad, rule_y, px1 - rule_pad, rule_y + rule_thickness],
        fill=RED,
    )

    # URL + PHONE row — URL navy, phone red
    up_text = f"{BRAND['url']}    {BRAND['phone']}"
    f_up = fit_font(FONT_BOLD, up_text, pw, up_h)
    upw, uph = measure(f_up, up_text)
    up_y = rule_y + rule_thickness + gap
    up_x = px0 + (pw - upw) // 2
    # Render in two pieces to color them separately
    sep_w, _ = measure(f_up, "    ")
    url_w, _ = measure(f_up, BRAND["url"])
    draw_text_with_halo(draw, (up_x, up_y), BRAND["url"], f_up, NAVY, halo_w=2)
    draw_text_with_halo(draw, (up_x + url_w + sep_w, up_y),
                        BRAND["phone"], f_up, RED, halo_w=2)

    # TAGLINE
    f_tag = fit_font(FONT_REG, BRAND["tag"], pw, tag_h)
    tw, th = measure(f_tag, BRAND["tag"])
    tag_y = up_y + uph + gap
    draw_text_with_halo(draw, (px0 + (pw - tw) // 2, tag_y),
                        BRAND["tag"], f_tag, NAVY, halo_w=1)

    # CITY
    f_city = fit_font(FONT_BOLD, BRAND["city"], pw, city_h)
    cw, ch = measure(f_city, BRAND["city"])
    city_y = tag_y + th + gap
    draw_text_with_halo(draw, (px0 + (pw - cw) // 2, city_y),
                        BRAND["city"], f_city, NAVY, halo_w=1)


def draw_rear(canvas: Image.Image, panel: tuple[float, float, float, float]) -> None:
    """Rear-doors layout: huge URL (highway readability), phone red, name, city."""
    W, H = canvas.size
    x0, y0, x1, y1 = panel
    px0, py0 = int(W * x0), int(H * y0)
    px1, py1 = int(W * x1), int(H * y1)
    pw, ph = px1 - px0, py1 - py0
    draw = ImageDraw.Draw(canvas)

    # URL gets the most space (it's the call-to-action people see at a stoplight)
    url_h = int(ph * 0.30)
    phone_h = int(ph * 0.22)
    name_h = int(ph * 0.13)
    city_h = int(ph * 0.10)
    gap = int(ph * 0.05)

    # URL
    f_url = fit_font(FONT_BOLD, BRAND["url"], pw, url_h)
    uw, uh = measure(f_url, BRAND["url"])
    url_y = py0 + gap
    draw_text_with_halo(draw, (px0 + (pw - uw) // 2, url_y),
                        BRAND["url"], f_url, NAVY, halo_w=2)

    # Phone — red
    f_ph = fit_font(FONT_BOLD, BRAND["phone"], pw, phone_h)
    phw, phh = measure(f_ph, BRAND["phone"])
    ph_y = url_y + uh + gap
    draw_text_with_halo(draw, (px0 + (pw - phw) // 2, ph_y),
                        BRAND["phone"], f_ph, RED, halo_w=2)

    # Thin red rule
    rule_y = ph_y + phh + gap // 2
    rule_thickness = max(2, ph // 80)
    rule_pad = int(pw * 0.18)
    draw.rectangle(
        [px0 + rule_pad, rule_y, px1 - rule_pad, rule_y + rule_thickness],
        fill=RED,
    )

    # Company name
    f_name = fit_font(FONT_BOLD, BRAND["name"], pw, name_h)
    nw, nh = measure(f_name, BRAND["name"])
    name_y = rule_y + rule_thickness + gap
    draw_text_with_halo(draw, (px0 + (pw - nw) // 2, name_y),
                        BRAND["name"], f_name, NAVY, halo_w=1)

    # City
    f_city = fit_font(FONT_BOLD, BRAND["city"], pw, city_h)
    cw, ch = measure(f_city, BRAND["city"])
    city_y = name_y + nh + gap
    draw_text_with_halo(draw, (px0 + (pw - cw) // 2, city_y),
                        BRAND["city"], f_city, NAVY, halo_w=1)


def write_meta(out_path: str, label: str, source: str):
    meta = {
        "agent_id": "sam_axe",
        "task_id": "ahbco-vehicle-branding-2026-05-05-v3",
        "created_at": datetime.datetime.now().isoformat(),
        "kind": "wrap_mockup_pil_panel_seated",
        "label": label,
        "source_image": source,
        "method": "PIL text drawn directly onto user-supplied van body within hand-picked panel rectangle. No overlay card.",
    }
    with open(out_path + ".meta", "w") as f:
        json.dump(meta, f, indent=2)


def main() -> int:
    print("AHBCO Vehicle Branding v3 — text seated inside actual panel bounds")
    print(f"Output → {OUT_DIR}\n")

    # Wipe the v2 paste-on mockups before regenerating
    for old in (
        "van_actual_side_left.png",
        "van_actual_side_rearquarter.png",
        "van_actual_rear.png",
    ):
        for x in (os.path.join(OUT_DIR, old), os.path.join(OUT_DIR, old + ".meta")):
            if os.path.isfile(x):
                os.unlink(x)
                print(f"  ✗ removed v2: {os.path.basename(x)}")
    print()

    jobs = [
        ("van side 2.jpeg",  "van_actual_side_left.png",        "Passenger Side", "side_passenger",   "side"),
        ("van side.jpeg",    "van_actual_side_rearquarter.png", "Rear Quarter",   "side_rearquarter", "side"),
        ("van rear.jpeg",    "van_actual_rear.png",             "Rear Doors",     "rear",             "rear"),
    ]
    for src_name, out_name, label, panel_key, kind in jobs:
        src = os.path.join(SOURCE_DIR, src_name)
        out = os.path.join(OUT_DIR, out_name)
        if not os.path.isfile(src):
            print(f"  ! source missing: {src}")
            continue
        canvas = upscale(Image.open(src), target_w=1400)
        if kind == "side":
            draw_side(canvas, PANEL[panel_key])
        else:
            draw_rear(canvas, PANEL[panel_key])
        canvas.save(out, "PNG", optimize=True)
        write_meta(out, label, src)
        w, h = canvas.size
        print(f"  ✓ {out_name}  ({w}x{h}, {os.path.getsize(out)//1024} KB, panel={PANEL[panel_key]})")

    print(f"\nDone. View at: http://localhost:8888/datahub  (project=proj-ahb123)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
