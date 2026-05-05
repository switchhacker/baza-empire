#!/usr/bin/env python3
"""
Generate AHB vehicle signage mockups via Stable Diffusion WebUI and save to
dashboard/artifacts/proj-ahb123/ so they show up in the Data Hub.

Outputs:
  van_signage_side_left.png
  van_signage_side_right.png
  van_signage_rear_window.png
  van_signage_full_wrap.png
  AHBCO_Vehicle_Branding_Spec.md

Each .png has a .meta sidecar so Data Hub's filters work.
"""
import base64
import datetime
import json
import os
import sys
import time
import urllib.request

SD_URL = "http://localhost:7860"
ART_DIR = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/artifacts/proj-ahb123"
os.makedirs(ART_DIR, exist_ok=True)

BRAND = {
    "company": "ALL HOME BUILDING CO LLC",
    "short": "AHB",
    "url": "AHB123.COM",
    "phone": "800-484-6404",
    "city": "Bensalem, PA 19020",
    "tagline": "Renovation • Construction • Roofing",
    "primary": "deep navy blue (#0d2b5e)",
    "accent": "fire red (#e94560)",
    "neutral": "white (#ffffff)",
}

NEG = ("low quality, blurry, distorted text, gibberish text, misspelled, "
       "multiple vehicles, watermark, signature, photo from far away, "
       "deformed, cluttered background, ugly typography, comic sans")


def prompt_for(angle: str) -> str:
    if angle == "side_left":
        return (
            "professional commercial cargo van vehicle wrap mockup, full driver-side "
            f"profile view, large bold serif company name '{BRAND['company']}' "
            f"across the side panel, secondary line with website '{BRAND['url']}' "
            f"and phone number '{BRAND['phone']}' beneath, small line at bottom "
            f"'{BRAND['city']}', clean modern construction-company branding, deep "
            f"navy blue and fire red accent colors on white base, sharp readable "
            f"typography, photorealistic 3/4 angle product render, sharp daylight, "
            f"studio backdrop, 4k product photography"
        )
    if angle == "side_right":
        return (
            "professional commercial cargo van vehicle wrap mockup, full passenger-side "
            f"profile view, large bold sans-serif '{BRAND['company']}' across the "
            f"side, web '{BRAND['url']}' and phone '{BRAND['phone']}' below, "
            f"'{BRAND['tagline']}' in smaller type, deep navy blue and fire red "
            f"on white base, photorealistic, sharp clean typography"
        )
    if angle == "rear":
        return (
            "commercial van rear view mockup, AHB construction company branding, "
            f"large rear-window lettering '{BRAND['url']}' centered, phone "
            f"'{BRAND['phone']}' below, smaller '{BRAND['city']}' line, perforated "
            f"window vinyl style allowing visibility through, navy blue lettering "
            f"with red accent stripe, clean professional contractor branding, "
            f"photorealistic, daylight"
        )
    if angle == "full":
        return (
            "professional 3/4 view product render of a white commercial cargo van "
            f"with full vehicle wrap branding, large bold '{BRAND['company']}' "
            f"company name on side panel, '{BRAND['url']}' website prominent, "
            f"'{BRAND['phone']}' phone below, '{BRAND['city']}' location, "
            f"navy blue and fire red color scheme on white base, clean modern "
            f"construction-company branding, photorealistic, sharp typography, "
            f"showroom lighting"
        )
    return ""


def generate(angle: str) -> str | None:
    prompt = prompt_for(angle)
    payload = {
        "prompt": prompt,
        "negative_prompt": NEG,
        "width": 1024,
        "height": 768,
        "steps": 32,
        "cfg_scale": 7.5,
        "sampler_name": "DPM++ 2M Karras",
        "seed": -1,
        "n_iter": 1,
    }
    req = urllib.request.Request(
        SD_URL + "/sdapi/v1/txt2img",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"  → generating {angle} ({payload['width']}x{payload['height']}, {payload['steps']} steps)…")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ✗ failed: {e}")
        return None
    dt = time.time() - t0
    images = result.get("images") or []
    if not images:
        print("  ✗ no images returned")
        return None
    fname = f"van_signage_{angle}.png"
    fpath = os.path.join(ART_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(images[0]))
    # Meta sidecar so Data Hub agent filter works
    meta = {
        "agent_id": "sam_axe",
        "task_id": "ahbco-vehicle-branding-2026-05-05",
        "created_at": datetime.datetime.now().isoformat(),
        "kind": "render",
        "prompt": prompt[:400],
        "duration_s": round(dt, 1),
    }
    with open(fpath + ".meta", "w") as f:
        json.dump(meta, f)
    print(f"  ✓ {fname}  ({os.path.getsize(fpath)//1024} KB, {dt:.1f}s)")
    return fname


