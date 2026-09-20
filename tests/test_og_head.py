"""Head rewriting for /s/:token share links.

Pure unit tests — no DB, no app. The DB path (token -> archetype) is already
covered by the visibility tests; what is untested and easy to get wrong is the
string surgery on index.html and the escaping of a user-controlled handle.
"""

import html

from app.routers.og import _render, _strip_head_tags, _tags

# A cut-down copy of the frontend's index.html: the tags that have to be
# removed, one that has to survive, and the two script blocks.
SHELL = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Bibliome — The Emotional Fingerprint of Your Reading Life</title>
    <meta name="description" content="Landing copy." />
    <meta name="robots" content="index, follow" />
    <link rel="canonical" href="https://bibliome.app/" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="Landing title" />
    <meta property="og:image" content="https://bibliome.app/og-image.png" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="Landing twitter title" />
    <script type="application/ld+json">{"@type": "FAQPage"}</script>
    <script>document.documentElement.setAttribute("data-theme", "light");</script>
    <link rel="icon" href="/favicon.svg" />
    <link rel="manifest" href="/manifest.webmanifest" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/assets/index-abc123.js"></script>
  </body>
</html>"""


def test_strip_removes_every_landing_head_tag():
    out = _strip_head_tags(SHELL)
    for gone in ("<title>", 'name="description"', 'name="robots"',
                 'rel="canonical"', 'property="og:', 'name="twitter:'):
        assert gone not in out, gone


def test_strip_removes_landing_structured_data_but_not_the_theme_script():
    out = _strip_head_tags(SHELL)
    # A share card is noindex and is neither the product page nor an FAQ.
    assert "FAQPage" not in out
    assert "application/ld+json" not in out
    # The inline theme script decides Vellum/Lamplight before first paint.
    # Losing it makes every shared card flash the wrong theme.
    assert 'setAttribute("data-theme"' in out


def test_strip_keeps_everything_else():
    out = _strip_head_tags(SHELL)
    # The asset script is the whole reason we serve the real shell rather than
    # a bare page — losing it would boot nothing.
    assert '/assets/index-abc123.js' in out
    assert 'rel="manifest"' in out
    assert 'rel="icon"' in out
    assert '<div id="root"></div>' in out
    assert 'charset="UTF-8"' in out


def test_strip_only_touches_the_head():
    body = SHELL.replace("<div id=\"root\"></div>",
                         '<div id="root"><title>not a real tag</title></div>')
    out = _strip_head_tags(body)
    assert "<title>not a real tag</title>" in out


def test_handle_is_escaped():
    # `handle` is user-controlled and reaches a raw HTML attribute. If this
    # ever fails, a handle is an injection vector on every share link.
    nasty = '"><script>alert(1)</script>'
    out = _tags(f"{nasty} is The Quiet Witness", "desc", "https://bibliome.app/s/tok")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    # The quote is what would break out of the attribute, so check it directly.
    assert "&quot;" in out
    assert 'content=""' not in out


def test_rendered_page_carries_the_new_tags_once():
    r = _render("Ada is The Quiet Witness — Bibliome", "She absorbs everything.",
                "https://bibliome.app/s/tok")
    # No frontend build on disk in tests, so this is the bare fallback — which
    # is itself worth asserting: a missing FRONTEND_DIR must not 500 a share.
    assert r.status_code == 200
    doc = r.body.decode()
    assert doc.count("<title>") == 1
    assert doc.count('property="og:title"') == 1
    assert "Ada is The Quiet Witness" in doc
    assert 'content="noindex, nofollow"' in doc
    assert r.headers["Cache-Control"] == "no-cache, must-revalidate"


def test_shell_path_injects_into_the_real_document(tmp_path, monkeypatch):
    import app.routers.og as og

    monkeypatch.setattr(og, "_index_html", lambda: SHELL)
    r = og._render("Ada is The Quiet Witness — Bibliome", "She absorbs everything.",
                   "https://bibliome.app/s/tok")
    doc = r.body.decode()
    assert doc.count("<title>") == 1
    assert "Landing title" not in doc
    assert "Landing twitter title" not in doc
    assert '/assets/index-abc123.js' in doc
    assert doc.index("og:title") < doc.index("</head>")
