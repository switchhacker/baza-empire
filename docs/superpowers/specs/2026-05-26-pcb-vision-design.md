# PCB Vision — Board photo → labeled overlays + clean schematic

**Date:** 2026-05-26
**Status:** Approved for build
**Author:** Claw Batto (drafted with Serge)
**Builds on:** Baza Projects Live Build Tree (`docs/superpowers/specs/2026-05-24-baza-projects-live-build-tree-design.md`) + Schematic feature (SC1–SC4, commits f49d3b1 / 6bfacb3 / 078ed1d / 099f817).

## Problem

When Serge uploads or pulls in a photo of a board / circuit / PCB / ESP module from his Data Hub, he wants Baza to identify what the board is, what it does, and label every visible component — and then give him a clean schematic-style diagram derived from that photo in the same UI as the existing scaffold schematic editor (`/projects/<id>` → 🌳 Scaffold → schematic node side panel).

## Solution at a glance

A new scaffold node type `pcb_vision` that:

1. Accepts an image (upload or Data Hub pick).
2. Runs a local vision pass (qwen3-vl primary, llava:13b fallback) constrained to the existing `baza_components_library.py` vocabulary.
3. Renders **two side-panel views** the user can toggle between:
   - **📷 Photo view** — original image with editable absolute-positioned overlay rectangles per component, numbered legend on the right showing board label + function summary.
   - **🔌 Schematic view** — the existing SVG schematic editor (unchanged), seeded from the overlays with geometry preserved and an optional "best-guess wires" pass.
4. Stores everything in the existing `scaffold_nodes` payload — no new tables.

The entire feature reuses the SC1–SC4 side-panel + auto-save + schematic editor infrastructure already shipped. Net new code is the vision skill, two routes, the photo-view JS, the upload/pick modal, and a node_type entry.

## Architecture

### Node type

`pcb_vision` added to `VALID_NODE_TYPES` in `core/scaffold_engine.py`. The node lives in the same `scaffold_nodes` table as every other scaffold node — graph CRUD / dep checks / weighted progress / event bus all work unchanged.

### Payload schema (JSON in `scaffold_nodes.payload_json`)

```jsonc
{
  "image_path": "/mnt/empirepool/cloud/1/Imports/2026-05-26-bench/IMG_0421.jpg",
  "image_source": "datahub",          // "upload" | "datahub" | "datahub_private"
  "board_label": "ESP32-WROOM dev board",
  "board_function": "Wi-Fi + BLE MCU, USB-serial via CP2102, 30-pin header",
  "overlays": [
    {
      "id": "ov_1",                   // stable id (used as overlay element id + schematic component id)
      "label": "ESP32-WROOM-32",
      "bbox": [0.12, 0.08, 0.55, 0.42], // x, y, w, h as fractions of image dims (resolution-independent)
      "confidence": 0.91,
      "suggested_part_id": "mcu.esp32_wroom",  // matches baza_components_library.py key, optional
      "user_corrected": false         // flips true when user edits position/label
    }
  ],
  "schematic": {                      // reuses existing schematic JSON format from SC3
    "components": [],
    "wires": [],
    "notes": ""
  },
  "best_guess_wires": false,          // toggle state — whether the next "Generate schematic" includes auto-wires
  "analyzed_at": "2026-05-26T15:32:11Z",
  "model_used": "qwen3-vl:latest"
}
```

`bbox` uses normalized [0,1] coordinates so resizing the side-panel image doesn't break positions.

### Routes (`dashboard/scaffold.py`)

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| POST | `/scaffold/pcb_vision/upload` | `multipart {project_id, parent_id?, file}` | Saves file to `dashboard/artifacts/<project_id>/pcb/<sha8>.<ext>`, then creates the node and kicks off analyze. Returns `{node_id}` |
| POST | `/scaffold/pcb_vision/create_from_datahub` | `{project_id, parent_id?, datahub_path, is_private}` | Path-references an existing Data Hub image (no copy), creates the node, kicks off analyze. Returns `{node_id}` |
| POST | `/scaffold/pcb_vision/analyze/{node_id}` | `{}` or `{mode:'merge'\|'reset'}` | (Re-)runs vision skill on the node's image |
| POST | `/scaffold/pcb_vision/generate_schematic/{node_id}` | `{best_guess_wires: bool}` | Seeds the schematic JSON from current overlays |
| GET | `/scaffold/pcb_vision/image/{node_id}` | — | Serves the image bytes for the side-panel `<img>` (handles both uploaded and Data Hub paths; re-checks vault lock for private files) |
| GET | `/scaffold/pcb_vision/datahub_list` | — | Proxy to existing `/api/datahub/list` filtered to image MIME types (jpg/jpeg/png/webp/heic) |

