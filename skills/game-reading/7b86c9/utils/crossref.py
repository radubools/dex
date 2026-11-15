"""Crossref metadata for DOI links, to check a reference whose doi.org page refuses scripts (403)."""
import json
import urllib.parse
import urllib.request

API = "https://api.crossref.org/works/"
UA = "dex-game-reading/1 (checking citation links)"


def doi_of(url: str) -> str | None:
    """The DOI in a https://doi.org/… link, or None for any other URL."""
    for host in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if url.startswith(host):
            return urllib.parse.unquote(url[len(host):])
    return None


def doi_metadata(doi: str, *, timeout: float = 20.0) -> dict:
    """{doi, title, authors (family names), year, container} for a DOI from Crossref; raises on a failed lookup."""
    req = urllib.request.Request(API + urllib.parse.quote(doi, safe="/()"), headers={"User-Agent": UA})
    m = json.load(urllib.request.urlopen(req, timeout=timeout))["message"]
    parts = (m.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "doi": doi,
        "title": " ".join((m.get("title") or [""])[0].split()),
        "authors": [a.get("family", "") for a in m.get("author") or []],
        "year": parts[0][0],
        "container": (m.get("container-title") or [""])[0],
    }
