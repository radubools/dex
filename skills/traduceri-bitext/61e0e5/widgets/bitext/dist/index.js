//#region skills/traduceri-bitext/d4d703/widgets/bitext/src/index.ts
function e(e) {
	let t;
	try {
		t = JSON.parse(e);
	} catch (e) {
		throw Error(`not a bitext file: ${e.message}`);
	}
	let n = t;
	if (!n || typeof n != "object" || !Array.isArray(n.segments)) throw Error("not a bitext file: no \"segments\" array");
	if (n.segments.length === 0) throw Error("bitext file has no segments");
	for (let [e, t] of n.segments.entries()) if (typeof t?.source != "string") throw Error(`segment ${e + 1} has no "source" string`);
	return {
		...n,
		source: n.source ?? { lang: "?" },
		target: n.target ?? { lang: "?" },
		segments: n.segments
	};
}
function t(e) {
	let t = [], n = /* @__PURE__ */ new Set(), r = 0;
	for (let [i, a] of e.entries()) {
		let e = a.n;
		if (typeof e != "number" || !Number.isFinite(e)) {
			t.push(`segment at position ${i + 1} has no number`);
			continue;
		}
		n.has(e) ? t.push(`number ${e} appears more than once`) : e !== r + 1 && r !== 0 && t.push(`jumps from ${r} to ${e}`), n.add(e), r = e;
	}
	return t;
}
var n = {
	dark: "--bx-line:#2a2f3a; --bx-accent:#d08b45; --bx-accent-bg:#2a2016; --bx-ok:#4f9d69;\n         --bx-ok-bg:#16241b; --bx-alarm:#e0736b; --bx-warn-line:#5a3b1c; --bx-warn-bg:#2a1f12;",
	light: "--bx-line:#d9dde4; --bx-accent:#a2650f; --bx-accent-bg:#fdf3e3; --bx-ok:#2f7d4f;\n          --bx-ok-bg:#eef7f0; --bx-alarm:#b3372c; --bx-warn-line:#e3c99a; --bx-warn-bg:#fdf6e8;"
}, r = (e, t, n) => {
	let r = document.createElement(e);
	return t && (r.className = t), n !== void 0 && (r.textContent = n), r;
};
async function i(i, a) {
	let o = e(a.text), s = a.theme !== "light", c = r("div", "bx");
	c.setAttribute("style", n[s ? "dark" : "light"]);
	let l = r("style");
	l.textContent = "\n.bx { font: 15px/1.55 system-ui, sans-serif; display: flex; flex-direction: column; gap: 14px; }\n.bx-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; }\n.bx-title { font-size: 19px; font-weight: 650; margin: 0; }\n.bx-chip { font-size: 12px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--bx-line); }\n.bx-meta { font-size: 12px; opacity: .65; font-family: ui-monospace, monospace; word-break: break-all; }\n.bx-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 13px; }\n.bx-bar label { display: flex; align-items: center; gap: 6px; cursor: pointer; }\n.bx-warn { font-size: 13px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--bx-warn-line);\n  background: var(--bx-warn-bg); }\n.bx-segs { display: flex; flex-direction: column; gap: 4px; }\n.bx-seg { display: grid; grid-template-columns: 3.2em 1fr; gap: 0 12px; padding: 10px 0;\n  border-top: 1px solid var(--bx-line); }\n.bx-seg[hidden] { display: none; }\n.bx-n { font: 12px/1.9 ui-monospace, monospace; opacity: .5; text-align: right; }\n.bx-ref { font: 11px/1.4 ui-monospace, monospace; opacity: .35; text-align: right; word-break: break-all; }\n.bx-body { display: flex; flex-direction: column; gap: 6px; min-width: 0; }\n.bx-src, .bx-tgt { margin: 0; }\n.bx-src { opacity: .62; }\n.bx-tgt { }\n.bx-seg.bx-heading .bx-src, .bx-seg.bx-heading .bx-tgt { font-size: 17px; font-weight: 650; }\n.bx-missing { font-style: italic; color: var(--bx-alarm); opacity: .9; }\n.bx-note, .bx-sugg { margin: 2px 0 0; padding: 6px 10px; border-left: 3px solid var(--bx-accent);\n  background: var(--bx-accent-bg); font-size: 13.5px; border-radius: 0 6px 6px 0; }\n.bx-sugg { border-left-color: var(--bx-ok); background: var(--bx-ok-bg); }\n.bx-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .6;\n  display: block; margin-bottom: 2px; }\n";
	let u = `${o.source.lang ?? "?"} → ${o.target.lang ?? "?"}`, d = r("div", "bx-head");
	d.append(r("h1", "bx-title", o.title ?? a.path.split("/").pop() ?? "bitext"), r("span", "bx-chip", u), r("span", "bx-chip", o.mode ?? "translate"));
	let f = r("div", "bx-meta");
	f.textContent = `${o.source.origin ? `from ${o.source.origin}` : ""}${o.target.file ? ` · translation written to ${o.target.file}` : ""}`;
	let p = o.segments.filter((e) => e.note || e.suggestion), m = o.segments.filter((e) => !String(e.target ?? "").trim()), h = r("div", "bx-bar"), g = r("span", void 0, `${o.segments.length} segments · ${p.length} with notes · ${m.length} untranslated`);
	h.append(g);
	let _ = document.createElement("input");
	_.type = "checkbox";
	let v = r("label");
	v.append(_, r("span", void 0, "only segments needing attention")), (p.length || m.length) && h.append(v);
	let y = r("div", "bx-segs"), b = [];
	for (let e of o.segments) {
		let t = r("section", "bx-seg");
		e.kind === "heading" && t.classList.add("bx-heading"), t.dataset.n = String(e.n);
		let n = r("div");
		n.append(r("div", "bx-n", String(e.n))), e.ref && n.append(r("div", "bx-ref", e.ref));
		let i = r("div", "bx-body");
		i.append(r("p", "bx-src", e.source));
		let a = String(e.target ?? "").trim();
		i.append(a ? r("p", "bx-tgt", a) : r("p", "bx-tgt bx-missing", "— not translated —"));
		for (let [t, n, a] of [[
			"bx-note",
			"note",
			e.note
		], [
			"bx-sugg",
			"suggested",
			e.suggestion
		]]) {
			if (!a) continue;
			let e = r("div", t);
			e.append(r("span", "bx-tag", n), document.createTextNode(a)), i.append(e);
		}
		t.append(n, i), y.append(t), b.push({
			node: t,
			flagged: !!(e.note || e.suggestion || !a)
		});
	}
	c.append(d, f, h);
	let x = t(o.segments);
	x.length && c.append(r("div", "bx-warn", `segment numbering: ${x.join("; ")}`)), c.append(y);
	let S = () => {
		for (let e of b) e.node.hidden = _.checked && !e.flagged;
	};
	return _.addEventListener("change", S), i.replaceChildren(l, c), () => {
		_.removeEventListener("change", S), i.replaceChildren();
	};
}
//#endregion
export { i as mount, t as numberingProblems, e as parseBitext };