The existing `PUT /scaffold/nodes/{id}` handles all overlay/schematic edits via auto-save (no new route needed).

**Why a dedicated image-serve route instead of linking directly to the file path?** Three reasons: (1) re-check vault lock state on every fetch so a mid-session lock immediately blocks private images, (2) handle uploaded paths and Data Hub paths through one URL the client doesn't need to know which is which, (3) lets the route set proper `Cache-Control` headers (long-lived for uploads since their filename is sha-based and content-addressed, no-cache for Data Hub paths since the user can replace them).

### Vision skill (`skills/shared/scaffold_analyze_pcb_image.py`)

Input via `SKILL_ARGS`:
```json
{ "node_id": 1234, "image_path": "/path/...", "mode": "merge" }
```

Steps:
1. Open image, downscale to max long-edge 1600px (Lanczos), save temp jpg.
2. Build prompt: system message lists `baza_components_library.py` part ids + human names ("mcu.esp32_wroom — ESP32-WROOM dev module", …) and instructs the model to use `suggested_part_id` from this catalog when confident, free-text label otherwise.
3. Call qwen3-vl with `format=json` and a strict response schema. Hard 90s timeout.
4. On timeout or parse failure → fallback to llava:13b with the same prompt.
5. Validate JSON, clamp bboxes to [0,1], drop entries with confidence < 0.30.
6. If `mode == "merge"`: keep all overlays with `user_corrected: true` from current payload; merge new overlays by IoU > 0.5 (update label/confidence, preserve manual position). Otherwise overwrite all.
7. Write payload back via scaffold_engine, emit event, update node status (`completed` if any components found; `awaiting_input` otherwise).

Returns JSON with the new payload for client.

### Best-guess wires (when toggle is on)

Heuristic, not LLM (deterministic + cheap):
- If a `power.barrel_jack` or `power.usb_micro` overlay exists, draw a power wire from it to every component's `VCC` pin.
- If any GND pad/header is detected, draw ground bus from it to every component's `GND` pin.
- For sensor → MCU pairs (e.g. `sensor.hc_sr04` + `mcu.esp32_wroom`), draw signal wires from sensor data pins to the nearest unused GPIO on the MCU.
- Wires generated this way get `auto_generated: true` so the user can wipe them with one click without losing manual edits.

Skipped if the toggle is off (default).

### Side-panel UI (`dashboard/templates/project_detail.html`)

The existing `ScaffoldUI.openSidePanel(node_id)` is extended with a new branch when `n.node_type === 'pcb_vision'`:

- Header: title, toggle group `[📷 Photo | 🔌 Schematic]`, ✕ close.
- **Photo view**:
  - `<div class="pcb-canvas" style="position:relative">` containing `<img src="/scaffold/pcb_vision/image/{id}">` and one absolute `<div class="ov-box">` per overlay with `left/top/width/height` computed from normalized bbox × natural dimensions.
  - Each overlay is draggable (pointer events; debounce auto-save 500ms after drag end) and click-to-edit-label (inline `<input>`).
  - Toolbar above: "+ Add component", "↻ Re-analyze ▾" (dropdown: *merge* / *reset*), "Generate schematic ▾" (dropdown: *with best-guess wires* / *without*), `□ best-guess wires` checkbox shadowing the toggle.
  - Right column: numbered legend (matches overlay numbers), `board_label`, `board_function`, model + timestamp footer.
- **Schematic view**: identical to the current `n.node_type === 'schematic'` branch (SC3), reading from `payload.schematic`. No changes to that code path.

Switching views does NOT re-fetch — both views share the same payload object in memory.

### Toolbar entry point

Inside the 🌳 Scaffold tab's existing toolbar, next to the "▶ Start scaffold" button:

```
[📸 Add PCB photo]   ← new
```

Click opens a small body-level modal (`pcb-source-modal`) with two tabs:
- **Upload** — `<input type="file" accept="image/*">`, multipart POST to `/scaffold/pcb_vision/upload` which saves + creates + analyzes in one round-trip.
- **Data Hub** — calls `/scaffold/pcb_vision/datahub_list`, renders a grid of thumbnails (uses existing `/api/datahub/thumb/...`), click → `POST /scaffold/pcb_vision/create_from_datahub`.

