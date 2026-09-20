"""Fetching a URL the operator named — mostly, refusing to.

The guard is the reason this module exists, so most of what is checked here is
what it will *not* do. A dex box sits on a tailnet beside its own database;
"fetch this URL" must not become a way to ask it to read that.
"""

from __future__ import annotations

import pytest

from dex import web_sources as web


def _resolves_to(monkeypatch, address: str) -> None:
    """Pin DNS, so the guard is tested rather than the internet."""
    monkeypatch.setattr(
        web.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (address, 0))],
    )


@pytest.mark.parametrize(
    "address, because",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "private"),
        ("192.168.1.10", "private"),
        ("172.16.4.4", "private"),
        # The cloud metadata endpoint: the single most valuable thing an SSRF
        # reaches, and link-local is what rules it out.
        ("169.254.169.254", "link-local"),
        ("0.0.0.0", "non-routable"),
        ("224.0.0.1", "non-routable"),
    ],
)
def test_it_refuses_an_address_that_is_not_public(monkeypatch, address, because):
    _resolves_to(monkeypatch, address)
    with pytest.raises(web.Blocked) as caught:
        web.check("https://looks-fine.example.com/data")
    assert because in str(caught.value)


def test_it_allows_a_public_address(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert web.check("https://example.com/a") == "https://example.com/a"


def test_a_bare_hostname_is_treated_as_https(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert web.check("example.com").startswith("https://")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://h/1"])
def test_it_refuses_a_scheme_it_does_not_fetch(url):
    with pytest.raises(web.Blocked, match="not a scheme"):
        web.check(url)


def test_a_name_that_does_not_resolve_is_refused_not_attempted(monkeypatch):
    import socket as real_socket

    def boom(*_a, **_k):
        raise real_socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(web.socket, "getaddrinfo", boom)
    with pytest.raises(web.Blocked, match="does not resolve"):
        web.check("https://nope.invalid/")


def test_every_resolved_address_has_to_pass(monkeypatch):
    """One public A record does not excuse a private one beside it."""
    monkeypatch.setattr(
        web.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0)),
                         (2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(web.Blocked, match="loopback"):
        web.check("https://split-horizon.example.com/")


# ------------------------------------------------------------------ redirects


class _Response:
    def __init__(self, status: int, location: str = "", body: bytes = b""):
        self.status_code = status
        self.headers = {"location": location, "content-type": "text/html"}
        self.content = body
        self.is_redirect = 300 <= status < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected {self.status_code}")


class _Client:
    """An httpx stand-in that answers from a script of responses."""

    def __init__(self, script):
        self.script = script
        self.asked: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str):
        self.asked.append(url)
        return self.script.pop(0)


@pytest.fixture
def httpx_script(monkeypatch):
    """Install a scripted client and hand back the list to fill in."""
    script: list[_Response] = []
    holder: dict[str, _Client] = {}

    class _Module:
        @staticmethod
        def AsyncClient(**_kwargs):
            holder["client"] = _Client(script)
            return holder["client"]

    import sys

    monkeypatch.setitem(sys.modules, "httpx", _Module)
    return script, holder


async def test_a_redirect_to_a_private_address_is_refused(monkeypatch, httpx_script):
    """The attack this exists for: a public name that 302s inward.

    A client with `follow_redirects=True` walks straight into it, which is why
    `fetch` follows them itself and re-checks every hop.
    """
    script, _ = httpx_script
    script.append(_Response(302, location="http://169.254.169.254/latest/meta-data/"))

    hosts = {"safe.example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"}
    monkeypatch.setattr(
        web.socket, "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", (hosts[host], 0))],
    )

    with pytest.raises(web.Blocked, match="link-local"):
        await web.fetch("https://safe.example.com/start")


async def test_a_redirect_to_another_public_address_is_followed(monkeypatch, httpx_script):
    script, holder = httpx_script
    script.append(_Response(302, location="https://elsewhere.example.com/final"))
    script.append(_Response(200, body=b"<h1>Arrived</h1>"))
    _resolves_to(monkeypatch, "93.184.216.34")

    got = await web.fetch("https://safe.example.com/start")
    assert got.url == "https://elsewhere.example.com/final"
    assert got.body == b"<h1>Arrived</h1>"


async def test_a_redirect_loop_gives_up(monkeypatch, httpx_script):
    script, _ = httpx_script
    script.extend(
        _Response(302, location="https://example.com/round") for _ in range(web.MAX_HOPS + 2)
    )
    _resolves_to(monkeypatch, "93.184.216.34")
    with pytest.raises(web.Blocked, match="redirected more than"):
        await web.fetch("https://example.com/round")


