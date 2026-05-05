#!/usr/bin/env python3
"""
AHBCO Vehicle Signage Mockup — v2

Replaces the previous SD txt2img approach (generated a generic van with
illegible text) with a PIL overlay onto the user's actual Ford Transit
van photos. The vehicle stays the user's vehicle. The text renders
crisply because it's drawn with PIL, not hallucinated by a diffusion
model. Output is what the wrap vendor needs: "this is your van, this is
where the lettering goes, this is what it says".

Outputs (in dashboard/artifacts/proj-ahb123/):
  van_actual_side_left.png      — passenger-side profile w/ overlay
  van_actual_side_rearquarter.png — rear-quarter view w/ overlay
  van_actual_rear.png           — rear doors w/ overlay
  AHBCO_Vehicle_Branding_Spec.md — refreshed brand spec

Brand:
  ALL HOME BUILDING CO LLC
  AHB123.COM
  800-484-6404
  Bensalem, PA 19020
  navy #0d2b5e, red #e94560 on white
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = "/home/switchhacker/baza-empire/agent-framework-v3"
SOURCE_DIR = os.path.join(ROOT, "dashboard", "artifacts", "data-hub")
OUT_DIR = os.path.join(ROOT, "dashboard", "artifacts", "proj-ahb123")
os.makedirs(OUT_DIR, exist_ok=True)

NAVY = (13, 43, 94)       # #0d2b5e
RED  = (233, 69, 96)      # #e94560
WHITE = (255, 255, 255)
BLACK_SHADOW = (0, 0, 0, 180)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

BRAND = {
    "name": "ALL HOME BUILDING CO LLC",
    "short": "AHB",
    "url": "AHB123.COM",
    "phone": "800-484-6404",
    "city": "BENSALEM, PA 19020",
    "tag": "RENOVATION  •  CONSTRUCTION  •  ROOFING",
}


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def upscale(img: Image.Image, target_w: int = 1280) -> Image.Image:
    """Upscale tiny stock photos to working size for clean text overlay."""
    w, h = img.size
    if w >= target_w:
        return img.convert("RGB")
    ratio = target_w / w
    return img.resize((target_w, int(h * ratio)), Image.LANCZOS).convert("RGB")


def text_with_outline(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                      font: ImageFont.FreeTypeFont, fill: tuple,
                      outline: tuple = WHITE, outline_w: int = 3):
    """Draw text with a halo so it's legible on any background."""
    x, y = xy
    for dx in range(-outline_w, outline_w + 1):
        for dy in range(-outline_w, outline_w + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text(xy, text, font=font, fill=fill)


def measure(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_font(path: str, text: str, max_w: int, start_size: int = 80,
             min_size: int = 18) -> ImageFont.FreeTypeFont:
    """Find the largest font size that keeps text under max_w."""
    size = start_size
    while size > min_size:
        f = load_font(path, size)
        w, _ = measure(f, text)
        if w <= max_w:
            return f
        size -= 2
    return load_font(path, min_size)


def overlay_side(img_path: str, out_path: str, label: str) -> dict:
    """Overlay branding on a side-view van image.

    Treats the van as occupying most of the frame; places a wrap-style text
    block on the side panel area (mid-height band, indented from edges).
    """
    src = Image.open(img_path)
    canvas = upscale(src, target_w=1400)
    W, H = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Define the lettering band on the side panel
    # Side panels on a high-roof Transit sit roughly between 38% and 70% of height.
    band_top = int(H * 0.38)
    band_bot = int(H * 0.72)
    band_left = int(W * 0.16)
    band_right = int(W * 0.92)
    band_w = band_right - band_left
    band_h = band_bot - band_top

    # Translucent white card for legibility on real-photo background
    card = Image.new("RGBA", (band_w, band_h), (255, 255, 255, 215))
    canvas.paste(card, (band_left, band_top), card)

    # Red accent stripe at the top edge of the card
    stripe_h = max(6, band_h // 18)
    draw.rectangle(
        [band_left, band_top, band_right, band_top + stripe_h],
        fill=RED + (255,),
    )

    # Company name — fit to width
    pad_x = 24
    avail = band_w - 2 * pad_x
    f_name = fit_font(FONT_BOLD, BRAND["name"], avail, start_size=int(band_h * 0.32))
    nw, nh = measure(f_name, BRAND["name"])
    name_y = band_top + stripe_h + int(band_h * 0.07)
    name_x = band_left + (band_w - nw) // 2
    draw.text((name_x, name_y), BRAND["name"], font=f_name, fill=NAVY)

    # URL + phone row — same family, ~60% size
    url_phone = f"{BRAND['url']}     {BRAND['phone']}"
    f_up = fit_font(FONT_BOLD, url_phone, avail, start_size=int(band_h * 0.22))
    upw, uph = measure(f_up, url_phone)
    up_y = name_y + nh + int(band_h * 0.06)
    up_x = band_left + (band_w - upw) // 2
    # Draw URL in navy, phone in red, with a separator
    f_sep_w, _ = measure(f_up, "     ")
    f_url_w, _ = measure(f_up, BRAND["url"])
    draw.text((up_x, up_y), BRAND["url"], font=f_up, fill=NAVY)
    draw.text((up_x + f_url_w + f_sep_w, up_y), BRAND["phone"], font=f_up, fill=RED)

    # Tagline + city line
    f_tag = fit_font(FONT_REG, BRAND["tag"], avail, start_size=int(band_h * 0.13))
    tw, th = measure(f_tag, BRAND["tag"])
    tag_y = up_y + uph + int(band_h * 0.06)
    tag_x = band_left + (band_w - tw) // 2
    draw.text((tag_x, tag_y), BRAND["tag"], font=f_tag, fill=NAVY)

    f_city = load_font(FONT_BOLD, max(14, int(band_h * 0.11)))
    cw, ch = measure(f_city, BRAND["city"])
    city_y = tag_y + th + int(band_h * 0.03)
    city_x = band_left + (band_w - cw) // 2
    draw.text((city_x, city_y), BRAND["city"], font=f_city, fill=NAVY)

    # Caption strip at very bottom of the canvas with the file label
    cap_h = 36
    cap = Image.new("RGBA", (W, cap_h), (13, 11, 30, 230))
    canvas.paste(cap, (0, H - cap_h), cap)
    f_cap = load_font(FONT_REG, 18)
    cap_text = f"AHBCO Vehicle Branding Mockup — {label}"
    cw2, ch2 = measure(f_cap, cap_text)
    draw.text(((W - cw2) // 2, H - cap_h + (cap_h - ch2) // 2 - 2),
              cap_text, font=f_cap, fill=WHITE)

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return {"path": out_path, "size": os.path.getsize(out_path),
            "dimensions": canvas.size}


def overlay_rear(img_path: str, out_path: str, label: str) -> dict:
    """Overlay branding on the rear of the van.

    Rear of the user's Transit has flat panel doors (no rear window glass).
    Layout: large URL on upper door area, phone on lower, city footer line.
    """
    src = Image.open(img_path)
    canvas = upscale(src, target_w=1400)
    W, H = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")

    # The rear doors occupy roughly the central column of the rear shot
    # On a Transit rear photo: doors are roughly between 22% and 78% of width
    # vertical span between 18% and 76% of height
    door_left = int(W * 0.22)
    door_right = int(W * 0.78)
    door_top = int(H * 0.20)
    door_bot = int(H * 0.78)
    dw = door_right - door_left
    dh = door_bot - door_top

    # Translucent white panel for legibility on real-photo background
    card = Image.new("RGBA", (dw, dh), (255, 255, 255, 200))
    canvas.paste(card, (door_left, door_top), card)

    # Red top stripe + bottom stripe, framing the wrap area
    stripe_h = max(6, dh // 30)
    draw.rectangle(
        [door_left, door_top, door_right, door_top + stripe_h],
        fill=RED + (255,),
    )
    draw.rectangle(
        [door_left, door_bot - stripe_h, door_right, door_bot],
        fill=RED + (255,),
    )

    # Big URL (the prime call-to-action on the back of a moving van)
    pad_x = 22
    avail = dw - 2 * pad_x
    f_url = fit_font(FONT_BOLD, BRAND["url"], avail, start_size=int(dh * 0.32))
    uw, uh = measure(f_url, BRAND["url"])
    url_y = door_top + stripe_h + int(dh * 0.10)
    draw.text((door_left + (dw - uw) // 2, url_y),
              BRAND["url"], font=f_url, fill=NAVY)

    # Phone — slightly smaller, red
    f_ph = fit_font(FONT_BOLD, BRAND["phone"], avail, start_size=int(dh * 0.22))
    pw, ph = measure(f_ph, BRAND["phone"])
    ph_y = url_y + uh + int(dh * 0.08)
    draw.text((door_left + (dw - pw) // 2, ph_y),
              BRAND["phone"], font=f_ph, fill=RED)

    # Company name in smaller navy text underneath
    f_name = fit_font(FONT_BOLD, BRAND["name"], avail, start_size=int(dh * 0.13))
    nw, nh = measure(f_name, BRAND["name"])
    name_y = ph_y + ph + int(dh * 0.10)
    draw.text((door_left + (dw - nw) // 2, name_y),
              BRAND["name"], font=f_name, fill=NAVY)

    # City line at very bottom
    f_city = fit_font(FONT_REG, BRAND["city"], avail, start_size=int(dh * 0.10))
    cw, ch = measure(f_city, BRAND["city"])
    draw.text((door_left + (dw - cw) // 2,
               door_bot - stripe_h - ch - int(dh * 0.02)),
              BRAND["city"], font=f_city, fill=NAVY)

    # Caption strip
    cap_h = 36
    cap = Image.new("RGBA", (W, cap_h), (13, 11, 30, 230))
    canvas.paste(cap, (0, H - cap_h), cap)
    f_cap = load_font(FONT_REG, 18)
    cap_text = f"AHBCO Vehicle Branding Mockup — {label}"
    cw2, ch2 = measure(f_cap, cap_text)
    draw.text(((W - cw2) // 2, H - cap_h + (cap_h - ch2) // 2 - 2),
              cap_text, font=f_cap, fill=WHITE)

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return {"path": out_path, "size": os.path.getsize(out_path),
            "dimensions": canvas.size}


def write_meta(out_path: str, label: str, source: str):
    meta = {
        "agent_id": "sam_axe",
        "task_id": "ahbco-vehicle-branding-2026-05-05-v2",
        "created_at": datetime.datetime.now().isoformat(),
        "kind": "wrap_mockup_pil_overlay",
        "label": label,
        "source_image": source,
        "method": "PIL overlay on user-supplied van photo (upscaled, branding rendered with DejaVu Sans Bold)",
    }
    with open(out_path + ".meta", "w") as f:
        json.dump(meta, f, indent=2)


def cleanup_bad_v1():
    """Remove the previous SD-generic mockups so the user doesn't see them."""
    for old in (
        "van_signage_full.png",
        "van_signage_rear.png",
        "van_signage_side_left.png",
        "van_signage_side_right.png",
    ):
        p = os.path.join(OUT_DIR, old)
        m = p + ".meta"
        for x in (p, m):
            if os.path.isfile(x):
                os.unlink(x)
                print(f"  ✗ removed bad v1: {os.path.basename(x)}")


def write_spec(saved: list[dict]):
    spec = f"""# AHBCO Vehicle Branding Specification — v2

**Project:** AHB van wrap mockups using actual vehicle photos
**Vehicle:** White Ford Transit high-roof cargo van (Serge's actual van — see source images below)
**Generated:** {datetime.datetime.now().isoformat()}
**Method:** PIL overlay on user-supplied photos. NOT a diffusion-model render.
The vehicle in every mockup IS Serge's vehicle.

## Brand Information (locked)

| Field | Value |
|---|---|
| Company | {BRAND['name']} |
| Web | {BRAND['url']} |
| Phone | {BRAND['phone']} |
| Address | {BRAND['city'].title()} |
| Tagline | {BRAND['tag']} |

## Color System

| Role | Color | Hex |
|---|---|---|
| Primary | Deep navy blue | #0d2b5e |
| Accent | Fire red | #e94560 |
| Neutral | White | #ffffff |

## Typography (in mockups)

- **DejaVu Sans Bold** — close stand-in for Montserrat ExtraBold which the wrap
  vendor should use in production. The mockup uses DejaVu because it ships
  with the system; the brand spec calls for Montserrat at print time.
- All-caps for company name and city. Mixed case for tagline.

## Layout — Side panels

```
[ red accent stripe ─────────────────────────────────────── ]
       ALL HOME BUILDING CO LLC                  ← navy, large
       AHB123.COM    800-484-6404                ← navy + red, mid
       RENOVATION • CONSTRUCTION • ROOFING       ← navy, small
       BENSALEM, PA 19020                        ← navy, small
```

The text band sits on the flat side panel between the rear wheel arch
and the rear lights / corner pillar.

## Layout — Rear doors

```
[ red accent stripe ────── ]
       AHB123.COM             ← navy, very large (highway readability)
       800-484-6404           ← red, large
       ALL HOME BUILDING CO LLC  ← navy, mid
       BENSALEM, PA 19020     ← navy, small
[ red accent stripe ────── ]
```

The user mentioned "rear window pane" — this Transit has solid rear
panel doors (no glass). The mockup treats those doors as the canvas.
If the production van has a rear window option, the same layout
applies with 50/50 perforated vinyl over the glass.

## Print / Wrap Vendor Notes

- Substrate: 3M IJ180Cv3 or Avery MPI 1105 cast vinyl (5–7yr durability)
- Lamination: gloss UV laminate
- Provide vector source files (.ai or .pdf) — no rasterized text in production
- Mockups in this directory are for layout discussion, not for printing

## Mockups (saved in this directory)

{chr(10).join(f"- `{os.path.basename(s['path'])}` — {s['dimensions'][0]}x{s['dimensions'][1]}, {s['size']//1024} KB" for s in saved)}

## Source photos (Serge's actual van — already in Data Hub)

- `dashboard/artifacts/data-hub/van side.jpeg` (rear-quarter)
- `dashboard/artifacts/data-hub/van side 2.jpeg` (passenger profile)
- `dashboard/artifacts/data-hub/van rear.jpeg` (rear doors)

## Why the previous mockups were wrong

The first attempt used Stable Diffusion txt2img which generated a
generic van and rendered text as illegible scribbles. That's been
deleted. This v2 keeps Serge's actual van and overlays real text
on top — the way wrap-mockup tools (e.g. Vinyl Wrap Studio) work.

---
**Saved by:** `gen_van_signage_v2.py` — 2026-05-05
"""
    spec_path = os.path.join(OUT_DIR, "AHBCO_Vehicle_Branding_Spec.md")
    with open(spec_path, "w") as f:
        f.write(spec)
    print(f"  ✓ updated {os.path.basename(spec_path)}")


def main() -> int:
    print(f"AHBCO Vehicle Branding v2 — using actual van photos")
    print(f"Output → {OUT_DIR}\n")

    cleanup_bad_v1()
    print()

    saved: list[dict] = []
    jobs = [
        # (source, output, label, overlay_fn)
        ("van side 2.jpeg",  "van_actual_side_left.png",        "Passenger Side", overlay_side),
        ("van side.jpeg",    "van_actual_side_rearquarter.png", "Rear Quarter View", overlay_side),
        ("van rear.jpeg",    "van_actual_rear.png",             "Rear Doors", overlay_rear),
    ]
    for src_name, out_name, label, fn in jobs:
        src_path = os.path.join(SOURCE_DIR, src_name)
        out_path = os.path.join(OUT_DIR, out_name)
        if not os.path.isfile(src_path):
            print(f"  ! source missing: {src_path}")
            continue
        info = fn(src_path, out_path, label)
        write_meta(out_path, label, src_path)
        saved.append({"path": out_path, **info})
        print(f"  ✓ {out_name}  ({info['dimensions'][0]}x{info['dimensions'][1]}, {info['size']//1024} KB)")

    write_spec(saved)
    print(f"\nDone. {len(saved)} mockup(s) + spec saved.")
    print("View at: http://localhost:8888/datahub  (filter project=proj-ahb123, agent=sam_axe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
