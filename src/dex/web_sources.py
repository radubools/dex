"""Fetching a URL the operator named, safely enough to do it server-side.

A survey that can read an uploaded PDF should be able to read a page the
operator linked, and the outline it produces is the same shape either way. The
difference is that this reaches out from the server, which is a capability
worth being careful with: the dex box sits on a tailnet beside a Postgres and
whatever else the machine runs, and "fetch this URL" is the classic way to ask
a server to read something on its own network on your behalf.

So every hop is checked, not just the first. The guard resolves the hostname
and refuses loopback, private, link-local, multicast and reserved addresses,
and it re-runs on each redirect rather than letting the HTTP client follow them
— a public hostname that 302s to 169.254.169.254 is the whole attack, and a
client with `follow_redirects=True` walks straight into it.

What remains is DNS rebinding: a name that answers with a public address when
it is checked and a private one when it is connected to. Closing that needs the
socket pinned to the address that was checked, which httpx does not expose;
given the operator is naming these URLs themselves, and dex is not a public
service, the check-then-connect window is a residual rather than the hole.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from .sources import MAX_DEPTH, Anchor, Node, Outline

log = logging.getLogger("dex.web")

#: Most bytes read from one URL. A survey wants a heading tree, and anything
#: that needs more than this is a download rather than a page.
MAX_BYTES = 4 * 1024 * 1024
#: Redirects followed before giving up, each one re-checked.
MAX_HOPS = 5
#: Pages fetched for one site survey, including the first.
MAX_PAGES = 25
TIMEOUT = 15.0

_UA = "dex/0.1 (+source survey)"


class Blocked(Exception):
    """A URL that will not be fetched, and why."""


def _check_host(host: str) -> None:
    """Refuse a hostname that resolves anywhere but the public internet."""
    if not host:
        raise Blocked("no host in the URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise Blocked(f"{host} does not resolve ({exc.strerror or exc})") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise Blocked(f"{host} does not resolve")
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%")[0])
        # `is_global` at the bottom is the check that is right by
        # construction; the named ones above it exist so a refusal can say
        # which rule was broken. Most specific first. `ipaddress` counts link-local, loopback and the
        # unspecified address as private too, so testing `is_private` earlier
        # would refuse 169.254.169.254 — the cloud metadata endpoint, and the
        # single most valuable thing an SSRF reaches — while reporting it as
        # merely "private". The address is blocked either way; what changes is
        # whether whoever reads the refusal can tell what was attempted.
        if ip.is_loopback:
            raise Blocked(f"{host} resolves to the loopback address {ip}")
        if ip.is_link_local:
            raise Blocked(f"{host} resolves to the link-local address {ip}")
        if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise Blocked(f"{host} resolves to the non-routable address {ip}")
        if ip.is_private:
            raise Blocked(f"{host} resolves to the private address {ip}")
        if not ip.is_global:
            raise Blocked(f"{host} resolves to {ip}, which is not a public address")


def check(url: str) -> str:
    """Validate one URL and return it normalised, or raise `Blocked`."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in {"http", "https"}:
        raise Blocked(f"{parsed.scheme or 'that'} is not a scheme dex will fetch")
    _check_host(parsed.hostname or "")
    return parsed.geturl()


@dataclass
class Fetched:
    url: str
    body: bytes
    content_type: str


async def fetch(url: str) -> Fetched:
    """One URL, following redirects by hand so every hop is checked."""
    import httpx

    current = check(url)
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=TIMEOUT, headers={"user-agent": _UA}
    ) as client:
        for _ in range(MAX_HOPS):
            response = await client.get(current)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise Blocked(f"{current} redirected with no destination")
                # Re-checked, which is the entire point of doing this by hand.
                current = check(urljoin(current, location))
                continue

            response.raise_for_status()
            body = response.content[:MAX_BYTES]
            return Fetched(
                url=current,
                body=body,
                content_type=response.headers.get("content-type", ""),
            )
    raise Blocked(f"{url} redirected more than {MAX_HOPS} times")


# ------------------------------------------------------------------- sitemaps


