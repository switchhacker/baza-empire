from browser.page_to_md import page_to_md

HTML = """<!doctype html><html><head>
<title>  Widget   Prices </title>
<meta name="description" content="Best widget prices in PA">
<script>alert('evil')</script><style>.x{color:red}</style>
</head><body>
<nav><a href="/nav1">Nav</a></nav>
<article><h1>Widget Prices</h1>
<p>The blue widget costs $5. The red widget costs $9.</p>
<a href="/products/blue">Blue widget</a>
<a href="https://other.com/red#frag">Red widget</a>
<a href="/products/blue">Blue again (dup)</a>
</article></body></html>"""


def test_extracts_markdown_and_title():
    out = page_to_md(HTML, "https://shop.example.com/list")
    assert "Widget Prices" in out["markdown"]
    assert "$5" in out["markdown"]
    assert "alert('evil')" not in out["markdown"]
    assert out["title"] == "Widget Prices"


def test_links_absolute_and_deduped():
    out = page_to_md(HTML, "https://shop.example.com/list")
    assert "https://shop.example.com/products/blue" in out["links"]
    assert "https://other.com/red" in out["links"]
    assert out["links"].count("https://shop.example.com/products/blue") == 1


def test_truncation():
    big = "<html><body><article><p>" + ("word " * 5000) + "</p></article></body></html>"
    out = page_to_md(big, "https://x.com", max_chars=500)
    assert len(out["markdown"]) <= 500
    assert out["truncated"] is True


def test_garbage_html_does_not_crash():
    out = page_to_md("<<<>>>not html at all", "https://x.com")
    assert isinstance(out["markdown"], str)
    assert out["links"] == []
