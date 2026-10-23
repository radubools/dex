"""Write the next round's criteria with a local LLM served by Ollama.

The generator sees what the last round learned — which criteria were kept and
how far they reached, which were dropped and why — plus passages no kept
criterion fires on yet, and proposes new yes/no criteria and revisions:
narrower wordings for pervasive ones, broader for rare ones, sharper for
incoherent ones. It returns SystemOne `noul` questions ready for `clef.ask`.

    from utils import generator
    proposals = generator.propose(kept, dropped, uncovered_texts, n=20)
    # [{"question": {"type": "noul", "instructions": "Does this passage say ...?"},
    #   "why": "...", "revises": "name_or_None"}]

Runs fully local: `qwen3.8:27b-mlx` by default (~10 tokens/s of output on the
M4 Max, so 20 criteria take a minute or two), with thinking off and the output
constrained to a JSON schema. `CRITERIA_GENERATOR` overrides the model.

Wordings follow "Does this passage say ...?" in the documents' own vocabulary —
on the Laya sweeps over this corpus that form beat declarative rewordings on six
of seven criteria, and it reads the same to Clef.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
MODEL = os.environ.get("CRITERIA_GENERATOR", "qwen3.8:27b-mlx")

SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "revises": {"type": ["string", "null"]},
                },
                "required": ["question", "why", "revises"],
            },
        }
    },
    "required": ["criteria"],
}

#: The risk areas criteria are written for — the ones that, in practice, turn into
#: change orders, overruns and delays on lender-financed construction. The
#: generator is asked to cover these, not the incidental facts of one report.
RISKS = """\
COST AND BUDGET: thin or missing contingency; allowances instead of priced scope; unpriced or
excluded scope; budget below the reviewer's estimate; soft-cost or hard-cost line items missing;
escalation not carried; GMP qualifications and exclusions; unit prices without quantities.
DESIGN AND DOCUMENTS: incomplete or preliminary drawings (schematic / design development);
plans not permitted or not approved; deferred submittals (fire, structural, MEP); missing
specifications, geotechnical or civil documents; conflicting or uncoordinated disciplines;
design-build scope carried by the contractor.
SITE AND GROUND: unknown soil conditions; groundwater; contamination or hazardous materials;
demolition of existing structures; rock, fill or debris; utilities to relocate or extend;
off-site improvements required by the city; existing-building conditions on renovations.
CONTRACT AND PROCUREMENT: cost-plus or open-ended contracts; no GMP; no liquidated damages;
retention terms; contractor not yet selected or bids not received; owner-furnished items;
separate contracts outside the GC's scope; long-lead equipment.
SCHEDULE: aggressive or missing schedule; start-date or completion-date risk; phasing;
weather or seasonal exposure; permit timing; sequencing with occupied buildings.
REGULATORY AND ENTITLEMENT: permits pending; conditions of approval; code upgrades triggered
by renovation; accessibility (ADA) upgrades; environmental review or mitigation required.
PARTIES AND MANAGEMENT: inexperienced developer or contractor; owner acting as its own GC;
thin contractor bonding or insurance; key consultants not engaged; reviewer could not verify
something (documents not provided, site not accessible).
FINANCE AND DRAWS: equity not in place; loan budget not matching the contract; stored
materials or offsite materials funded; lien or payment risk."""

#: Wording that names the label itself. Criteria are features for predicting change
#: orders; one that asks whether change orders, amendments or overruns happened is
#: the outcome restated (target leakage), so it is refused however it is phrased.
LEAKAGE = re.compile(
    r"change[\s-]*orders?|\bC\.?O\.?s?\b|amendments?\b|cost[\s-]*overruns?|over[\s-]*runs?|"
    r"budget (has been |was |is )?(increase|adjust|revis|reallocat|overrun)\w*|"
    r"(increase|adjust|revis|reallocat)\w* (to|in|of) the (contract|budget|GMP|loan)|contract sum|"
    r"budget reallocations?|reallocat\w+",
    re.I,
)

#: What the semantic guard compares a proposal against (BGE-M3, cosine).
TARGET_DESCRIPTIONS = [
    "The project had change orders that increased the construction contract or budget.",
    "Construction costs overran the budget and the contract sum was increased by amendments.",
    "The general contractor submitted change orders adding cost to the project.",
    "The loan budget was adjusted or reallocated after costs changed.",
]
#: Proposals at or above this cosine to any target description are refused. On the
#: 139 criteria so far, the leak ("a change order ... resulted in an increase") sat at
#: 0.786 and the nearest legitimate causes (cost-plus contract, code upgrades,
#: estimate above budget) at 0.65-0.70.
LEAKAGE_SIMILARITY = 0.75


def implies_target(wording: str) -> bool:
    """Whether a criterion's wording names change orders, amendments, overruns or budget changes."""
    return bool(LEAKAGE.search(wording))