_LOC = re.compile(rb"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


async def sitemap(origin: str, limit: int = 500) -> list[str]:
    """URLs a site lists for itself, from robots.txt and `/sitemap.xml`.

    A sitemap index is followed one level: the top-level file of a large site
    is a list of other sitemaps, and stopping there would return no pages at
    all.
    """
    parsed = urlparse(check(origin))
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []

    try:
        robots = await fetch(f"{root}/robots.txt")
        for line in robots.body.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())
    except Exception:
        log.debug("no robots.txt for %s", root)
    candidates.append(f"{root}/sitemap.xml")

    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates[:5]:
        try:
            doc = await fetch(candidate)
        except Exception:
            continue
        found = [m.decode("utf-8", "replace") for m in _LOC.findall(doc.body)]
        is_index = b"<sitemapindex" in doc.body[:2000].lower()
        if is_index:
            for child in found[:5]:
                try:
                    sub = await fetch(child)
                except Exception:
                    continue
                found.extend(
                    m.decode("utf-8", "replace") for m in _LOC.findall(sub.body)
                )
        for url in found:
            if url not in seen:
                seen.add(url)
                urls.append(url)
            if len(urls) >= limit:
                return urls
    return urls


# ------------------------------------------------------------------- outlines


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _page_node(url: str, title: str = "") -> Node:
    """The page itself, as the first entry in its own outline.

    Without this the outline of a URL is a list of *other* pages. The `h1`s
    inside it are sections rather than a unit of work, the sitemap entries are
    every page but this one, and a landing page with no headings contributes
    nothing at all — so a survey plans the children and silently drops the page
    the operator actually named. It goes first because it is the thing that was
    asked about; everything else was discovered from it.
    """
    return Node(
        title=title or url,
        depth=0,
        anchor=Anchor(source=url, url=url, label=title or url),
    )


def _others(urls: list[str], skip: str) -> list[Node]:
    """Discovered pages, as peers of the one that was named.

    `skip` drops the page itself: a site's sitemap almost always lists its own
    landing page, and without this it appears twice — once at the top as the
    page, and again somewhere down the list as a URL among hundreds.
    """
    return [
        Node(title=u, depth=0, anchor=Anchor(source=u, url=u, label=u))
        for u in urls if u.rstrip("/") != skip.rstrip("/")
    ]


async def outline_url(url: str, crawl: bool = True) -> Outline:
    """The structure of a page, and of the pages it links to.

    Prefers a sitemap when the site publishes one — that is the site's own
    account of what it contains, and it costs one request instead of twenty.
    Falls back to the page's heading tree plus its same-origin links. Either
    way the page that was named is the first entry.
    """
    from bs4 import BeautifulSoup

    from .sources import _outline_html

    first = await fetch(url)
    if "html" not in first.content_type and "xml" not in first.content_type:
        # A PDF or a dataset at a URL. Nothing to outline, but it is still the
        # material: listed as one node so it can be planned as one piece.
        return Outline(
            source=first.url, kind="url", nodes=[_page_node(first.url)],
            facts={"contentType": first.content_type or "unknown",
                   "bytes": len(first.body)},
            note="not a document dex can outline; it is one piece of material",
        )

    page = _outline_html(first.url, first.body, url=first.url)
    page.source = first.url
    page.kind = "url"
    title = str(page.facts.get("title") or "")
    # The headings belong *to* the page, so they nest under it rather than
    # standing beside the other pages as though they were pages themselves.
    for node in page.nodes:
        node.depth = min(node.depth + 1, MAX_DEPTH)
        node.anchor = Anchor(
            source=first.url, url=node.anchor.url or first.url,
            heading=node.anchor.heading, label=node.title[:60],
        )
    page.nodes.insert(0, _page_node(first.url, title))

    if not crawl:
        return page

    listed = await sitemap(first.url)
    if listed:
        others = _others(listed[:MAX_PAGES + 1], first.url)[:MAX_PAGES]
        page.facts["sitemap"] = len(listed)
        page.nodes.extend(others)
        page.note = (
            f"this page first, then the site's own sitemap "
            f"({len(listed)} URLs, {len(others)} listed)"
        )
        return page

    soup = BeautifulSoup(first.body, "html.parser")
    links: list[str] = []
    seen = {first.url}
    for tag in soup.find_all("a", href=True):
        target = urljoin(first.url, str(tag["href"])).split("#")[0]
        if target not in seen and _same_origin(first.url, target):
            seen.add(target)
            links.append(target)
        if len(links) >= MAX_PAGES:
            break
    if links:
        page.facts["links"] = len(links)
        page.nodes.extend(_others(links, first.url))
        page.note = "this page first, then the same-origin links on it"
    return page


async def text_of(url: str) -> str:
    """A page's readable text, for searching."""
    from .sources import _html_text

    got = await fetch(url)
    if "html" in got.content_type:
        return _html_text(got.body)
    return got.body.decode("utf-8", "replace")