def main() -> int:
    angles = ["side_left", "side_right", "rear", "full"]
    print(f"Generating {len(angles)} AHBCO vehicle-branding mockups → {ART_DIR}")
    saved = []
    for a in angles:
        out = generate(a)
        if out:
            saved.append(out)
        time.sleep(1)  # let GPU breathe between requests

    # Write a real design spec markdown
    spec_path = os.path.join(ART_DIR, "AHBCO_Vehicle_Branding_Spec.md")
    spec = f"""# AHBCO Vehicle Branding Specification

**Project:** AHB van wrap & rear-window signage
**Generated:** {datetime.datetime.now().isoformat()}
**Source images:** dashboard/artifacts/data-hub/van side.jpeg, van side 2.jpeg, van rear.jpeg

## Brand Information

| Field | Value |
|---|---|
| Company | {BRAND['company']} |
| Web | {BRAND['url']} |
| Phone | {BRAND['phone']} |
| Address | {BRAND['city']} |
| Tagline | {BRAND['tagline']} |

## Color System

| Role | Color | Hex |
|---|---|---|
| Primary | Deep navy blue | #0d2b5e |
| Accent | Fire red | #e94560 |
| Neutral | White | #ffffff |

## Typography

- **Headline:** bold sans-serif (Montserrat ExtraBold or Helvetica Black). All-caps for company name. Minimum 6 inch cap height on side panels for highway readability.
- **Web URL:** same family, regular weight, 60% the size of the headline.
- **Phone number:** same as URL — high-contrast, easy to call from a moving lane.
- **Address line:** small, light weight, footer-style.

## Layout — Side Panels (driver + passenger)

```
┌──────────────────────────────────────────────────┐
│                                                  │
│   ALL HOME BUILDING CO LLC                       │  ← navy, large
│                                                  │
│   AHB123.COM     800-484-6404                    │  ← red+navy, mid
│                                                  │
│   Renovation • Construction • Roofing            │  ← navy, small italic
│   Bensalem, PA 19020                             │  ← navy, small
│                                                  │
└──────────────────────────────────────────────────┘
```

- Keep negative space generous; do not crowd the headline.
- Red accent stripe optional below address line, full width, 1.5" tall.
- Avoid placing text over wheel arches or door seams — use the flat panels.

## Layout — Rear Window (perforated vinyl, see-through)

```
            AHB123.COM
        800-484-6404
         Bensalem, PA 19020
```

- Use 50/50 perforated vinyl so the driver can see out.
- Center on the rear window pane; leave 1.5" margin from window edge.
- Navy lettering only — red accent reserved for body panels.

## Print / Wrap Vendor Notes

- Substrate: 3M IJ180Cv3 or Avery MPI 1105 cast vinyl (5–7yr durability).
- Lamination: gloss or matte UV laminate.
- Rear window: 3M Scotchcal 8170 perforated, 50/50 transparency.
- Provide vector source files (.ai or .pdf) — no rasterized text.

## Assumptions

- Branding info and contact details are confirmed as listed above.
- White-body cargo van as in the source photos; design treats this as the canvas.
- Mockups are illustrative — final renders should be done by the wrap vendor over their template using the brand spec above.

## Mockups (this directory)

{chr(10).join(f"- `{f}`" for f in saved)}

## Reference photos (already in Data Hub)

- `dashboard/artifacts/data-hub/van side.jpeg`
- `dashboard/artifacts/data-hub/van side 2.jpeg`
- `dashboard/artifacts/data-hub/van rear.jpeg`

---
**Saved by:** `gen_van_signage.py` — 2026-05-05
"""
    with open(spec_path, "w") as f:
        f.write(spec)
    print(f"  ✓ AHBCO_Vehicle_Branding_Spec.md  ({os.path.getsize(spec_path)//1024} KB)")
    saved.append(os.path.basename(spec_path))

    print(f"\nDone. {len(saved)} files in {ART_DIR}")
    print("View at: http://localhost:8888/datahub  (filter agent=sam_axe, project=proj-ahb123)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
