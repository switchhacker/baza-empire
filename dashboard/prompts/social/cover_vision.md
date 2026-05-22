# Cover-Pick Vision Prompt v1

You will be shown N candidate frames (1 per message). Choose the single most arresting cover for a social video.

Rules:
- Output ONLY JSON: {"index": <0-based int>, "reason": "<1 sentence>"}.
- Prefer faces, eye contact, dramatic lighting, action mid-motion.
- Avoid blurry, occluded, or off-center subjects.
- If two frames are tied, prefer the earlier one.
