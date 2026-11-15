"""arXiv metadata for arXiv links, to check that a reference's arxiv.org/abs/… URL is the work it names."""
import re
import urllib.parse
import urllib.request

API = "https://export.arxiv.org/api/query?id_list="
UA = "dex-game-reading/1 (checking citation links)"


def arxiv_id_of(url: str) -> str | None:
    """The arXiv id in a https://arxiv.org/abs/… (or /pdf/…) link, version kept, or None for any other URL."""
    m = re.match(r"https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?/?$", url)
    return m.group(1) if m else None


def arxiv_metadata(arxiv_id: str, *, timeout: float = 20.0) -> dict:
    """{id, title, authors (full names), year (of first version)} for an arXiv id from the arXiv API; raises on a failed lookup."""
    req = urllib.request.Request(API + urllib.parse.quote(arxiv_id, safe="/."), headers={"User-Agent": UA})
    xml = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    if "<entry>" not in xml:
        raise LookupError(f"arXiv has no entry for {arxiv_id}")
    entry = xml.split("<entry>", 1)[1]
    title = re.search(r"<title>(.*?)</title>", entry, re.S)
    year = re.search(r"<published>(\d{4})", entry)
    if not title or not year:
        raise LookupError(f"arXiv has no entry for {arxiv_id}")
    return {
        "id": arxiv_id,
        "title": " ".join(title.group(1).split()),
        "authors": [" ".join(n.split()) for n in re.findall(r"<name>(.*?)</name>", entry)],
        "year": int(year.group(1)),
    }
