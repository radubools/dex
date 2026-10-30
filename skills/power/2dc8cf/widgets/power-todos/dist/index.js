//#region skills/power/2dc8cf/widgets/power-todos/src/index.ts
var e = {
	todo: "Todo",
	queued: "Starting",
	running: "Running",
	awaiting_input: "Waiting on you",
	blocked: "Waiting on tasks",
	paused: "Paused",
	succeeded: "Done",
	failed: "Failed",
	cancelled: "Cancelled",
	archived: "Archived"
}, t = /* @__PURE__ */ new Set([
	"succeeded",
	"failed",
	"cancelled",
	"archived"
]), n = "\n.pt { font: 13px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); padding: 14px; min-height: 100%; box-sizing: border-box; }\n.pt-title { font-size: 15px; font-weight: 600; margin: 0 0 8px; }\n.pt-summary { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; margin-bottom: 6px; }\n.pt-bar { height: 4px; border-radius: 2px; background: var(--line); margin-bottom: 12px; overflow: hidden; }\n.pt-bar > i { display: block; height: 100%; background: var(--done); }\n.pt-list, .pt-list ul { list-style: none; margin: 0; padding: 0; }\n.pt-list ul { margin-left: 16px; border-left: 1px solid var(--line); padding-left: 10px; }\n.pt-row { display: flex; align-items: baseline; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }\n.pt-main { flex: 1; min-width: 0; }\n.pt-name { overflow-wrap: anywhere; }\n.pt-activity { color: var(--muted); font-size: 11px; }\n.pt-cost { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }\n.pt-chip { flex: none; font-size: 10px; padding: 1px 6px; border-radius: 9px; background: var(--line); }\n.pt-chip.running, .pt-chip.queued { background: var(--run); color: #06222e; }\n.pt-chip.succeeded { background: var(--done); color: #052e14; }\n.pt-chip.failed { background: var(--bad); color: #300; }\n.pt-chip.awaiting_input { background: var(--warn); color: #2e2100; }\n.pt-empty { color: var(--muted); }\n", r = {
	dark: "--fg:#e6e8ee;--bg:#0f1218;--muted:#8b93a3;--line:#262b35;--run:#7dd3fc;--done:#4ade80;--bad:#f87171;--warn:#fbbf24",
	light: "--fg:#1a1d24;--bg:#fff;--muted:#667085;--line:#e4e7ec;--run:#7dd3fc;--done:#4ade80;--bad:#f87171;--warn:#fbbf24"
};
function i(e, t = "", n) {
	let r = document.createElement(e);
	return t && (r.className = t), n !== void 0 && (r.textContent = n), r;
}
function a(e) {
	let t = JSON.parse(e);
	if (!t || !Array.isArray(t.todos)) throw Error("not a todo list: it has no `todos`");
	return t;
}
function o(n, r) {
	let a = i("li");
	a.dataset.state = n.state;
	let s = i("div", "pt-row"), c = i("div", "pt-main");
	c.append(i("div", "pt-name", n.title)), n.activity && !t.has(n.state) && c.append(i("div", "pt-activity", n.activity)), s.append(i("span", `pt-chip ${n.state}`, e[n.state] ?? n.state), c), n.costUsd && s.append(i("span", "pt-cost", `$${n.costUsd.toFixed(2)}`)), a.append(s);
	let l = r.get(n.id);
	if (l?.length) {
		let e = i("ul");
		e.append(...l.map((e) => o(e, r))), a.append(e);
	}
	return a;
}
async function s(e, s) {
	let c = a(s.text), l = i("div", "pt");
	l.setAttribute("style", r[s.theme === "light" ? "light" : "dark"]);
	let u = i("style");
	u.textContent = n, l.append(u, i("h1", "pt-title", c.request ?? "Todos"));
	let d = new Set(c.todos.map((e) => e.id)), f = /* @__PURE__ */ new Map();
	for (let e of c.todos) e.spawnedBy && d.has(e.spawnedBy) && f.set(e.spawnedBy, [...f.get(e.spawnedBy) ?? [], e]);
	let p = c.todos.filter((e) => !e.spawnedBy || !d.has(e.spawnedBy));
	if (c.todos.length === 0) l.append(i("p", "pt-empty", "Nothing has been added to this list yet."));
	else {
		let e = c.todos.filter((e) => t.has(e.state)).length, n = c.todos.reduce((e, t) => e + (t.costUsd ?? 0), 0), r = i("div", "pt-summary");
		r.append(i("span", "pt-count", `${e} of ${c.todos.length} done`), i("span", "pt-total", `$${n.toFixed(2)}`));
		let a = i("div", "pt-bar"), s = i("i");
		s.style.width = `${e / c.todos.length * 100}%`, a.append(s);
		let u = i("ul", "pt-list");
		u.append(...p.map((e) => o(e, f))), l.append(r, a, u);
	}
	return e.replaceChildren(l), () => e.replaceChildren();
}
//#endregion
export { s as mount, a as parseList };
