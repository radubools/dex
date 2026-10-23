"""Checks for edil-corpus that need no database: selection, offset repair, chunk budgets, flags.

    /Users/raduparadovschi/dex/.venv/bin/python skills/edil-corpus/<version>/test/test_edil_corpus.py

Run by path (`test` is a standard-library package name). The selection checks
read the real eDIL export, so the external drive must be mounted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import chunking, edil_sources, pdf_text  # noqa: E402


def test_unshift_both_directions():
    # -29: every space a U+0003, "Permits Total" as "3HUPLWV 7RWDO".
    fixed, did = pdf_text.unshift("3HUPLWV\x03 7RWDO\x032XWVLGH\x03&RQWUDFW\x03DQG\x03WKH\x03EXGJHW")
    assert did and fixed.startswith("Permits  Total Outside Contract and the budget"), fixed
    # +3: "Wkh#surmhfw" for "The project".
    fixed, did = pdf_text.unshift("Wkh#surmhfw#lv#zlwklq#wkh#exgjhw#dqg#wkh#frqwudfw")
    assert did and fixed.startswith("The project is within the budget"), fixed
    # Clean text is left alone.
    clean = "The project is within the budget and the contract is signed."
    assert pdf_text.unshift(clean) == (clean, False)


def test_chunks_respect_budget_and_pages():
    count = lambda t: len(t.split())  # noqa: E731 - a word counter keeps this offline
    pages = [{"page": i, "text": ("The contractor has not priced the work. " * 30), "is_text": True}
             for i in (1, 2, 3)]
    chunks = chunking.split_pages(pages, budget=50, count=count)
    assert chunks and all(c["tokens"] <= 50 for c in chunks)
    assert chunks[0]["pages"] == [1] and chunks[-1]["pages"] == [3]
    assert [c["ord"] for c in chunks] == list(range(len(chunks)))


def test_flags():
    chunks = [{"text": "The lender has not approved the change order and the budget is short.",
               "text_page_share": 1.0},
              {"text": "$1,234 $5,678 12.5% 9,000 $44,100 (3,200) 77.1%", "text_page_share": 1.0}]
    chunking.flag(chunks)
    assert chunks[0]["usable"] and not chunks[1]["usable"]


def test_select_is_deterministic_and_spread():
    if not edil_sources.DATASET.exists():
        print("skip: the eDIL export is not mounted")
        return
    all_projects = edil_sources.projects()
    a = edil_sources.select(all_projects, 30)
    assert a == edil_sources.select(all_projects, 30)
    assert len({p.building_type for p in a}) == 30, "30 projects should cover 30 building types"
    nxt = edil_sources.select(all_projects, 30, skip=30)
    assert not {p.key for p in a} & {p.key for p in nxt}
    one = edil_sources.select(all_projects, folders=[a[0].folder, a[1].folder[:12]])
    assert a[0].key in {p.key for p in one} and a[1].key in {p.key for p in one}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
