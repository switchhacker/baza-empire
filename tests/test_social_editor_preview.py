"""Social composer media must be served via the social-scoped routes.

Composer source rows carry ABSOLUTE filesystem sub_paths (under
dashboard/artifacts/ or uploads/social). The cloud routes
(/api/cloud/thumb/<path>, /api/cloud/media/serve/<path>) take the path as a
URL segment — Flask collapses the encoded leading slash — and only allow the
ZFS pool dirs, so a composer path always 404s (blank image editor preview,
blank IG grid-preview tile). The social-scoped routes
(/api/ahb/social/media/{serve,thumb}?path=...) exist precisely for this; see
the comment block above social_media_serve() in dashboard/social_studio.py.
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO_ROOT, "dashboard", "templates", "ahb123.html")


def _template():
    with open(TPL) as f:
        return f.read()


def test_no_social_sub_path_fed_to_cloud_routes():
    src = _template()
    bad = [
        m.group(0)
        for m in re.finditer(
            r"/api/cloud/(?:thumb|media/serve)/'\s*\+\s*encodeURIComponent\("
            r"[\w.]*\.(?:sub_path|abs_path)",
            src,
        )
    ]
    assert not bad, (
        "social source paths (absolute) must use /api/ahb/social/media/* "
        f"query-param routes, not cloud segment routes: {bad}"
    )


def test_image_editor_preview_uses_social_serve_route():
    src = _template()
    editor = src.split("SocialStudio.modules.imageEditor", 1)[1]
    assert "/api/ahb/social/media/serve?path=" in editor.split("ie-preview")[0], (
        "imageEditor.open() preview src must go through the social serve route"
    )
