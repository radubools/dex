//#region skills/postgres-experiments-state/169cc8/widgets/criteria-dashboard/src/index.ts
var e = "http://127.0.0.1:4317", t = .3, n = "chunks.json", r = [
	["hits", "hits"],
	["lift", "lift"],
	["support", "support"],
	["mean_confidence", "mean confidence"],
	["J", "Youden's J"]
], i = "\nSELECT DISTINCT ON (s.id)\n       s.name, s.kind,\n       coalesce(h.detail->>'model',\n                CASE s.kind WHEN 'jev_criterion' THEN 'jev' ELSE 'laya' END) AS model,\n       coalesce(h.detail->'question', s.body->'question', '{}'::jsonb)       AS question,\n       h.metrics,\n       (h.metrics->>'hits')::float AS hits,\n       h.recorded_at, h.note\n  FROM experiments.state   s\n  JOIN experiments.history h ON h.state_id = s.id\n WHERE h.task = $1\n   AND s.kind IN ('laya_criterion', 'jev_criterion')\n ORDER BY s.id,\n          (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n          h.recorded_at DESC, h.id DESC", a = "\nWITH latest AS (\n  SELECT h.id\n    FROM experiments.state   s\n    JOIN experiments.history h ON h.state_id = s.id\n   WHERE h.task = $1 AND s.name = $2 AND s.kind = $3\n   ORDER BY (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n            h.recorded_at DESC, h.id DESC\n   LIMIT 1\n)\nSELECT a.key                            AS chunk,\n       a.value->>'verdict'              AS verdict,\n       CASE WHEN a.value->>'verdict' ~ '^-?[0-9]+(\\.[0-9]+)?$'\n            THEN (a.value->>'verdict')::float END AS score,\n       (a.value->>'confidence')::float  AS confidence,\n       a.value->'probabilities'         AS probabilities\n  FROM experiments.history h\n  JOIN latest l ON l.id = h.id\n  CROSS JOIN LATERAL jsonb_each(coalesce(h.detail->'answers', '{}'::jsonb)) AS a\n ORDER BY score DESC NULLS LAST, confidence DESC NULLS LAST, chunk";
function o(e) {
	let t;
	try {
		t = JSON.parse(e);
	} catch (e) {
		throw Error(`not a criteria file: ${e.message}`);
	}
	let n = t;
	if (!n || typeof n != "object" || Array.isArray(n)) throw Error("not a criteria file: the top level is not an object");
	if (!n.task && !Array.isArray(n.criteria)) throw Error("not a criteria file: needs a \"task\" to query, or a \"criteria\" array");
	return n;
}
async function s(e, t, n = [], r = 2e4) {
	let i = `${e.replace(/\/+$/, "")}/api/sql?sql=${encodeURIComponent(t)}&params=${encodeURIComponent(JSON.stringify(n))}&limit=${r}`, a;
	try {
		a = await fetch(i);
	} catch (t) {
		throw Error(`cannot reach ${e}/api/sql (${t.message}). Is dex running, and is the /api/sql router installed?`);
	}
	let o = await a.json().catch(() => ({}));
	if (!a.ok) throw Error(o.error ?? `/api/sql returned ${a.status}`);
	return o.rows ?? [];
}
function c(e) {
	return e == null || e === "" ? "—" : typeof e == "number" ? Number.isFinite(e) ? Number.isInteger(e) ? String(e) : e.toFixed(e < 1 ? 4 : 3) : "—" : typeof e == "boolean" ? e ? "yes" : "no" : String(e);
}
function l(e) {
	return [...e].sort((e, t) => (u(t) ?? -1) - (u(e) ?? -1) || e.name.localeCompare(t.name));
}
function u(e) {
	for (let t of [e.hits, e.metrics?.hits]) if (typeof t == "number" && Number.isFinite(t)) return t;
	return null;
}
function d(e, t) {
	return e.filter((e) => f(e) >= t).sort((e, t) => f(t) - f(e) || e.chunk.localeCompare(t.chunk));
}
function f(e) {
	return typeof e.score == "number" && Number.isFinite(e.score) ? e.score : typeof e.verdict == "number" && Number.isFinite(e.verdict) ? e.verdict : typeof e.confidence == "number" && Number.isFinite(e.confidence) ? e.confidence : -1;
}
var p = "\n.cd { font: 14px/1.5 system-ui, sans-serif; display: grid; grid-template-columns: 260px 1fr;\n  gap: 0 18px; align-items: start; }\n.cd-head { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px;\n  padding-bottom: 10px; border-bottom: 1px solid var(--cd-line); margin-bottom: 12px; }\n.cd-task { font-size: 16px; font-weight: 650; margin: 0; font-family: ui-monospace, monospace; }\n.cd-chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--cd-line);\n  text-transform: uppercase; letter-spacing: .05em; }\n.cd-chip.laya { border-color: var(--cd-laya); color: var(--cd-laya); }\n.cd-chip.jev { border-color: var(--cd-jev); color: var(--cd-jev); }\n.cd-note { grid-column: 1 / -1; font-size: 12.5px; padding: 8px 10px; border-radius: 6px;\n  border: 1px solid var(--cd-warn-line); background: var(--cd-warn-bg); margin-bottom: 10px; }\n.cd-side { display: flex; flex-direction: column; gap: 8px; min-width: 0; }\n.cd-filter { width: 100%; box-sizing: border-box; padding: 5px 8px; border-radius: 6px;\n  border: 1px solid var(--cd-line); background: transparent; color: inherit; font: inherit; }\n.cd-list { list-style: none; margin: 0; padding: 0; max-height: 620px; overflow-y: auto;\n  border: 1px solid var(--cd-line); border-radius: 8px; }\n.cd-item { display: flex; justify-content: space-between; gap: 8px; align-items: baseline;\n  padding: 6px 9px; cursor: pointer; border-bottom: 1px solid var(--cd-line-soft); }\n.cd-item:last-child { border-bottom: 0; }\n.cd-item:hover { background: var(--cd-hover); }\n.cd-item[aria-selected=true] { background: var(--cd-sel); font-weight: 650; }\n.cd-item[hidden] { display: none; }\n.cd-name { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }\n.cd-hits { font-family: ui-monospace, monospace; font-size: 11px; opacity: .6; flex: none; }\n.cd-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }\n.cd-q { margin: 0; font-size: 15px; }\n.cd-meta { font-size: 11.5px; opacity: .6; font-family: ui-monospace, monospace; }\n.cd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }\n.cd-card { border: 1px solid var(--cd-line); border-radius: 8px; padding: 8px 10px; }\n.cd-card-v { font-size: 19px; font-weight: 650; font-variant-numeric: tabular-nums; }\n.cd-card-k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }\n.cd-rest { font-size: 11.5px; opacity: .7; font-family: ui-monospace, monospace; word-break: break-all; }\n.cd-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 13px; }\n.cd-bar input[type=range] { flex: 1 1 160px; }\n.cd-count { font-variant-numeric: tabular-nums; }\n.cd-chunks { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow-y: auto;\n  border: 1px solid var(--cd-line); border-radius: 8px; }\n.cd-chunk { display: grid; grid-template-columns: 74px 1fr; gap: 0 10px; padding: 9px 11px;\n  border-bottom: 1px solid var(--cd-line-soft); }\n.cd-chunk:last-child { border-bottom: 0; }\n.cd-score { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 650;\n  font-variant-numeric: tabular-nums; }\n.cd-conf { font-family: ui-monospace, monospace; font-size: 10.5px; opacity: .55; }\n.cd-id { font-family: ui-monospace, monospace; font-size: 10px; opacity: .4; }\n.cd-text { margin: 0; white-space: pre-wrap; font-size: 13px; max-height: 8.4em; overflow: hidden;\n  position: relative; }\n.cd-text.open { max-height: none; }\n.cd-more { font-size: 11px; opacity: .6; cursor: pointer; background: none; border: 0;\n  color: inherit; padding: 2px 0; text-align: left; }\n.cd-empty { padding: 14px; opacity: .6; font-size: 13px; }\n.cd-err { color: var(--cd-alarm); font-family: ui-monospace, monospace; font-size: 12px;\n  white-space: pre-wrap; }\n", m = {
	dark: "--cd-line:#2a2f3a; --cd-line-soft:#20242c; --cd-hover:#1b1f27; --cd-sel:#243043;\n         --cd-laya:#7dd3fc; --cd-jev:#c4b5fd; --cd-alarm:#e0736b;\n         --cd-warn-line:#5a3b1c; --cd-warn-bg:#2a1f12;",
	light: "--cd-line:#d9dde4; --cd-line-soft:#ecEEF2; --cd-hover:#f3f5f8; --cd-sel:#e3ecfa;\n          --cd-laya:#0369a1; --cd-jev:#6d28d9; --cd-alarm:#b3372c;\n          --cd-warn-line:#e3c99a; --cd-warn-bg:#fdf6e8;"
}, h = (e, t, n) => {
	let r = document.createElement(e);
	return t && (r.className = t), n !== void 0 && (r.textContent = n), r;
};
async function g(e, n) {
	let g = o(n.text), _ = g.api ?? "http://127.0.0.1:4317", v = typeof g.threshold == "number" ? g.threshold : t, y = n.theme !== "light", b = h("div", "cd");
	b.setAttribute("style", m[y ? "dark" : "light"]);
	let x = h("style");
	x.textContent = p, e.replaceChildren(x, b);
	let S = {}, C = "", w = (e, t = []) => {
		for (let [t, n] of Object.entries(e ?? {})) S[t] = typeof n == "string" ? { text: n } : n ?? { text: "" };
		t.forEach((e, t) => {
			S[e] && S[e].n === void 0 && (S[e].n = t + 1);
		});
	};
	if (g.chunks && typeof g.chunks == "object") w(g.chunks);
	else {
		let e = g.chunks ?? "chunks.json";
		try {
			let t = JSON.parse(await n.fetchAsset(e));
			w(t?.chunks ?? t, t?.meta?.order ?? []);
		} catch (t) {
			C = `no chunk text (${e}: ${t.message}) — showing ids only`;
		}
	}
	let T = Array.isArray(g.criteria), E;
	if (E = T ? g.criteria : (await s(_, i, [g.task])).map((e) => ({
		name: String(e.name),
		kind: String(e.kind),
		model: String(e.model ?? ""),
		question: e.question ?? {},
		metrics: e.metrics ?? {},
		hits: typeof e.hits == "number" ? e.hits : null,
		recorded_at: e.recorded_at ? String(e.recorded_at) : void 0,
		note: e.note ? String(e.note) : null
	})), !E.length) throw Error(`no criterion in experiments.state was scored by task "${g.task ?? ""}"`);
	let D = l(E), O = h("div", "cd-head");
	O.append(h("h1", "cd-task", g.task ?? n.path.split("/").pop() ?? "criteria")), O.append(h("span", "cd-chip", `${D.length} criteria`));
	for (let e of [...new Set(D.map((e) => e.model))].sort()) O.append(h("span", `cd-chip ${e.startsWith("jev") ? "jev" : "laya"}`, e));
	O.append(h("span", "cd-chip", T ? "from the file" : "live")), b.append(O), C && b.append(h("div", "cd-note", C));
	let k = h("div", "cd-side"), A = document.createElement("input");
	A.className = "cd-filter", A.type = "search", A.placeholder = `filter ${D.length} names…`;
	let j = h("ul", "cd-list"), M = [];
	for (let e of D) {
		let t = h("li", "cd-item");
		t.setAttribute("role", "option"), t.setAttribute("aria-selected", "false"), t.dataset.name = e.name, t.append(h("span", "cd-name", e.name));
		let n = u(e);
		t.append(h("span", "cd-hits", n === null ? "—" : String(n))), j.append(t), M.push({
			node: t,
			criterion: e
		});
	}
	k.append(A, j);
	let N = h("div", "cd-main");
	b.append(k, N);
	let P = 0, F = /* @__PURE__ */ new Map();
	async function I(e) {
		let t = ++P;
		for (let t of M) t.node.setAttribute("aria-selected", String(t.criterion === e));
		N.replaceChildren();
		let n = e.question ?? {};
		N.append(h("p", "cd-q", n.instructions ?? "(no question recorded)")), N.append(h("div", "cd-meta", [
			e.name,
			e.model,
			n.type ?? "",
			e.recorded_at ? `recorded ${e.recorded_at}` : ""
		].filter(Boolean).join(" · ")));
		let i = e.metrics ?? {}, o = h("div", "cd-cards");
		for (let [e, t] of r) {
			let n = h("div", "cd-card");
			n.dataset.metric = e, n.append(h("div", "cd-card-v", c(i[e]))), n.append(h("div", "cd-card-k", t)), o.append(n);
		}
		N.append(o);
		let l = Object.keys(i).filter((e) => !r.some(([t]) => t === e)).sort();
		l.length && N.append(h("div", "cd-rest", `also recorded: ${l.map((e) => `${e}=${c(i[e])}`).join("  ")}`));
		let u = h("div", "cd-bar"), p = document.createElement("input");
		p.type = "range", p.min = "0", p.max = "100", p.step = "1", p.value = String(Math.round(v * 100));
		let m = h("span", "cd-count"), y = h("span", void 0, "verdict ≥"), b = h("span", "cd-count", `${p.value}%`);
		u.append(y, b, p, m), N.append(u);
		let x = h("ul", "cd-chunks");
		N.append(x);
		let C = e.answers ?? F.get(e.name);
		if (!C) {
			x.append(h("li", "cd-empty", "loading chunks…"));
			try {
				C = (await s(_, a, [
					g.task,
					e.name,
					e.kind
				])).map((e) => ({
					chunk: String(e.chunk),
					verdict: e.verdict,
					score: typeof e.score == "number" ? e.score : null,
					confidence: typeof e.confidence == "number" ? e.confidence : null,
					probabilities: e.probabilities ?? null
				}));
			} catch (e) {
				if (t !== P) return;
				x.replaceChildren(), N.append(h("div", "cd-err", e.message));
				return;
			}
			F.set(e.name, C);
		}
		if (t !== P) return;
		let w = () => {
			let e = Number(p.value) / 100;
			b.textContent = `${p.value}%`;
			let t = d(C, e);
			if (m.textContent = `${t.length} of ${C.length} chunks`, x.replaceChildren(), !t.length) {
				x.append(h("li", "cd-empty", `no chunk reaches ${p.value}% — drag the slider down`));
				return;
			}
			for (let e of t) {
				let t = h("li", "cd-chunk");
				t.dataset.chunk = e.chunk;
				let n = h("div"), r = f(e), i = typeof e.score == "number" || typeof e.verdict == "number";
				n.append(h("div", "cd-score", i ? `${(r * 100).toFixed(0)}%` : String(e.verdict ?? "?"))), typeof e.confidence == "number" && n.append(h("div", "cd-conf", `conf ${e.confidence.toFixed(2)}`));
				let a = S[e.chunk];
				n.append(h("div", "cd-id", a?.n ? `#${a.n}` : e.chunk.slice(0, 6)));
				let o = h("div"), s = h("p", "cd-text", a?.text ?? `(no text for ${e.chunk})`);
				if (o.append(s), (a?.text ?? "").length > 420) {
					let e = h("button", "cd-more", "show all");
					e.addEventListener("click", () => {
						let t = s.classList.toggle("open");
						e.textContent = t ? "show less" : "show all";
					}), o.append(e);
				}
				t.append(n, o), x.append(t);
			}
		};
		p.addEventListener("input", w), w();
	}
	let L = (e) => {
		let t = e.target.closest(".cd-item");
		if (!t) return;
		let n = M.find((e) => e.node === t);
		n && I(n.criterion);
	};
	j.addEventListener("click", L);
	let R = () => {
		let e = A.value.trim().toLowerCase();
		for (let t of M) t.node.hidden = e.length > 0 && !t.criterion.name.toLowerCase().includes(e);
	};
	return A.addEventListener("input", R), await I(D[0]), () => {
		j.removeEventListener("click", L), A.removeEventListener("input", R), e.replaceChildren();
	};
}
//#endregion
export { a as ANSWERS_SQL, r as CARDS, i as CRITERIA_SQL, e as DEFAULT_API, n as DEFAULT_CHUNKS, t as DEFAULT_THRESHOLD, d as applicable, s as ask, u as hitsOf, g as mount, l as ordered, o as parsePointer, c as show, f as strength };
