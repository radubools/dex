"""Ask criteria about the picture regions OCR indexed (`corpus.images`) with Clef-Flash's image input.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.image_sweep                          # kept criteria x all pictures
    ../../.venv/bin/python -m utils.image_sweep --criteria 12 --criteria 31
    ../../.venv/bin/python -m utils.image_sweep --folder Fallbrook --min-share 0.1 --caption
    ../../.venv/bin/python -m utils.image_sweep --top 20                 # strongest matches so far

Decisions land in `criteria.image_decisions`, one per (criterion, picture), and
a pair already decided is not asked again. `--caption` puts Docling's caption in
the state beside the image. Uses the vision backbone (`clef.load(vision=True)`),
a separate ~19 GB instance — do not run it beside a text round.

Only `modality = 'image'` criteria are asked about pictures; text criteria are never
sent to an image. `--round --generate 20` has the generator write picture criteria
("Does this image show ...?") from the picture classes and captions on file, asks
them of every content picture, and judges them with the coverage rule.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from . import clef, corpus, coverage, criteria_store as store, generator

#: Docling picture classes that are page furniture; skipped unless `all_kinds`.
FURNITURE = ("logo", "stamp", "signature")


async def run(conn, *, criterion_ids: list[int] | None = None, building_type: str | None = None,
              project_folder: str | None = None, min_page_share: float = 0.0, caption: bool = False,
              all_kinds: bool = False, log=print) -> dict:
    await corpus.ensure_schema(conn)
    await store.ensure_schema(conn)
    crits = await store.criteria(conn, ids=criterion_ids,
                                 status=None if criterion_ids else ["candidate", "kept"], modality="image")
    imgs = await corpus.images(conn, building_type=building_type, project_folder=project_folder,
                               min_page_share=min_page_share)
    if not all_kinds:
        imgs = [i for i in imgs if i["kind"] not in FURNITURE]
    if not crits or not imgs:
        log(f"nothing to do: {len(crits)} criteria, {len(imgs)} pictures")
        return {"pairs": 0}
    done = {(r["criterion_id"], r["image_id"]) for r in await conn.fetch(
        f"SELECT criterion_id, image_id FROM {store.SCHEMA}.image_decisions WHERE criterion_id = ANY($1)",
        [c["id"] for c in crits])}
    log(f"{len(crits)} criteria x {len(imgs)} pictures, {len(done)} pairs already decided")
    model = clef.load(vision=True)
    pairs, started = 0, time.time()
    for i, img in enumerate(imgs, 1):
        todo = [c for c in crits if (c["id"], img["id"]) not in done]
        if not todo:
            continue
        png = await conn.fetchval(f"SELECT png FROM {corpus.SCHEMA}.images WHERE id = $1", img["id"])
        qs = {f"c{c['id']}": c["question"] for c in todo}
        try:
            result = await asyncio.to_thread(clef.ask_image, png, qs,
                                             text=img["caption"] if caption else None, model=model)
        except Exception as exc:  # noqa: BLE001 - note it in corpus.errors, carry on
            log(f"  FAILED picture {img['id']}: {type(exc).__name__}: {exc}")
            await corpus.record_error(conn, "image", exc, image_id=img["id"], document_id=img["document_id"],
                                      page=img["page"], detail={"criteria": [c["id"] for c in todo]})
            continue
        await corpus.resolve_error(conn, "image", image_id=img["id"], document_id=img["document_id"],
                                   page=img["page"])
        await conn.executemany(
            f"""INSERT INTO {store.SCHEMA}.image_decisions (criterion_id, image_id, p, with_caption, evaluator)
                VALUES ($1, $2, $3, $4, $5) ON CONFLICT (criterion_id, image_id) DO NOTHING""",
            [(int(q[1:]), img["id"], float(clef.scale(a)), caption and bool(img["caption"]), clef.MODEL)
             for q, a in result["answers"].items()],
        )
        pairs += len(todo)
        if i % 10 == 0 or i == len(imgs):
            log(f"  {i}/{len(imgs)} pictures, {pairs} pairs, {(time.time() - started) / i:.1f} s/picture")
    return {"pairs": pairs, "pictures": len(imgs), "criteria": len(crits)}


async def run_round(conn, *, generate: int = 0, caption: bool = False, rule: coverage.Rule | None = None,
                    note: str | None = None, log=print) -> dict:
    """An image round: (generate visual criteria) -> ask them of every content picture -> judge.

    Judged by the same rule as text (`coverage.judge`): eligible once decided on ≥ 5
    documents and > 70% of the pictures; dropped only as non-confident, low
    variance or correlated with a kept image criterion.
    """
    rule = rule or coverage.Rule()
    await store.ensure_schema(conn)
    await coverage.ensure_schema(conn)
    rid = await store.start_round(conn, evaluator=clef.MODEL,
                                  generator=generator.MODEL if generate else None,
                                  params={"mode": "image", "generate": generate, "caption": caption,
                                          "rule": coverage.rule_dict(rule)}, note=note)
    pics = [i for i in await corpus.images(conn) if i["kind"] not in FURNITURE]
    log(f"round {rid} (image): {len(pics)} content pictures")
    if generate:
        kept = await store.criteria(conn, status="kept", modality="image")
        dropped = [c for c in await store.criteria(conn, status="dropped", modality="image")]
        import random
        examples = random.Random(rid).sample(pics, min(40, len(pics)))
        proposals = await asyncio.to_thread(generator.propose_visual, kept, dropped, examples, n=generate)
        by_name = {c["name"]: c["id"] for c in kept + dropped}
        items = [{"question": p["question"], "parent_id": by_name.get(p.get("revises") or "")} for p in proposals]
        added = await store.add_criteria(conn, items, origin="generated", round_id=rid, modality="image")
        log(f"  generator added {len(added)} picture criteria")
    out = await run(conn, caption=caption, log=log)
    live = await store.criteria(conn, status=["candidate", "kept"], modality="image")
    ids = [c["id"] for c in live]
    if not ids:
        return {"round": rid, **out}
    profiles = await coverage.profile(conn, ids, [p["id"] for p in pics], band=rule.band,
                                      table="image_decisions")
    verdicts = coverage.judge(profiles, rule)
    await coverage.record(conn, rid, verdicts)
    counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("kept", "dropped", "pending")}
    await store.finish_round(conn, rid, {**out, **counts})
    log(f"round {rid} done: {counts['kept']} kept, {counts['dropped']} dropped, {counts['pending']} pending")
    for v in verdicts:
        if v["verdict"] == "dropped":
            log(f"    dropped {v['criterion_id']}: {v['reason']}")
    return {"round": rid, **out, **counts}


async def top(conn, n: int = 20, threshold: float = 0.5) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT c.name, d.filename, i.page, i.kind, i.caption, round(x.p::numeric, 3) AS p
              FROM {store.SCHEMA}.image_decisions x
              JOIN {store.SCHEMA}.criteria c ON c.id = x.criterion_id
              JOIN {corpus.SCHEMA}.images i ON i.id = x.image_id
              JOIN {corpus.SCHEMA}.documents d ON d.id = i.document_id
             WHERE x.p >= $2 ORDER BY x.p DESC LIMIT $1""",
        n, threshold,
    )
    return [dict(r) for r in rows]


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        if args.round:
            print(json.dumps(await run_round(conn, generate=args.generate, caption=args.caption,
                                             note=args.note), indent=2, default=str))
            return 0
        if args.top:
            print(json.dumps(await top(conn, args.top), indent=2, default=str))
            return 0
        print(json.dumps(await run(conn, criterion_ids=args.criteria, building_type=args.building_type,
                                   project_folder=args.folder, min_page_share=args.min_share,
                                   caption=args.caption, all_kinds=args.all_kinds), indent=2))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m utils.image_sweep", description=__doc__.split("\n")[0])
    p.add_argument("--criteria", type=int, action="append", help="criterion id (default: every kept one)")
    p.add_argument("--building-type")
    p.add_argument("--folder")
    p.add_argument("--min-share", type=float, default=0.0, help="smallest picture, as a share of its page")
    p.add_argument("--caption", action="store_true", help="give Clef the picture's caption too")
    p.add_argument("--all-kinds", action="store_true", help="include logos, stamps and signatures")
    p.add_argument("--round", action="store_true",
                   help="a full image round: generate, ask, judge (with --generate N)")
    p.add_argument("--generate", type=int, default=0, help="picture criteria the LLM writes first")
    p.add_argument("--note")
    p.add_argument("--top", type=int, help="print the strongest matches so far and stop")
    sys.exit(asyncio.run(_main(p.parse_args())))
