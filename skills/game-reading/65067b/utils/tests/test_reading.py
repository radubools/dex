"""Checks for game-reading: write, validate, figures, links and install."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils import reading as rd  # noqa: E402

WORK = {"title": "The Elements of Statistical Learning", "authors": ["Trevor Hastie", "Robert Tibshirani", "Jerome Friedman"],
        "year": 2009, "edition": "2nd ed.", "url": "https://hastie.su.domains/ElemStatLearn/"}
REFS = {"hoerl-kennard-1970": {"text": "Hoerl, A. E. and Kennard, R. W. (1970). Ridge regression: biased estimation for nonorthogonal problems. Technometrics 12(1), 55–67.",
                               "url": "https://doi.org/10.1080/00401706.1970.10488634"}}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def passages(md="Ridge regression shrinks the coefficients [Hoerl and Kennard, 1970](#ref:hoerl-kennard-1970):\n\n"
                "$$\\hat\\beta^{\\rm ridge}=\\arg\\min_\\beta \\sum_i (y_i-\\beta_0-\\sum_j x_{ij}\\beta_j)^2+\\lambda\\sum_j\\beta_j^2 \\qquad (3.41)$$\n\n"
                "with $\\lambda\\ge 0$ the complexity parameter.\n\n![Figure 3.8: profiles of ridge coefficients.](figures/fig-3-8.png)"):
    return [{"id": "ridge-penalty", "title": "3.4.1 Ridge regression", "where": "Ch. 3, p. 63", "markdown": md, "refs": []}]


def make(tmp_path, md=None):
    pkg = tmp_path / "reading-eslr-ridge"
    (pkg / "figures").mkdir(parents=True)
    (pkg / "figures" / "fig-3-8.png").write_bytes(PNG)
    man = rd.write_reading(pkg, name="eslr-ridge", story="story/eslr-ridge", work=WORK,
                           passages=passages() if md is None else passages(md), references=REFS)
    return pkg, man


def test_write_and_validate(tmp_path):
    pkg, man = make(tmp_path)
    assert man["kind"] == "reading" and man["files"] == ["reading.json", "figures/fig-3-8.png"]
    assert {"type:reading", "story:eslr-ridge"} <= set(man["tags"])
    rep = rd.validate_reading(pkg)
    assert rep["ok"], rep
    assert rd.passage_ids(pkg) == ["ridge-penalty"]
    assert "3.4.1 Ridge regression" in rd.outline(pkg) and "doi.org" in rd.outline(pkg)


def test_refuses_what_the_game_could_not_show(tmp_path):
    with pytest.raises(ValueError, match="not in references"):
        make(tmp_path, md="See [them](#ref:nobody-1900).")
    shutil.rmtree(tmp_path / "reading-eslr-ridge")
    with pytest.raises(ValueError, match="is not closed"):
        make(tmp_path, md="An open $$x formula.")
    shutil.rmtree(tmp_path / "reading-eslr-ridge")
    with pytest.raises(ValueError, match="must be http"):
        make(tmp_path, md="[x](javascript:alert(1))")
    shutil.rmtree(tmp_path / "reading-eslr-ridge")
    with pytest.raises(ValueError, match="is missing"):
        make(tmp_path, md="![a](figures/none.png)")


def test_math_and_code_and_money(tmp_path):
    assert rd.math_problems("costs $5 and $6, and `a$b` is code") == []
    assert rd.math_problems("$x$ and $$y$$") == []
    assert rd.math_problems("$x and then") != []


def test_a_changed_reading_json_is_caught(tmp_path):
    pkg, _ = make(tmp_path)
    body = json.loads((pkg / "reading.json").read_text())
    body["passages"][0]["title"] = "edited"
    (pkg / "reading.json").write_text(json.dumps(body))
    assert any("changed since" in e for e in rd.validate_reading(pkg)["errors"])


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler")
def test_a_figure_from_a_pdf_page(tmp_path):
    from PIL import Image, ImageDraw

    page = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(page).rectangle((100, 200, 300, 400), fill="black")
    pdf = tmp_path / "book.pdf"
    page.save(pdf)
    out = rd.pdf_figure(pdf, 1, tmp_path / "figures" / "fig.png", crop=(0, 0, 1, 0.75), dpi=72)
    img = Image.open(out)
    # Trimmed to the black box and its small margin.
    assert 200 <= img.width <= 230 and 200 <= img.height <= 230
    assert "" == rd.pdf_text(pdf, 1).strip()


def test_install_into_a_game(tmp_path):
    pkg, man = make(tmp_path)
    game = tmp_path / "game"
    (game / "public" / "assets").mkdir(parents=True)
    try:
        entry = rd.export_reading(pkg, game)
    except ImportError:
        pytest.skip("needs game-mesh's catalog for the index")
    assert entry["base"] == "assets/reading/eslr-ridge/"
    assert set(entry["hashes"]) == {"reading.json", "figures/fig-3-8.png"}
    assert (game / "public/assets/reading/eslr-ridge/figures/fig-3-8.png").is_file()
    assert "reading/eslr-ridge" in json.loads((game / "public/assets/index.json").read_text())["assets"]