SYSTEM = f"""You design yes/no criteria for a decision model. The model reads one passage \
(~400 tokens) cut from a construction lender's initial report on a project — a cost review, \
construction analysis or estimate prepared before construction — and answers each criterion \
with P(yes). The criteria are features for predicting which projects later suffer budget change \
orders, so they must describe conditions that cause change orders on real projects.

Write criteria about these known risk areas:
{RISKS}

A criterion is useful when it:
- applies to ANY construction project — it must make sense for an apartment block, a warehouse, \
a clinic or a renovation alike, and it should plausibly be true in roughly 5-40% of reports;
- names a condition or finding, not a fact about one project: NEVER mention a company, firm, \
person, project name, city, street, specific dollar figure, date or percentage;
- is answerable yes or no from a single passage, with one fact per criterion — no "and/or" lists;
- is written as "Does this passage say ...?" in the vocabulary these reports use (GMP, \
contingency, allowance, exclusions, plans and specifications, schedule of values, general \
contractor, lender, draw, retention, soft costs, hard costs);
- describes a CAUSE that exists before construction, never the outcome: do NOT ask whether \
change orders, amendments, cost overruns, budget increases, budget adjustments or reallocations, \
or contract-sum changes have happened or are mentioned — that is what the criteria predict.

Do not repeat or trivially rephrase an existing criterion."""

#: Picture criteria: what a site photo, rendering, plan or chart can show about risk.
SYSTEM_VISUAL = """You design yes/no criteria for a decision model that looks at one picture cut from a \
construction lender's initial report — a site photograph, an aerial or satellite view, a map, a \
rendering, a plan or elevation drawing, or a chart — and answers each criterion with P(yes).

The criteria are features for predicting which projects later suffer budget change orders, so \
they must describe what a picture can show about construction risk: existing structures to \
demolish; occupied or adjacent buildings; slopes, cuts or retaining walls; standing water or \
drainage problems; debris, fill or stockpiles; trees or vegetation to clear; overhead or \
exposed utilities; access constraints; a renovation's existing condition (damage, decay, \
outdated systems); work already in progress; a dense urban site versus open land; drawings \
that look preliminary (no dimensions, marked conceptual) versus complete.

Rules:
- The answer must be visible in the picture itself, not in surrounding text.
- Apply to ANY project; never name a company, place or project.
- One visual fact per criterion, phrased "Does this image show ...?"
- Do not repeat or trivially rephrase an existing criterion."""