Both responses return `{node_id}`; client then closes the modal and calls `ScaffoldUI.openSidePanel(node_id)` to land directly on the analyzing-state photo view.

Private vault images are listed only if `/api/datahub/private/status` reports unlocked — matches existing privacy gate behaviour.

### Auto-save flow

Reuses the existing `_schem` change-detect + debounced PUT pattern from `project_detail.html:981-1046`. Overlays are part of the same payload, so a drag/edit triggers the same auto-save with no new client code beyond reading from `payload.overlays`.

### Storage

| Where | What |
|-------|------|
| `scaffold_nodes` row | Node metadata + payload JSON (new keys above) |
| `dashboard/artifacts/<project_id>/pcb/<sha8>.jpg` | Uploaded images only; Data Hub picks are path-referenced (not copied) |
| `claw_reviews.db` | The continuous reviewer will pick up the new code paths automatically on next commit |

No new tables. No schema migration required beyond extending the `VALID_NODE_TYPES` whitelist.

## Edge cases

- **Vision returns nothing** → node status `awaiting_input`, photo view shows "Couldn't identify anything — try a higher-res photo. ↻ Re-analyze" and the model + timestamp.
- **Bbox out of range** → clamp to [0,1] server-side before persisting.
- **Image deleted from disk** → photo view shows broken-image placeholder + "Source missing" banner; node not auto-deleted.
- **Re-analyze (merge) with all overlays user-corrected** → new vision pass becomes no-op (every overlay is preserved); side panel toasts "No new components found".
- **Re-analyze (reset)** → wipes all overlays including user-corrected ones; confirmation prompt before firing.
- **HEIC images** → downscale step uses `pyheif` if available, else server returns 415 and the modal tells the user to convert.
- **Data Hub private files** → if vault locks between modal-open and selection, server rejects with 403 and the modal re-checks lock status.

## Out of scope (YAGNI)

- Auto-tracing actual PCB traces from photos — vision is not reliable enough; wires stay manual or come from the best-guess heuristic only.
- 3D `.glb` render of the analyzed board (already a noted Phase 3 follow-up in baza-map for the schematic feature).
- Cross-project component aggregation from PCB scans (Phase 3 stub).
- OCR'ing silkscreen text into part numbers — model already returns labels; an extra OCR pass adds latency for marginal gain.
- Mobile camera capture path — uploads via file input cover phone "Take a photo" on iOS/Android natively.

## Testing

`tests/scaffold_pcb_vision_test.py`:
- Node create with both image sources.
- Vision skill mock that returns deterministic JSON → merge mode preserves `user_corrected: true` overlays via IoU > 0.5.
- Reset mode wipes everything.
- Best-guess wires heuristic generates the expected wires for a fixture payload (USB + ESP32 + HC-SR04).
- Bbox clamp rejects out-of-range and invalid types.
- Privacy gate: vault-locked Data Hub pick rejected with 403.
- Empty vision response → status flips to `awaiting_input`.

## Files

**New:**
- `skills/shared/scaffold_analyze_pcb_image.py`
- `tests/scaffold_pcb_vision_test.py`

**Modified:**
- `core/scaffold_engine.py` — add `'pcb_vision'` to `VALID_NODE_TYPES`
- `dashboard/scaffold.py` — 4 new routes + upload handler
- `dashboard/templates/project_detail.html` — toolbar button, source modal, side-panel branch, photo-view JS (~250 lines)
- `core/baza_components_library.py` — no changes (read-only consumer)

## Rollout

1. Build behind no flag — additive only, doesn't touch existing schematic flow.
2. Manual smoke test on a real PCB photo from Serge's Data Hub.
3. Dashboard restart picks up template + route changes.
4. baza-map.md §4 services table unchanged (no new systemd unit).
5. Continuous Claw reviewer will produce per-file findings on commit; address any blockers before announcing.

## Open questions (none — all resolved during brainstorm)

- ~~Entry point: separate tab vs. scaffold toolbar~~ → **scaffold toolbar**
- ~~Auto-wire policy~~ → **best-guess toggle, heuristic-only, off by default**
- ~~Re-analyze overwrite policy~~ → **both modes available via dropdown (merge default, reset with confirm)**
