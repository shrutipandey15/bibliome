"""Server-rendered <head> for share links.

The problem this solves: nginx serves one static index.html for every path, so
a shared /s/:token link carried the landing page's og:title and og:description.
Every DNA card anyone sent to a friend previewed as "Bibliome — The Emotional
Fingerprint of Your Reading Life" instead of naming the archetype. The frontend
has a useHead hook, but social scrapers (Facebook, Twitter/X, Slack, iMessage,
WhatsApp, LinkedIn) do not execute JavaScript — they read the raw HTML — so the
tags have to exist before React runs. See the KNOWN LIMIT note in the
frontend's src/hooks/useHead.js, which this is the other half of.

A route, not an ASGI middleware. Middleware would run on every request to
inspect a path only these URLs use; this fires only where it applies and is
ordinary testable code. nginx routes /s/ here (location ^~ /s/ in
deploy/bibliome.nginx.conf).

It also serves the card's own preview image, /s/:token/card.jpg?v=hash
(card_preview): a 1200×630 JPEG of the reader's card, rendered before the first
crawler arrives and served as stored bytes. The static /og-image.png is now only
for links that are invalid, revoked, or whose reader has no card yet.
"""

import html
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.rate_limit import RateLimiter
from app.routers.public import public_limiter
from app.services import dna_card
from app.services.card_counts import bump, crawler_name
from app.services.card_preview import ensure_preview, stored_preview
from app.services.dna_service import card_payload
from app.services.visibility import resolve_share_token

logger = logging.getLogger("bibliome.og")

router = APIRouter(tags=["og"])

SITE = "https://bibliome.app"
GENERIC_IMAGE = f"{SITE}/og-image.png"
GENERIC_ALT = "Bibliome — the emotional fingerprint of your reading life"

# The image is stored bytes, so it can take a crawler burst after a share (one
# WhatsApp group fetches it from several Meta addresses at once) more generously
# than the page does.
image_limiter = RateLimiter(max_requests=120, window_seconds=60, prefix="og-image")

# Served when the built frontend isn't on disk — a dev box, or a deploy that
# half-finished. A scraper still gets correct tags and a human still gets the
# link, which beats a 500 on someone's shared card.
_BARE = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{head}</head>'
    '<body><p><a href="{url}">Open this reading DNA card on Bibliome</a></p></body></html>'
)


def _index_html() -> str | None:
    """The built SPA shell, re-read when it changes on disk.

    Cached on the function so a burst of scraper hits is one file read, but
    keyed on mtime — a deploy rewrites index.html with new asset hashes, and
    serving the previous one would boot the SPA against chunks that have been
    deleted (the same failure the nginx no-cache rule on index.html exists to
    prevent).
    """
    path = Path(get_settings().FRONTEND_DIR) / "index.html"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if getattr(_index_html, "_mtime", None) != mtime:
        try:
            _index_html._body = path.read_text(encoding="utf-8")
        except OSError:
            return None
        _index_html._mtime = mtime
    return _index_html._body


def _tags(title: str, description: str, url: str, image: str = GENERIC_IMAGE,
          alt: str = GENERIC_ALT, image_type: str = "image/png") -> str:
    """The head block. Everything interpolated is escaped — `handle` is
    user-controlled, and this is the one place it reaches raw HTML."""
    t, d, u, i, a = (html.escape(x, quote=True) for x in (title, description, url, image, alt))
    return (
        f"<title>{t}</title>"
        f'<meta name="description" content="{d}" />'
        # A share token is a capability link, not a page. robots.txt already
        # disallows /s/, but a scraper that ignores robots.txt still reads this.
        f'<meta name="robots" content="noindex, nofollow" />'
        f'<link rel="canonical" href="{u}" />'
        f'<meta property="og:type" content="profile" />'
        f'<meta property="og:url" content="{u}" />'
        f'<meta property="og:title" content="{t}" />'
        f'<meta property="og:description" content="{d}" />'
        f'<meta property="og:site_name" content="Bibliome" />'
        f'<meta property="og:image" content="{i}" />'
        f'<meta property="og:image:type" content="{image_type}" />'
        f'<meta property="og:image:width" content="1200" />'
        f'<meta property="og:image:height" content="630" />'
        f'<meta property="og:image:alt" content="{a}" />'
        f'<meta name="twitter:card" content="summary_large_image" />'
        f'<meta name="twitter:title" content="{t}" />'
        f'<meta name="twitter:description" content="{d}" />'
        f'<meta name="twitter:image" content="{i}" />'
        f'<meta name="twitter:image:alt" content="{a}" />'
    )


