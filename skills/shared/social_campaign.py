#!/usr/bin/env python3
"""social_campaign — topic/project -> per-platform on-brand post pack.

Generates copy (local Ollama), composes a branded image per platform
(photo-first; SD background fallback), saves each as an artifact, and
(optionally) queues each as a DRAFT in Social Studio (never auto-publishes).
Usage: ##SKILL:social_campaign{"topic":"kitchen remodel","platforms":["ig_square","fb"],"queue":true}##
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402

# test/ops overrides
if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])

DEFAULT_PLATFORMS = ["ig_square", "ig_reel", "fb", "yt_thumb"]


def _compose(platform, photo, copy, brand):
    w, h = media_kit.PLATFORMS[platform]
    base = None
    if photo and Path(photo).exists():
        base = media_kit.load_photo(photo, (w, h))
    else:
        bg = media_kit.gen_background(
            f"professional home renovation, {copy['caption'][:60]}, clean, bright",
            w, h)
        if bg and Path(bg).exists():
            base = media_kit.load_photo(bg, (w, h))
    if base is None:
        base = media_kit.new_canvas(platform,
                                    bg=media_kit.hex_to_rgb(brand["colors"]["primary"]))
    media_kit.scrim(base, side="bottom", height_frac=0.45)
    headline = copy["caption"].split(".")[0][:70]
    media_kit.draw_headline(base, headline,
                            (int(w * 0.06), int(h * 0.62), int(w * 0.94), int(h * 0.9)),
                            color=(255, 255, 255),
                            font_path=brand["fonts"]["headline"])
    media_kit.place_logo(base, brand, corner="tr")
    return base


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    topic = args.get("topic", "")
    if not topic and not args.get("project_id"):
        print(json.dumps({"error": "topic or project_id required"})); return
    platforms = args.get("platforms") or DEFAULT_PLATFORMS
    do_queue = bool(args.get("queue", True))
    project_id = args.get("project_id")
    photo = args.get("photo", "")

    brand = media_kit.load_brand()
    copy = media_kit.write_copy(topic or f"project {project_id}", brand, kind="caption")

    artifacts, queued, warnings = [], [], []
    for plat in platforms:
        if plat not in media_kit.PLATFORMS:
            warnings.append(f"unknown platform {plat}"); continue
        img = _compose(plat, photo, copy, brand)
        fname = f"campaign_{(topic or 'project').replace(' ', '_')[:30]}_{plat}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(project_id or "shared"),
            description=f"Social campaign ({plat}): {topic}",
            tags=["social", "campaign", plat])
        if not saved.get("success"):
            warnings.append(f"save failed {plat}: {saved.get('error')}"); continue
        artifacts.append(saved)
        if do_queue:
            pid = media_kit.queue_social_post(
                platform=plat, variant="feed", asset_path=saved["path"],
                caption=copy["caption"], hashtags=copy["hashtags"],
                first_comment=copy.get("first_comment", ""),
                project_id=project_id,
                ai_meta={"copy_model": copy["model"], "topic": topic})
            queued.append({"platform": plat, "post_id": pid})

    print(json.dumps({"skill": "social_campaign", "copy": copy,
                      "artifacts": artifacts, "queued": queued,
                      "warnings": warnings}))


if __name__ == "__main__":
    main()
