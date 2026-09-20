---
name: traduceri-sources
description: Getting readable text out of a source — CJK-aware paragraph chunking, and pulling the article out of a MediaWiki page.
---

# Sources

Turning something the operator supplied into text worth translating.

| Module | For |
|---|---|
| `utils/chunking.py` | Cut extracted text into paragraph chunks, CJK-aware |
| `utils/mediawiki.py` | Pull the readable article out of a MediaWiki page as ordered blocks |

Both are mechanism: each takes text and returns a list. Neither fetches
anything, so both can be run over a fragment you are holding.

`mediawiki` exists because **a rendered wiki page is mostly not the article** —
maintenance banners, navigation, infoboxes, references. Translating the page as
scraped translates all of that too.

CJK chunking is separate because a paragraph of Chinese has no spaces to split
on, and a splitter written for English produces one chunk per document.

Needs `bs4`.

## What it will not do

It does not know what a bitext is. The format and its checks are
`traduceri-bitext`.