# The tags the static shell already carries, which have to come OUT before the
# per-card ones go in or the scraper sees two of each and picks whichever it
# likes. Kept as one ordered list so index.html changing is one edit here.
_STRIP = (
    ("<title>", "</title>"),
    ('<meta name="description"', ">"),
    ('<meta name="robots"', ">"),
    ('<link rel="canonical"', ">"),
    ('<meta property="og:', ">"),
    ('<meta name="twitter:', ">"),
    # The landing page's WebApplication and FAQPage blocks. A share card is
    # noindex and is not the product page or an FAQ, so it carries no
    # structured data at all rather than somebody else's. Matched on the typed
    # opening tag so the inline theme script (plain <script>) is left alone.
    ('<script type="application/ld+json">', "</script>"),
)


def _strip_head_tags(doc: str) -> str:
    head_end = doc.find("</head>")
    if head_end == -1:
        return doc
    head, rest = doc[:head_end], doc[head_end:]
    for opener, closer in _STRIP:
        while True:
            i = head.find(opener)
            if i == -1:
                break
            j = head.find(closer, i + len(opener))
            if j == -1:
                break
            head = head[:i] + head[j + len(closer):]
    return head + rest


def _render(title: str, description: str, url: str, **image) -> HTMLResponse:
    tags = _tags(title, description, url, **image)
    shell = _index_html()
    if shell is None:
        logger.warning("FRONTEND_DIR has no index.html; serving the bare shell")
        body = _BARE.format(head=tags, url=html.escape(url, quote=True))
    else:
        body = _strip_head_tags(shell).replace("</head>", tags + "</head>", 1)
    return HTMLResponse(
        body,
        # Same reasoning as nginx's rule on index.html: this embeds the current
        # asset hashes, so it must be revalidated after every deploy. The
        # scrapers cache the tags themselves anyway.
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/s/{token}", response_class=HTMLResponse, include_in_schema=False)
async def shared_card_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The SPA shell for /s/:token, with the card's own OG tags in the head.

    Always 200 with a bootable page, never a 404: an invalid or revoked token
    still has to load the app so it can show its own "link invalid or expired"
    state. It just gets the generic tags, which is also the right answer — a
    dead link should not preview as somebody's archetype.
    """
    await public_limiter.check(request)
    url = f"{SITE}/s/{token}"
    # A person opening a shared card, counted into today's total. Crawlers are
    # counted where they matter, on the image they came for.
    if crawler_name(request.headers.get("user-agent")) is None:
        await bump(db, "visit", "human")

    generic = (
        "Bibliome — the emotional fingerprint of your reading life",
        "Someone shared their Reading DNA with you. Bibliome tracks the emotions books "
        "make you feel, not stars or page counts.",
    )

    user = await resolve_share_token(db, token)
    if not user:
        return _render(*generic, url)

    # The card as strangers see it: the reader's switches applied.
    card = card_payload(user)
    if card is None:
        return _render(*generic, url)

    a = card["archetype"]
    name = dna_card.display_name(a.get("name") or "reading archetype")
    who = f"@{user.handle}" if user.handle else "This reader"
    title = f"{who} reads like {a.get('article') or 'a'} {name} — Bibliome"
    # The archetype's own line, first person, as on the card itself.
    line = a.get("share_line") or a.get("description") or ""
    desc = f"“{line}” What does your reading say about you?" if line else \
        "What does your reading say about you?"

    # Make (or confirm) the stored preview BEFORE handing out its URL, so a
    # crawler that follows these tags always finds the image.
    v = await ensure_preview(db, user, card)
    if v is None:
        return _render(title, desc, url)
    await db.commit()
    return _render(title, desc, url,
                   image=f"{url}/card.jpg?v={v}",
                   alt=dna_card.preview_alt(card, user.handle),
                   image_type="image/jpeg")


@router.get("/s/{token}/card.jpg", include_in_schema=False)
async def shared_card_image(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The card's link-preview image. Stored bytes, never a render on the hot
    path — unless the stored one is missing or stale, which ensure_preview on
    the page above makes rare.

    A dead link redirects to the generic image: the preview a crawler cached
    for a link that has since been turned off should not keep showing the card.
    """
    await image_limiter.check(request)
    crawler = crawler_name(request.headers.get("user-agent"))
    if crawler:
        await bump(db, "preview", crawler)

    user = await resolve_share_token(db, token)
    card = card_payload(user) if user else None
    if card is None:
        return RedirectResponse(GENERIC_IMAGE, status_code=302)

    want = dna_card.preview_hash(dna_card.preview_spec(card, user.handle))
    have = await stored_preview(db, user.id)
    if have is not None and have.hash == want:
        jpeg = have.jpeg
    else:
        v = await ensure_preview(db, user, card)
        have = await stored_preview(db, user.id) if v else None
        if have is None:
            return RedirectResponse(GENERIC_IMAGE, status_code=302)
        jpeg = have.jpeg

    # The URL carries the hash, so a URL whose hash matches can be cached for
    # good; an old hash still gets the current card, briefly cached.
    current = request.query_params.get("v") == want
    return Response(
        jpeg, media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable" if current
            else "public, max-age=3600",
            "X-Robots-Tag": "noindex",
        },
    )
