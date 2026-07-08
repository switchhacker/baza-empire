# ahb123.com static site (Cloudflare Pages)

## Build
    venv/bin/python web/ahb123/make_og_image.py   # once, regenerates og image
    venv/bin/python web/ahb123/build.py           # -> web/ahb123/dist/

## Deploy (preview)
One-time: create Cloudflare Pages project `ahb123`; create an API token scoped
to **Pages:Edit**; save it: `echo TOKEN > web/ahb123/.cf_pages_token && chmod 600 web/ahb123/.cf_pages_token`.
    venv/bin/python web/ahb123/deploy.py          # prints the *.pages.dev URL

## Preview verification checklist (on the *.pages.dev URL — Squarespace still live)
- [ ] All 6 pages render with Navy/Oak branding, desktop + mobile
- [ ] All 48 portfolio images load
- [ ] Nova chat widget opens and replies
- [ ] /plan multi-step form submits -> new ahb_clients row (source=plan_page) -> Rex Telegram alert
- [ ] /contact reviews grid + QR load (served from nova.ahb123.com)
- [ ] Footer shows: All Home Building Co LLC · Bensalem, PA 19020 · (215) 554-5488 · contactahbco@gmail.com · PA HIC# PA175897
- [ ] View-source JSON-LD passes Google Rich Results test

## Cutover (ONLY after nameserver migration shows zone "Active" in Cloudflare)
1. Cloudflare Pages -> project ahb123 -> Custom domains -> add `ahb123.com` and `www.ahb123.com`.
   Cloudflare auto-replaces the apex A records / www CNAME.
2. Re-run the verification checklist on the real https://ahb123.com.
3. Leave Squarespace PAID and untouched for a few days (fallback).

## Rollback (any time before cancelling Squarespace)
Repoint DNS back to Squarespace:
    ahb123.com  A      198.49.23.144
    ahb123.com  A      198.185.159.144
    ahb123.com  A      198.185.159.145
    ahb123.com  A      198.49.23.145
    www         CNAME  ext-sq.squarespace.com

## Cancel Squarespace
Only after several days of the real domain serving from Pages with no issues,
Cancel the Squarespace subscription. Then remove the Squarespace-only
`_domainconnect` CNAME from DNS.
