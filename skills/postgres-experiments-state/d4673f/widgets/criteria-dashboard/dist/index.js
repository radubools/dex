//#region skills/postgres-experiments-state/d4673f/widgets/criteria-dashboard/src/index.ts
var e = "http://127.0.0.1:4317", t = .3, n = "chunks.json", r = [
	["hits", "hits"],
	["lift", "lift"],
	["support", "support"],
	["mean_confidence", "mean confidence"],
	["J", "Youden's J"]
], i = "\nSELECT DISTINCT ON (s.id)\n       s.name, s.kind,\n       coalesce(h.detail->>'model',\n                CASE s.kind WHEN 'jev_criterion' THEN 'jev' ELSE 'laya' END) AS model,\n       coalesce(h.detail->'question', s.body->'question', '{}'::jsonb)       AS question,\n       h.metrics,\n       (h.metrics->>'hits')::float AS hits,\n       h.recorded_at, h.note\n  FROM experiments.state   s\n  JOIN experiments.history h ON h.state_id = s.id\n WHERE h.task = $1\n   AND s.kind IN ('laya_criterion', 'jev_criterion')\n ORDER BY s.id,\n          (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n          h.recorded_at DESC, h.id DESC", a = "\nWITH latest AS (\n  SELECT h.id\n    FROM experiments.state   s\n    JOIN experiments.history h ON h.state_id = s.id\n   WHERE h.task = $1 AND s.name = $2 AND s.kind = $3\n   ORDER BY (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n            h.recorded_at DESC, h.id DESC\n   LIMIT 1\n), expanded AS (\n  SELECT a.key   AS chunk,\n         a.value AS raw,\n         CASE jsonb_typeof(a.value)\n              WHEN 'object' THEN a.value->>'verdict'\n              WHEN 'array'  THEN a.value->>0\n              ELSE a.value #>> '{}'\n         END AS verdict_text,\n         CASE jsonb_typeof(a.value)\n              WHEN 'object' THEN a.value->>'confidence'\n              WHEN 'array'  THEN a.value->>1\n         END AS confidence_text\n    FROM experiments.history h\n    JOIN latest l ON l.id = h.id\n    CROSS JOIN LATERAL jsonb_each(coalesce(h.detail->'answers', '{}'::jsonb)) AS a\n)\nSELECT chunk,\n       verdict_text AS verdict,\n       CASE WHEN verdict_text ~ '^-?[0-9]+(\\.[0-9]+)?$'\n            THEN verdict_text::float END AS score,\n       CASE WHEN confidence_text ~ '^-?[0-9]+(\\.[0-9]+)?$'\n            THEN confidence_text::float END AS confidence,\n       CASE WHEN jsonb_typeof(raw) = 'object' THEN raw->'probabilities' END AS probabilities\n  FROM expanded\n ORDER BY score DESC NULLS LAST, confidence DESC NULLS LAST, chunk", o = "\nSELECT h.task,\n       count(DISTINCT s.id) AS criteria,\n       count(*)             AS entries,\n       max(h.recorded_at)   AS last_run\n  FROM experiments.history h\n  JOIN experiments.state   s ON s.id = h.state_id\n WHERE s.kind IN ('laya_criterion', 'jev_criterion')\n   AND h.task ~ $1\n GROUP BY h.task\n ORDER BY criteria DESC, entries DESC, h.task";
function s(e) {
	let t = e.split("/").filter(Boolean);
	return t.length >= 2 ? t[t.length - 2] : "";
}
function c(e) {
	return `(^|/)${e.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[/#])`;
}
function l(e) {
	let t;
	try {
		t = JSON.parse(e);
	} catch (e) {
		throw Error(`not a criteria file: ${e.message}`);
	}
	let n = t;
	if (!n || typeof n != "object" || Array.isArray(n)) throw Error("not a criteria file: the top level is not an object");
	return n;
}
var u = /^[0-9a-f]{8,}$/;
function d(e) {
	if (e.task || Array.isArray(e.criteria)) return !1;
	let t = e.chunks;
	if (t && typeof t == "object") return !0;
	let n = Object.keys(e);
	return n.length > 0 && n.every((e) => u.test(e));
}
async function f(e, t, n = [], r = 2e4) {
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
function p(e) {
	return e == null || e === "" ? "—" : typeof e == "number" ? Number.isFinite(e) ? Number.isInteger(e) ? String(e) : e.toFixed(e < 1 ? 4 : 3) : "—" : typeof e == "boolean" ? e ? "yes" : "no" : String(e);
}
function m(e) {
	return [...e].sort((e, t) => (h(t) ?? -1) - (h(e) ?? -1) || e.name.localeCompare(t.name));
}
function h(e) {
	for (let t of [e.hits, e.metrics?.hits]) if (typeof t == "number" && Number.isFinite(t)) return t;
	return null;
}
function g(e, t) {
	return e.filter((e) => _(e) >= t).sort((e, t) => _(t) - _(e) || e.chunk.localeCompare(t.chunk));
}
function _(e) {
	return typeof e.score == "number" && Number.isFinite(e.score) ? e.score : typeof e.verdict == "number" && Number.isFinite(e.verdict) ? e.verdict : typeof e.confidence == "number" && Number.isFinite(e.confidence) ? e.confidence : -1;
}
var v = "\n.cd { font: 14px/1.5 system-ui, sans-serif; display: grid; grid-template-columns: 260px 1fr;\n  gap: 0 18px; align-items: start; }\n.cd-head { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px;\n  padding-bottom: 10px; border-bottom: 1px solid var(--cd-line); margin-bottom: 12px; }\n.cd-task { font-size: 16px; font-weight: 650; margin: 0; font-family: ui-monospace, monospace; }\n.cd-chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--cd-line);\n  text-transform: uppercase; letter-spacing: .05em; }\n.cd-chip.laya { border-color: var(--cd-laya); color: var(--cd-laya); }\n.cd-chip.jev { border-color: var(--cd-jev); color: var(--cd-jev); }\n.cd-picker { font: inherit; font-size: 12px; font-family: ui-monospace, monospace;\n  background: transparent; color: inherit; border: 1px solid var(--cd-line);\n  border-radius: 6px; padding: 2px 4px; max-width: 100%; }\n.cd-note { grid-column: 1 / -1; font-size: 12.5px; padding: 8px 10px; border-radius: 6px;\n  border: 1px solid var(--cd-warn-line); background: var(--cd-warn-bg); margin-bottom: 10px; }\n.cd-side { display: flex; flex-direction: column; gap: 8px; min-width: 0; }\n.cd-filter { width: 100%; box-sizing: border-box; padding: 5px 8px; border-radius: 6px;\n  border: 1px solid var(--cd-line); background: transparent; color: inherit; font: inherit; }\n.cd-list { list-style: none; margin: 0; padding: 0; max-height: 620px; overflow-y: auto;\n  border: 1px solid var(--cd-line); border-radius: 8px; }\n.cd-item { display: flex; justify-content: space-between; gap: 8px; align-items: baseline;\n  padding: 6px 9px; cursor: pointer; border-bottom: 1px solid var(--cd-line-soft); }\n.cd-item:last-child { border-bottom: 0; }\n.cd-item:hover { background: var(--cd-hover); }\n.cd-item[aria-selected=true] { background: var(--cd-sel); font-weight: 650; }\n.cd-item[hidden] { display: none; }\n.cd-name { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }\n.cd-hits { font-family: ui-monospace, monospace; font-size: 11px; opacity: .6; flex: none; }\n.cd-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }\n.cd-q { margin: 0; font-size: 15px; }\n.cd-meta { font-size: 11.5px; opacity: .6; font-family: ui-monospace, monospace; }\n.cd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }\n.cd-card { border: 1px solid var(--cd-line); border-radius: 8px; padding: 8px 10px; }\n.cd-card-v { font-size: 19px; font-weight: 650; font-variant-numeric: tabular-nums; }\n.cd-card-k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }\n.cd-rest { font-size: 11.5px; opacity: .7; font-family: ui-monospace, monospace; word-break: break-all; }\n.cd-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 13px; }\n.cd-bar input[type=range] { flex: 1 1 160px; }\n.cd-count { font-variant-numeric: tabular-nums; }\n.cd-chunks { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow-y: auto;\n  border: 1px solid var(--cd-line); border-radius: 8px; }\n.cd-chunk { display: grid; grid-template-columns: 74px 1fr; gap: 0 10px; padding: 9px 11px;\n  border-bottom: 1px solid var(--cd-line-soft); }\n.cd-chunk:last-child { border-bottom: 0; }\n.cd-score { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 650;\n  font-variant-numeric: tabular-nums; }\n.cd-conf { font-family: ui-monospace, monospace; font-size: 10.5px; opacity: .55; }\n.cd-id { font-family: ui-monospace, monospace; font-size: 10px; opacity: .4; }\n.cd-text { margin: 0; white-space: pre-wrap; font-size: 13px; max-height: 8.4em; overflow: hidden;\n  position: relative; }\n.cd-text.open { max-height: none; }\n.cd-more { font-size: 11px; opacity: .6; cursor: pointer; background: none; border: 0;\n  color: inherit; padding: 2px 0; text-align: left; }\n.cd-empty { padding: 14px; opacity: .6; font-size: 13px; }\n.cd-err { color: var(--cd-alarm); font-family: ui-monospace, monospace; font-size: 12px;\n  white-space: pre-wrap; }\n", y = {
	dark: "--cd-line:#2a2f3a; --cd-line-soft:#20242c; --cd-hover:#1b1f27; --cd-sel:#243043;\n         --cd-laya:#7dd3fc; --cd-jev:#c4b5fd; --cd-alarm:#e0736b;\n         --cd-warn-line:#5a3b1c; --cd-warn-bg:#2a1f12;",
	light: "--cd-line:#d9dde4; --cd-line-soft:#ecEEF2; --cd-hover:#f3f5f8; --cd-sel:#e3ecfa;\n          --cd-laya:#0369a1; --cd-jev:#6d28d9; --cd-alarm:#b3372c;\n          --cd-warn-line:#e3c99a; --cd-warn-bg:#fdf6e8;"
}, b = (e, t, n) => {
	let r = document.createElement(e);
	return t && (r.className = t), n !== void 0 && (r.textContent = n), r;
};
async function x(e, n) {
	let u = l(n.text), S = u.api ?? "http://127.0.0.1:4317", C = typeof u.threshold == "number" ? u.threshold : t, w = n.theme !== "light", T = b("div", "cd");
	T.setAttribute("style", y[w ? "dark" : "light"]);
	let E = b("style");
	E.textContent = v, e.replaceChildren(E, T);
	let D = {}, O = "", k = (e, t = []) => {
		for (let [t, n] of Object.entries(e ?? {})) D[t] = typeof n == "string" ? { text: n } : n ?? { text: "" };
		t.forEach((e, t) => {
			D[e] && D[e].n === void 0 && (D[e].n = t + 1);
		});
	}, A = d(u);
	if (A) {
		let e = u;
		k(e.chunks ?? e, e.meta?.order ?? []);
	} else if (u.chunks && typeof u.chunks == "object") k(u.chunks);
	else {
		let e = u.chunks ?? "chunks.json";
		try {
			let t = JSON.parse(await n.fetchAsset(e));
			k(t?.chunks ?? t, t?.meta?.order ?? []);
		} catch (t) {
			O = `no chunk text (${e}: ${t.message}) — showing ids only`;
		}
	}
	let j = Array.isArray(u.criteria), M = u.task, N = [];
	if (!M && !j) {
		if (!A) throw Error("not a criteria file: needs a \"task\" to query, or a \"criteria\" array, or the chunk map itself");
		let e = s(n.path);
		if (!e) throw Error(`cannot tell which package ${n.path} belongs to`);
		if (N = (await f(S, o, [c(e)], 200)).map((e) => ({
			task: String(e.task),
			criteria: Number(e.criteria)
		})), !N.length) throw Error(`no Laya or Jev criterion in experiments.history names the package "${e}". Nothing has been scored here yet, or the run recorded a different task name.`);
		M = N[0].task;
	}
	let P;
	if (P = j ? u.criteria : (await f(S, i, [M])).map((e) => ({
		name: String(e.name),
		kind: String(e.kind),
		model: String(e.model ?? ""),
		question: e.question ?? {},
		metrics: e.metrics ?? {},
		hits: typeof e.hits == "number" ? e.hits : null,
		recorded_at: e.recorded_at ? String(e.recorded_at) : void 0,
		note: e.note ? String(e.note) : null
	})), !P.length) throw Error(`no criterion in experiments.state was scored by task "${M ?? ""}"`);
	let F = m(P), I = b("div", "cd-head");
	I.append(b("h1", "cd-task", M ?? n.path.split("/").pop() ?? "criteria")), I.append(b("span", "cd-chip", `${F.length} criteria`));
	for (let e of [...new Set(F.map((e) => e.model))].sort()) I.append(b("span", `cd-chip ${e.startsWith("jev") ? "jev" : "laya"}`, e));
	I.append(b("span", "cd-chip", j ? "from the file" : "live"));
	let L = null;
	if (N.length > 1) {
		L = document.createElement("select"), L.className = "cd-picker";
		for (let e of N) {
			let t = document.createElement("option");
			t.value = e.task, t.textContent = `${e.task}  (${e.criteria})`, t.selected = e.task === M, L.append(t);
		}
		I.append(L);
	}
	T.append(I), O && T.append(b("div", "cd-note", O));
	let R = b("div", "cd-side"), z = document.createElement("input");
	z.className = "cd-filter", z.type = "search", z.placeholder = `filter ${F.length} names…`;
	let B = b("ul", "cd-list"), V = [];
	for (let e of F) {
		let t = b("li", "cd-item");
		t.setAttribute("role", "option"), t.setAttribute("aria-selected", "false"), t.dataset.name = e.name, t.append(b("span", "cd-name", e.name));
		let n = h(e);
		t.append(b("span", "cd-hits", n === null ? "—" : String(n))), B.append(t), V.push({
			node: t,
			criterion: e
		});
	}
	R.append(z, B);
	let H = b("div", "cd-main");
	T.append(R, H);
	let U = 0, W = /* @__PURE__ */ new Map();
	async function G(e) {
		let t = ++U;
		for (let t of V) t.node.setAttribute("aria-selected", String(t.criterion === e));
		H.replaceChildren();
		let n = e.question ?? {};
		H.append(b("p", "cd-q", n.instructions ?? "(no question recorded)")), H.append(b("div", "cd-meta", [
			e.name,
			e.model,
			n.type ?? "",
			e.recorded_at ? `recorded ${e.recorded_at}` : ""
		].filter(Boolean).join(" · ")));
		let i = e.metrics ?? {}, o = b("div", "cd-cards");
		for (let [e, t] of r) {
			let n = b("div", "cd-card");
			n.dataset.metric = e, n.append(b("div", "cd-card-v", p(i[e]))), n.append(b("div", "cd-card-k", t)), o.append(n);
		}
		H.append(o);
		let s = Object.keys(i).filter((e) => !r.some(([t]) => t === e)).sort();
		s.length && H.append(b("div", "cd-rest", `also recorded: ${s.map((e) => `${e}=${p(i[e])}`).join("  ")}`));
		let c = b("div", "cd-bar"), l = document.createElement("input");
		l.type = "range", l.min = "0", l.max = "100", l.step = "1", l.value = String(Math.round(C * 100));
		let u = b("span", "cd-count"), d = b("span", void 0, "verdict ≥"), m = b("span", "cd-count", `${l.value}%`);
		c.append(d, m, l, u), H.append(c);
		let h = b("ul", "cd-chunks");
		H.append(h);
		let v = e.answers ?? W.get(e.name);
		if (!v) {
			h.append(b("li", "cd-empty", "loading chunks…"));
			try {
				v = (await f(S, a, [
					M,
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
				if (t !== U) return;
				h.replaceChildren(), H.append(b("div", "cd-err", e.message));
				return;
			}
			W.set(e.name, v);
		}
		if (t !== U) return;
		let y = () => {
			let e = Number(l.value) / 100;
			m.textContent = `${l.value}%`;
			let t = g(v, e);
			if (u.textContent = `${t.length} of ${v.length} chunks`, h.replaceChildren(), !t.length) {
				h.append(b("li", "cd-empty", `no chunk reaches ${l.value}% — drag the slider down`));
				return;
			}
			for (let e of t) {
				let t = b("li", "cd-chunk");
				t.dataset.chunk = e.chunk;
				let n = b("div"), r = _(e), i = typeof e.score == "number" || typeof e.verdict == "number";
				n.append(b("div", "cd-score", i ? `${(r * 100).toFixed(0)}%` : String(e.verdict ?? "?"))), typeof e.confidence == "number" && n.append(b("div", "cd-conf", `conf ${e.confidence.toFixed(2)}`));
				let a = D[e.chunk];
				n.append(b("div", "cd-id", a?.n ? `#${a.n}` : e.chunk.slice(0, 6)));
				let o = b("div"), s = b("p", "cd-text", a?.text ?? `(no text for ${e.chunk})`);
				if (o.append(s), (a?.text ?? "").length > 420) {
					let e = b("button", "cd-more", "show all");
					e.addEventListener("click", () => {
						let t = s.classList.toggle("open");
						e.textContent = t ? "show less" : "show all";
					}), o.append(e);
				}
				t.append(n, o), h.append(t);
			}
		};
		l.addEventListener("input", y), y();
	}
	let K = (e) => {
		let t = e.target.closest(".cd-item");
		if (!t) return;
		let n = V.find((e) => e.node === t);
		n && G(n.criterion);
	};
	B.addEventListener("click", K);
	let q = () => {
		let e = z.value.trim().toLowerCase();
		for (let t of V) t.node.hidden = e.length > 0 && !t.criterion.name.toLowerCase().includes(e);
	};
	z.addEventListener("input", q);
	let J = null, Y = () => {
		L && x(e, {
			...n,
			text: JSON.stringify({
				format: "experiments.criteria",
				task: L.value,
				chunks: D,
				api: S,
				threshold: C
			})
		}).then((e) => {
			J = e;
		});
	};
	return L?.addEventListener("change", Y), await G(F[0]), () => {
		B.removeEventListener("click", K), z.removeEventListener("input", q), L?.removeEventListener("change", Y), J?.(), e.replaceChildren();
	};
}
//#endregion
export { a as ANSWERS_SQL, r as CARDS, i as CRITERIA_SQL, e as DEFAULT_API, n as DEFAULT_CHUNKS, t as DEFAULT_THRESHOLD, o as TASKS_SQL, g as applicable, f as ask, h as hitsOf, d as isChunkStore, x as mount, m as ordered, s as packageOf, l as parsePointer, p as show, _ as strength, c as taskPattern };
