//#region skills/postgres-experiments-state/draft/widgets/experiments-criteria/src/index.ts
var e = "http://127.0.0.1:4317", t = .3, n = "chunks.json";
function r() {
	try {
		let e = location.ancestorOrigins, t = e && e.length ? e[0] : "";
		if (t && t !== "null") return t.replace(/\/+$/, "");
	} catch {}
	try {
		if (document.referrer) return new URL(document.referrer).origin;
	} catch {}
	return "";
}
function i(t) {
	return t ? [t.replace(/\/+$/, "")] : [...new Set([r(), e].filter(Boolean))];
}
var a = [
	["hits", "hits"],
	["lift", "lift"],
	["support", "support"],
	["mean_confidence", "mean confidence"],
	["J", "Youden's J"]
], o = "\nSELECT DISTINCT ON (s.id)\n       s.name, s.kind,\n       coalesce(h.detail->>'model',\n                CASE s.kind WHEN 'jev_criterion' THEN 'jev' ELSE 'laya' END) AS model,\n       coalesce(h.detail->'question', s.body->'question', '{}'::jsonb)       AS question,\n       h.metrics,\n       (h.metrics->>'hits')::float AS hits,\n       h.recorded_at, h.note\n  FROM experiments.state   s\n  JOIN experiments.history h ON h.state_id = s.id\n WHERE h.task = $1\n   AND s.kind IN ('laya_criterion', 'jev_criterion')\n ORDER BY s.id,\n          (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n          h.recorded_at DESC, h.id DESC", s = "\nWITH latest AS (\n  SELECT h.id\n    FROM experiments.state   s\n    JOIN experiments.history h ON h.state_id = s.id\n   WHERE h.task = $1 AND s.name = $2 AND s.kind = $3\n   ORDER BY (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,\n            h.recorded_at DESC, h.id DESC\n   LIMIT 1\n), expanded AS (\n  SELECT a.key   AS chunk,\n         a.value AS raw,\n         CASE jsonb_typeof(a.value)\n              WHEN 'object' THEN a.value->>'verdict'\n              WHEN 'array'  THEN a.value->>0\n              ELSE a.value #>> '{}'\n         END AS verdict_text,\n         CASE jsonb_typeof(a.value)\n              WHEN 'object' THEN a.value->>'confidence'\n              WHEN 'array'  THEN a.value->>1\n         END AS confidence_text\n    FROM experiments.history h\n    JOIN latest l ON l.id = h.id\n    CROSS JOIN LATERAL jsonb_each(coalesce(h.detail->'answers', '{}'::jsonb)) AS a\n)\nSELECT chunk,\n       verdict_text AS verdict,\n       CASE WHEN verdict_text ~ '^-?[0-9]+(\\.[0-9]+)?$'\n            THEN verdict_text::float END AS score,\n       CASE WHEN confidence_text ~ '^-?[0-9]+(\\.[0-9]+)?$'\n            THEN confidence_text::float END AS confidence,\n       CASE WHEN jsonb_typeof(raw) = 'object' THEN raw->'probabilities' END AS probabilities\n  FROM expanded\n ORDER BY score DESC NULLS LAST, confidence DESC NULLS LAST, chunk", c = "\nSELECT h.task,\n       coalesce(h.detail->>'model',\n                CASE s.kind WHEN 'jev_criterion' THEN 'jev' ELSE 'laya' END) AS model,\n       count(DISTINCT s.id) AS criteria,\n       count(*)             AS entries,\n       max(h.recorded_at)   AS last_run\n  FROM experiments.history h\n  JOIN experiments.state   s ON s.id = h.state_id\n WHERE s.kind IN ('laya_criterion', 'jev_criterion')\n   AND h.task ~ $1\n GROUP BY h.task, 2\n ORDER BY criteria DESC, entries DESC, h.task";
function l(e) {
	let t = e.split("/").filter(Boolean);
	return t.length >= 2 ? t[t.length - 2] : "";
}
function u(e) {
	return `(^|/)${e.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[/#])`;
}
function d(e) {
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
var f = /^[0-9a-f]{8,}$/;
function p(e) {
	if (e.task || Array.isArray(e.criteria)) return !1;
	let t = e.chunks;
	if (t && typeof t == "object") return !0;
	let n = Object.keys(e);
	return n.length > 0 && n.every((e) => f.test(e));
}
async function m(e, t, n = [], r = 2e4) {
	let i = (Array.isArray(e) ? e : [e]).map((e) => e.replace(/\/+$/, "")), a = h && i.includes(h) ? [h, ...i.filter((e) => e !== h)] : i, o = `/api/sql?sql=${encodeURIComponent(t)}&params=${encodeURIComponent(JSON.stringify(n))}&limit=${r}`, s = [];
	for (let e of a) {
		let t;
		try {
			t = await fetch(e + o);
		} catch (t) {
			s.push(`${e} (${t.message})`);
			continue;
		}
		h = e;
		let n = await t.json().catch(() => ({}));
		if (!t.ok) throw Error(n.error ?? `/api/sql returned ${t.status}`);
		return n.rows ?? [];
	}
	throw Error(`cannot reach /api/sql. Tried: ${s.join("; ")}. Is dex running, and is the /api/sql router installed? If dex is open under a different name, put {"api": "<origin>"} in the file.`);
}
var h = "";
function g(e) {
	return e == null || e === "" ? "—" : typeof e == "number" ? Number.isFinite(e) ? Number.isInteger(e) ? String(e) : e.toFixed(e < 1 ? 4 : 3) : "—" : typeof e == "boolean" ? e ? "yes" : "no" : String(e);
}
function _(e) {
	return [...e].sort((e, t) => (v(t) ?? -1) - (v(e) ?? -1) || e.name.localeCompare(t.name));
}
function v(e) {
	for (let t of [e.hits, e.metrics?.hits]) if (typeof t == "number" && Number.isFinite(t)) return t;
	return null;
}
function ee(e, t) {
	return e.filter((e) => y(e) >= t).sort((e, t) => y(t) - y(e) || e.chunk.localeCompare(t.chunk));
}
function y(e) {
	return typeof e.score == "number" && Number.isFinite(e.score) ? e.score : typeof e.verdict == "number" && Number.isFinite(e.verdict) ? e.verdict : typeof e.confidence == "number" && Number.isFinite(e.confidence) ? e.confidence : -1;
}
var te = "\n/* One column, top to bottom: heading, the three choosers, then the detail.\n   It used to be a 260px sidebar beside a detail pane, which on a phone in\n   portrait left the criterion list about 40% of a narrow screen and the chunk\n   text unreadable beside it. A single column costs a wide screen nothing --\n   the cards and the chunk rows are what want the width, and they still get\n   all of it. */\n.cd { font: 14px/1.5 system-ui, sans-serif; display: flex; flex-direction: column;\n  gap: 12px; align-items: stretch; }\n.cd-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px;\n  padding-bottom: 10px; border-bottom: 1px solid var(--cd-line); }\n.cd-task { font-size: 16px; font-weight: 650; margin: 0; font-family: ui-monospace, monospace; }\n.cd-chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--cd-line);\n  text-transform: uppercase; letter-spacing: .05em; }\n.cd-chip.laya { border-color: var(--cd-laya); color: var(--cd-laya); }\n.cd-chip.jev { border-color: var(--cd-jev); color: var(--cd-jev); }\n.cd-note { font-size: 12.5px; padding: 8px 10px; border-radius: 6px;\n  border: 1px solid var(--cd-warn-line); background: var(--cd-warn-bg); margin-bottom: 10px; }\n/* The choosers. A row on a wide screen, a stack on a narrow one: flex-wrap\n   plus a flex-basis does that with no media query, so it also adapts to the\n   resizable frame dex gives a widget, not just to phone widths.\n   (No backticks in here -- this string is a template literal.) */\n.cd-controls { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; }\n.cd-field { display: flex; flex-direction: column; gap: 3px; flex: 1 1 220px; min-width: 0; }\n/* The display:flex above beats the UA stylesheet's rule for [hidden], so the\n   hidden attribute does nothing at all without this line. */\n.cd-field[hidden] { display: none; }\n.cd-field.cd-wide { flex: 1 1 100%; }\n.cd-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }\n.cd-select, .cd-filter { width: 100%; box-sizing: border-box; padding: 6px 8px;\n  border-radius: 6px; border: 1px solid var(--cd-line); background: transparent;\n  color: inherit; font: inherit; font-size: 13px; }\n/* A native select is the point: on a phone it opens the platform picker, which\n   scrolls 243 options far better than a custom list ever will. */\n.cd-select { font-family: ui-monospace, monospace; }\n.cd-select option { font-family: ui-monospace, monospace; }\n.cd-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }\n.cd-q { margin: 0; font-size: 15px; }\n.cd-meta { font-size: 11.5px; opacity: .6; font-family: ui-monospace, monospace; }\n.cd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }\n.cd-card { border: 1px solid var(--cd-line); border-radius: 8px; padding: 8px 10px; }\n.cd-card-v { font-size: 19px; font-weight: 650; font-variant-numeric: tabular-nums; }\n.cd-card-k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }\n.cd-rest { font-size: 11.5px; opacity: .7; font-family: ui-monospace, monospace; word-break: break-all; }\n.cd-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 13px; }\n.cd-bar input[type=range] { flex: 1 1 160px; }\n.cd-count { font-variant-numeric: tabular-nums; }\n.cd-chunks { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow-y: auto;\n  border: 1px solid var(--cd-line); border-radius: 8px; }\n.cd-chunk { display: grid; grid-template-columns: minmax(54px, 74px) 1fr; gap: 0 10px;\n  padding: 9px 11px;\n  border-bottom: 1px solid var(--cd-line-soft); }\n.cd-chunk:last-child { border-bottom: 0; }\n.cd-score { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 650;\n  font-variant-numeric: tabular-nums; }\n.cd-conf { font-family: ui-monospace, monospace; font-size: 10.5px; opacity: .55; }\n.cd-id { font-family: ui-monospace, monospace; font-size: 10px; opacity: .4; }\n.cd-text { margin: 0; white-space: pre-wrap; font-size: 13px; max-height: 8.4em; overflow: hidden;\n  position: relative; }\n.cd-text.open { max-height: none; }\n.cd-more { font-size: 11px; opacity: .6; cursor: pointer; background: none; border: 0;\n  color: inherit; padding: 2px 0; text-align: left; }\n.cd-empty { padding: 14px; opacity: .6; font-size: 13px; }\n.cd-err { color: var(--cd-alarm); font-family: ui-monospace, monospace; font-size: 12px;\n  white-space: pre-wrap; }\n", ne = {
	dark: "--cd-line:#2a2f3a; --cd-line-soft:#20242c; --cd-hover:#1b1f27; --cd-sel:#243043;\n         --cd-laya:#7dd3fc; --cd-jev:#c4b5fd; --cd-alarm:#e0736b;\n         --cd-warn-line:#5a3b1c; --cd-warn-bg:#2a1f12;",
	light: "--cd-line:#d9dde4; --cd-line-soft:#ecEEF2; --cd-hover:#f3f5f8; --cd-sel:#e3ecfa;\n          --cd-laya:#0369a1; --cd-jev:#6d28d9; --cd-alarm:#b3372c;\n          --cd-warn-line:#e3c99a; --cd-warn-bg:#fdf6e8;"
}, b = (e, t, n) => {
	let r = document.createElement(e);
	return t && (r.className = t), n !== void 0 && (r.textContent = n), r;
};
async function re(e, n) {
	let r = d(n.text), f = i(r.api), ie = typeof r.threshold == "number" ? r.threshold : t, ae = n.theme !== "light", x = b("div", "cd");
	x.setAttribute("style", ne[ae ? "dark" : "light"]);
	let S = b("style");
	S.textContent = te, e.replaceChildren(S, x);
	let C = {}, w = "", T = (e, t = []) => {
		for (let [t, n] of Object.entries(e ?? {})) C[t] = typeof n == "string" ? { text: n } : n ?? { text: "" };
		t.forEach((e, t) => {
			C[e] && C[e].n === void 0 && (C[e].n = t + 1);
		});
	}, oe = p(r);
	if (oe) {
		let e = r;
		T(e.chunks ?? e, e.meta?.order ?? []);
	} else if (r.chunks && typeof r.chunks == "object") T(r.chunks);
	else {
		let e = r.chunks ?? "chunks.json";
		try {
			let t = JSON.parse(await n.fetchAsset(e));
			T(t?.chunks ?? t, t?.meta?.order ?? []);
		} catch (t) {
			w = `no chunk text (${e}: ${t.message}) — showing ids only`;
		}
	}
	let E = Array.isArray(r.criteria), D = r.task, O = [];
	if (!E) {
		if (!D && !oe) throw Error("not a criteria file: needs a \"task\" to query, or a \"criteria\" array, or the chunk map itself");
		let e = l(n.path);
		if (e && (O = (await m(f, c, [u(e)], 400)).map((e) => ({
			task: String(e.task),
			model: String(e.model ?? ""),
			criteria: Number(e.criteria)
		}))), !D) {
			if (!e) throw Error(`cannot tell which package ${n.path} belongs to`);
			if (!O.length) throw Error(`no Laya or Jev criterion in experiments.history names the package "${e}". Nothing has been scored here yet, or the run recorded a different task name.`);
			D = O[0].task;
		}
	}
	let k = (e) => [...new Set(O.filter((t) => t.task === e).map((e) => e.model))].sort(), A = r.model ?? (k(D).length > 1 ? "" : k(D)[0] ?? ""), j;
	if (j = E ? r.criteria : (await m(f, o, [D])).map((e) => ({
		name: String(e.name),
		kind: String(e.kind),
		model: String(e.model ?? ""),
		question: e.question ?? {},
		metrics: e.metrics ?? {},
		hits: typeof e.hits == "number" ? e.hits : null,
		recorded_at: e.recorded_at ? String(e.recorded_at) : void 0,
		note: e.note ? String(e.note) : null
	})), !j.length) throw Error(`no criterion in experiments.state was scored by task "${D ?? ""}"`);
	let M = _(j), N = b("div", "cd-head");
	N.append(b("h1", "cd-task", D ?? n.path.split("/").pop() ?? "criteria")), N.append(b("span", "cd-chip", E ? "from the file" : "live")), x.append(N), w && x.append(b("div", "cd-note", w));
	let P = (e, t = !1) => {
		let n = b("div", `cd-field${t ? " cd-wide" : ""}`), r = document.createElement("select");
		return r.className = "cd-select", r.dataset.role = e, r.setAttribute("aria-label", e), n.append(b("div", "cd-label", e), r), {
			wrap: n,
			select: r
		};
	}, F = (e, t, n) => {
		e.replaceChildren();
		for (let [r, i] of t) {
			let t = document.createElement("option");
			t.value = r, t.textContent = i, t.selected = r === n, e.append(t);
		}
	}, I = b("div", "cd-controls"), L = [...new Set(O.map((e) => e.model))].sort(), { wrap: se, select: R } = P("model"), z = (e) => O.filter((t) => t.model === e).reduce((e, t) => e + t.criteria, 0);
	if (L.length > 1) {
		let e = k(D), t = L.map((e) => [e, `${e}  (${z(e)})`]);
		e.length > 1 && t.unshift(["", `both  (${e.reduce((e, t) => e + z(t), 0)})`]), F(R, t, A), I.append(se);
	}
	let { wrap: B, select: V } = P("round", !0), H = (e) => {
		let t = O.filter((t) => !e || t.model === e), n = /* @__PURE__ */ new Map();
		for (let e of t) n.set(e.task, (n.get(e.task) ?? 0) + e.criteria);
		return [...n.entries()].sort((e, t) => t[1] - e[1] || e[0].localeCompare(t[0]));
	}, U = (e, t) => {
		let n = H(e);
		return F(V, n.map(([e, t]) => [e, `${e}  (${t})`]), t), B.hidden = new Set(O.map((e) => e.task)).size < 2, n;
	};
	O.length && (U(A, D ?? ""), I.append(B));
	let { wrap: W, select: G } = P("criterion", !0), K = b("div", "cd-field cd-wide"), q = document.createElement("input");
	q.className = "cd-filter", q.type = "search", q.dataset.role = "filter", K.append(b("div", "cd-label", "filter names"), q), I.append(W, K), x.append(I);
	let J = b("div", "cd-main");
	x.append(J);
	let Y = [], X = (e) => {
		let t = R.value, n = q.value.trim().toLowerCase();
		Y = M.filter((e) => (!t || e.model === t) && (!n || e.name.toLowerCase().includes(n))), q.placeholder = `${Y.length} of ${M.length} names`, F(G, Y.map((e) => {
			let t = v(e);
			return [e.name, `${e.name}  ${t === null ? "—" : t}`];
		}), e && Y.some((t) => t.name === e) ? e : Y[0]?.name ?? ""), W.hidden = Y.length === 0;
	}, Z = 0, ce = /* @__PURE__ */ new Map();
	async function le(e) {
		let t = ++Z;
		G.value !== e.name && (G.value = e.name), J.replaceChildren();
		let n = e.question ?? {};
		J.append(b("p", "cd-q", n.instructions ?? "(no question recorded)")), J.append(b("div", "cd-meta", [
			e.name,
			e.model,
			n.type ?? "",
			e.recorded_at ? `recorded ${e.recorded_at}` : ""
		].filter(Boolean).join(" · ")));
		let r = e.metrics ?? {}, i = b("div", "cd-cards");
		for (let [e, t] of a) {
			let n = b("div", "cd-card");
			n.dataset.metric = e, n.append(b("div", "cd-card-v", g(r[e]))), n.append(b("div", "cd-card-k", t)), i.append(n);
		}
		J.append(i);
		let o = Object.keys(r).filter((e) => !a.some(([t]) => t === e)).sort();
		o.length && J.append(b("div", "cd-rest", `also recorded: ${o.map((e) => `${e}=${g(r[e])}`).join("  ")}`));
		let c = b("div", "cd-bar"), l = document.createElement("input");
		l.type = "range", l.min = "0", l.max = "100", l.step = "1", l.value = String(Math.round(ie * 100));
		let u = b("span", "cd-count"), d = b("span", void 0, "verdict ≥"), p = b("span", "cd-count", `${l.value}%`);
		c.append(d, p, l, u), J.append(c);
		let h = b("ul", "cd-chunks");
		J.append(h);
		let _ = e.answers ?? ce.get(e.name);
		if (!_) {
			h.append(b("li", "cd-empty", "loading chunks…"));
			try {
				_ = (await m(f, s, [
					D,
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
				if (t !== Z) return;
				h.replaceChildren(), J.append(b("div", "cd-err", e.message));
				return;
			}
			ce.set(e.name, _);
		}
		if (t !== Z) return;
		let v = () => {
			let e = Number(l.value) / 100;
			p.textContent = `${l.value}%`;
			let t = ee(_, e);
			if (u.textContent = `${t.length} of ${_.length} chunks`, h.replaceChildren(), !t.length) {
				h.append(b("li", "cd-empty", `no chunk reaches ${l.value}% — drag the slider down`));
				return;
			}
			for (let e of t) {
				let t = b("li", "cd-chunk");
				t.dataset.chunk = e.chunk;
				let n = b("div"), r = y(e), i = typeof e.score == "number" || typeof e.verdict == "number";
				n.append(b("div", "cd-score", i ? `${(r * 100).toFixed(0)}%` : String(e.verdict ?? "?"))), typeof e.confidence == "number" && n.append(b("div", "cd-conf", `conf ${e.confidence.toFixed(2)}`));
				let a = C[e.chunk];
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
		l.addEventListener("input", v), v();
	}
	let Q = () => {
		let e = Y.find((e) => e.name === G.value);
		e && le(e);
	};
	G.addEventListener("change", Q);
	let $ = () => {
		let e = G.value;
		X(e), G.value !== e && Q();
	};
	q.addEventListener("input", $);
	let ue = null, de = (t, r) => {
		re(e, {
			...n,
			text: JSON.stringify({
				format: "experiments.criteria",
				task: t,
				model: r || void 0,
				chunks: C,
				api: h || f[0],
				threshold: ie
			})
		}).then((e) => {
			ue = e;
		});
	}, fe = () => de(V.value, R.value), pe = () => {
		let e = R.value, t = H(e);
		if (t.length) {
			if (t.some(([e]) => e === D)) {
				U(e, D ?? ""), X(G.value), Q();
				return;
			}
			de(t[0][0], e);
		}
	};
	return V.addEventListener("change", fe), R.addEventListener("change", pe), X(), Y.length ? await le(Y[0]) : J.append(b("div", "cd-empty", `no ${R.value || "matching"} criterion in this round`)), () => {
		G.removeEventListener("change", Q), q.removeEventListener("input", $), V.removeEventListener("change", fe), R.removeEventListener("change", pe), ue?.(), e.replaceChildren();
	};
}
//#endregion
export { s as ANSWERS_SQL, a as CARDS, o as CRITERIA_SQL, n as DEFAULT_CHUNKS, t as DEFAULT_THRESHOLD, e as FALLBACK_API, c as TASKS_SQL, i as apiBases, ee as applicable, m as ask, v as hitsOf, p as isChunkStore, re as mount, _ as ordered, l as packageOf, r as parentOrigin, d as parsePointer, h as reached, g as show, y as strength, u as taskPattern };