def _chat(messages: list[dict], *, model: str = MODEL, temperature: float = 0.7,
          timeout: float = 1800) -> str:
    """One structured-output chat call; returns the JSON text."""
    body = {"model": model, "stream": False, "think": False, "format": SCHEMA,
            "options": {"temperature": temperature, "num_ctx": 16384}, "messages": messages}
    req = urllib.request.Request(HOST.rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["message"]["content"]


def _brief(kept: list[dict], dropped: list[dict], uncovered: list[str], n: int) -> str:
    lines = [f"Propose {n} criteria for the next round.", "",
             "Do NOT name firms, people, projects, places, dollar figures or dates.", ""]
    if kept:
        lines.append("KEPT so far (do not repeat these; you may propose sharper siblings):")
        for c in kept[:60]:
            lines.append(f"- [{c['name']}] {c['wording']} — {c.get('reason', '')}")
        lines.append("")
    if dropped:
        lines.append("DROPPED last round, with the reason. Revise the promising ones: narrow a "
                     "pervasive one, broaden a rare one, make an incoherent one about meaning not words. "
                     "Set `revises` to the bracketed name when you revise one.")
        for c in dropped[:40]:
            lines.append(f"- [{c['name']}] {c['wording']} — {c.get('reason', '')}")
        lines.append("")
    if uncovered:
        lines.append("PASSAGES no kept criterion fires on yet. At least half of your criteria "
                     "should be about things passages like these say:")
        for i, t in enumerate(uncovered, 1):
            lines.append(f"--- passage {i} ---\n{t.strip()[:1600]}")
        lines.append("")
    lines.append("Spread the new criteria across the risk areas, favouring areas the kept criteria "
                 "do not cover yet. Return JSON: {\"criteria\": [{\"question\", \"why\", \"revises\"}]}.")
    return "\n".join(lines)


def propose(kept: list[dict], dropped: list[dict], uncovered: list[str], *, n: int = 20,
            model: str = MODEL, temperature: float = 0.7) -> list[dict]:
    """New criteria as SystemOne questions, deduplicated against `kept` and each other."""
    raw = _chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": _brief(kept, dropped, uncovered, n)}],
                model=model, temperature=temperature)
    items = json.loads(raw).get("criteria", [])
    seen = {c["wording"].strip().lower() for c in kept}
    out = []
    for item in items:
        q = " ".join(str(item.get("question", "")).split())
        if not q or q.lower() in seen or implies_target(q):
            continue
        if not q.endswith("?"):
            q += "?"
        seen.add(q.lower())
        out.append({"question": {"type": "noul", "instructions": q},
                    "why": item.get("why", ""), "revises": item.get("revises")})
    return out[:n]


def propose_visual(kept: list[dict], dropped: list[dict], examples: list[dict], *, n: int = 20,
                   model: str = MODEL, temperature: float = 0.7) -> list[dict]:
    """Image criteria (`Does this image show ...?`) from the picture classes and captions on file.

    `examples` are `corpus.images` rows (kind, caption); the generator never sees the
    pixels, only what kinds of pictures exist and how the reports caption them.
    """
    lines = [f"Propose {n} picture criteria.", ""]
    if kept:
        lines.append("KEPT picture criteria (do not repeat):")
        lines += [f"- [{c['name']}] {c['wording']} — {c.get('reason', '')}" for c in kept[:60]]
        lines.append("")
    if dropped:
        lines.append("DROPPED picture criteria and why (revise the promising ones, set `revises`):")
        lines += [f"- [{c['name']}] {c['wording']} — {c.get('reason', '')}" for c in dropped[:40]]
        lines.append("")
    if examples:
        lines.append("The pictures on file look like this (class, caption):")
        lines += [f"- {e.get('kind') or 'picture'}: {e.get('caption') or '(no caption)'}" for e in examples[:40]]
        lines.append("")
    lines.append("Return JSON: {\"criteria\": [{\"question\", \"why\", \"revises\"}]}.")
    raw = _chat([{"role": "system", "content": SYSTEM_VISUAL}, {"role": "user", "content": "\n".join(lines)}],
                model=model, temperature=temperature)
    seen = {c["wording"].strip().lower() for c in kept}
    out = []
    for item in json.loads(raw).get("criteria", []):
        q = " ".join(str(item.get("question", "")).split())
        if not q or q.lower() in seen:
            continue
        if not q.endswith("?"):
            q += "?"
        seen.add(q.lower())
        out.append({"question": {"type": "noul", "instructions": q},
                    "why": item.get("why", ""), "revises": item.get("revises")})
    return out[:n]