async def test_a_body_is_capped(monkeypatch, httpx_script):
    """A survey wants a heading tree; anything bigger is a download."""
    script, _ = httpx_script
    script.append(_Response(200, body=b"x" * (web.MAX_BYTES + 5000)))
    _resolves_to(monkeypatch, "93.184.216.34")
    got = await web.fetch("https://example.com/huge")
    assert len(got.body) == web.MAX_BYTES


# -------------------------------------------------------------- the outline


PAGE = b"""
<title>Docs home</title>
<h1>Welcome</h1><h2>Getting started</h2>
<a href="/guide">Guide</a><a href="/api">API</a>
<a href="https://elsewhere.example/off">Off site</a>
"""


async def test_the_page_that_was_named_comes_first(monkeypatch, httpx_script):
    """The link the operator gave is the material, not just a way to find it."""
    script, _ = httpx_script
    script.append(_Response(200, body=PAGE))          # the page
    script.append(_Response(200, body=b""))           # robots.txt
    script.append(_Response(200, body=b""))           # sitemap.xml, empty
    _resolves_to(monkeypatch, "93.184.216.34")

    outline = await web.outline_url("https://docs.example.com/")
    assert outline.nodes[0].title == "Docs home"
    assert outline.nodes[0].anchor.url == "https://docs.example.com/"
    assert outline.nodes[0].depth == 0


async def test_the_pages_headings_nest_under_it(monkeypatch, httpx_script):
    """An `h1` is a section of the page, not a page beside it."""
    script, _ = httpx_script
    script.extend([_Response(200, body=PAGE), _Response(200), _Response(200)])
    _resolves_to(monkeypatch, "93.184.216.34")

    nodes = (await web.outline_url("https://docs.example.com/")).nodes
    headings = [n for n in nodes if n.title in {"Welcome", "Getting started"}]
    assert [n.depth for n in headings] == [1, 2]


async def test_a_landing_page_with_no_headings_still_appears(monkeypatch, httpx_script):
    """The case that lost the page entirely: a hub of links and no `h1`."""
    script, _ = httpx_script
    script.append(_Response(200, body=b'<title>Hub</title><a href="/a">A</a>'))
    script.extend([_Response(200), _Response(200)])
    _resolves_to(monkeypatch, "93.184.216.34")

    nodes = (await web.outline_url("https://hub.example.com/")).nodes
    assert nodes[0].title == "Hub"
    assert nodes[0].anchor.url == "https://hub.example.com/"


async def test_the_named_page_is_not_repeated_from_the_sitemap(monkeypatch, httpx_script):
    """A site's sitemap lists its own landing page; it must not appear twice."""
    script, _ = httpx_script
    script.append(_Response(200, body=PAGE))
    script.append(_Response(200, body=b""))
    script.append(_Response(200, body=(
        b"<urlset>"
        b"<url><loc>https://docs.example.com/</loc></url>"
        b"<url><loc>https://docs.example.com/guide</loc></url>"
        b"</urlset>"
    )))
    _resolves_to(monkeypatch, "93.184.216.34")

    nodes = (await web.outline_url("https://docs.example.com/")).nodes
    # Page-level entries only: a heading with no `id` can only point at the
    # page it is in, so it shares the URL without being a second listing of it.
    named = [
        n for n in nodes
        if n.depth == 0 and (n.anchor.url or "").rstrip("/") == "https://docs.example.com"
    ]
    assert len(named) == 1
    assert nodes[0] is named[0]
    assert any(n.anchor.url == "https://docs.example.com/guide" for n in nodes)


async def test_discovered_links_follow_the_page(monkeypatch, httpx_script):
    script, _ = httpx_script
    script.extend([_Response(200, body=PAGE), _Response(200), _Response(200)])
    _resolves_to(monkeypatch, "93.184.216.34")

    outline = await web.outline_url("https://docs.example.com/")
    urls = [n.anchor.url for n in outline.nodes]
    assert urls[0] == "https://docs.example.com/"
    assert "https://docs.example.com/guide" in urls
    # Another origin is somebody else's site, not this material.
    assert not any("elsewhere.example" in (u or "") for u in urls)


async def test_a_non_html_url_is_still_one_piece_of_material(monkeypatch, httpx_script):
    """A PDF at a URL has no outline, but it is still a thing to plan."""
    script, _ = httpx_script
    response = _Response(200, body=b"%PDF-1.7")
    response.headers["content-type"] = "application/pdf"
    script.append(response)
    _resolves_to(monkeypatch, "93.184.216.34")

    outline = await web.outline_url("https://example.com/spec.pdf")
    assert len(outline.nodes) == 1
    assert outline.nodes[0].anchor.url == "https://example.com/spec.pdf"
