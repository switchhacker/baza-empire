#!/usr/bin/env python3
"""Deploy web/ahb123/dist to Cloudflare Pages via wrangler.

Prerequisite: node + `npx wrangler` available. Token is a Cloudflare API token
scoped to Pages:Edit, stored at web/ahb123/.cf_pages_token (mode 0600).
"""
import os, re, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN = os.path.join(HERE, ".cf_pages_token")
_URL_RE = re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]*\.?pages\.dev")

def load_token(path=DEFAULT_TOKEN):
    with open(path) as f:
        return f.read().strip()

def deploy(dist_dir, project, token, runner=subprocess.run):
    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = token
    argv = ["npx", "--yes", "wrangler", "pages", "deploy", dist_dir,
            f"--project-name={project}"]
    res = runner(argv, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"wrangler deploy failed (rc={res.returncode}): {res.stdout}\n{res.stderr}")
    m = _URL_RE.search(res.stdout or "")
    if not m:
        raise RuntimeError(f"wrangler deploy succeeded but no *.pages.dev URL found in output:\n{res.stdout}")
    return m.group(0)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    ap.add_argument("--project", default="ahb123")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN)
    args = ap.parse_args()
    url = deploy(args.dist, args.project, load_token(args.token_file))
    print(f"deployed: {url}")
