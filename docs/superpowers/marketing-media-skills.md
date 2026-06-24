# Marketing & Media Super Skills — Usage

Bootstrap once: `##SKILL:brand_kit{"mode":"detect"}##` (scrapes ahb123.com -> brand.json).

- `##SKILL:social_campaign{"topic":"kitchen remodel","platforms":["ig_square","fb"],"queue":true,"project_id":4}##`
- `##SKILL:before_after_showcase{"before":"/path/b.jpg","after":"/path/a.jpg","title":"Ritz Remediation","queue":false}##`
- `##SKILL:marketing_flyer{"headline":"Spring Roofing Special","subhead":"20% off","bullets":["Licensed","Free estimates"],"cta":"Call (555) 123-4567"}##`
- `##SKILL:brand_kit{"mode":"show"}##` / `##SKILL:brand_kit{"mode":"set","patch":{"colors":{"accent":"#F39C12"}}}##`

Local-first (Ollama copy, SD imagery); photo-first; queued posts are DRAFTS — approve in Social Studio before publishing.

## Platforms / sizes
ig_square 1080x1080 · ig_reel/tiktok 1080x1920 · fb 1200x630 · yt_thumb 1280x720 · flyer_portrait 1275x1650 · ad_square 1080x1080 · ad_landscape 1200x628

## Where things live
- Brand source of truth: agents/sam_axe/brand/brand.json (+ assets/logo.png)
- Shared lib: skills/shared/media_kit.py
- Artifacts: dashboard/artifacts/<project_id>/ (tracked in dashboard)
- Social queue: ahb_social_posts (status='draft')
