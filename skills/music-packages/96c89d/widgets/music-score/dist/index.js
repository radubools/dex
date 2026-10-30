//#region skills/music-packages/a0b070/widgets/music-score/src/smf.ts
var e = class {
	offset = 0;
	data;
	constructor(e) {
		this.data = e;
	}
	byte() {
		if (this.offset >= this.data.length) throw Error("unexpected end of MIDI data");
		return this.data[this.offset++];
	}
	bytes(e) {
		let t = this.data.subarray(this.offset, this.offset + e);
		if (t.length < e) throw Error("unexpected end of MIDI data");
		return this.offset += e, t;
	}
	uint32() {
		let e = this.bytes(4);
		return (e[0] << 24 >>> 0) + (e[1] << 16) + (e[2] << 8) + e[3];
	}
	uint16() {
		let e = this.bytes(2);
		return (e[0] << 8) + e[1];
	}
	varint() {
		let e = 0;
		for (let t = 0; t < 4; t++) {
			let t = this.byte();
			if (e = e << 7 | t & 127, !(t & 128)) return e;
		}
		throw Error("malformed variable-length quantity");
	}
	ascii(e) {
		return String.fromCharCode(...this.bytes(e));
	}
};
function t(t) {
	let n = new e(t);
	if (n.ascii(4) !== "MThd") throw Error("not a MIDI file: no MThd header");
	let r = n.uint32(), i = n.uint16(), a = n.uint16(), o = n.uint16();
	if (n.offset += Math.max(0, r - 6), o & 32768) throw Error("SMPTE-timecode MIDI is not supported; use ticks per quarter note");
	if (o === 0) throw Error("MIDI header declares zero ticks per quarter note");
	let s = [], c = [], l = [], u = 0, d = 0;
	for (let e = 0; e < a && n.offset < t.length; e++) {
		let t = n.ascii(4), r = n.uint32();
		if (t !== "MTrk") {
			n.offset += r, e--;
			continue;
		}
		let i = n.offset + r, a = {
			name: "",
			notes: []
		}, o = /* @__PURE__ */ new Map(), f = 0, p = 0;
		for (; n.offset < i;) {
			f += n.varint();
			let e = n.byte();
			if (e < 128) {
				if (p === 0) throw Error("running status with no preceding status byte");
				n.offset--, e = p;
			} else e < 240 && (p = e);
			if (e === 255) {
				let e = n.byte(), t = n.bytes(n.varint());
				e === 81 && t.length === 3 ? c.push({
					tick: f,
					usPerQuarter: (t[0] << 16) + (t[1] << 8) + t[2]
				}) : e === 88 && t.length >= 2 ? l.push({
					tick: f,
					numerator: t[0],
					denominator: 2 ** t[1]
				}) : e === 89 && t.length >= 1 ? u = t[0] > 127 ? t[0] - 256 : t[0] : (e === 3 || e === 4) && !a.name && (a.name = String.fromCharCode(...t).trim());
				continue;
			}
			if (e === 240 || e === 247) {
				n.offset += n.varint();
				continue;
			}
			let t = e & 240, r = e & 15;
			if (t === 144 || t === 128) {
				let e = n.byte(), i = n.byte(), s = r * 128 + e;
				if (t === 144 && i > 0) {
					let t = {
						tick: f,
						durTicks: 0,
						pitch: e,
						velocity: i,
						channel: r
					}, n = o.get(s);
					n ? n.push(t) : o.set(s, [t]), a.notes.push(t);
				} else {
					let e = o.get(s), t = e?.shift();
					t && (t.durTicks = Math.max(1, f - t.tick)), e && e.length === 0 && o.delete(s);
				}
			} else t === 192 || t === 208 || n.byte(), n.byte();
		}
		for (let e of o.values()) for (let t of e) t.durTicks = Math.max(1, f - t.tick);
		n.offset = i, a.notes.sort((e, t) => e.tick - t.tick || e.pitch - t.pitch), d = Math.max(d, f), s.push(a);
	}
	return c.sort((e, t) => e.tick - t.tick), l.sort((e, t) => e.tick - t.tick), {
		format: i,
		division: o,
		tracks: s,
		tempos: c,
		timeSigs: l,
		keySf: u,
		endTick: d
	};
}
function n(e) {
	let t = [], n = e.tempos.length > 0 && e.tempos[0].tick === 0 ? e.tempos[0].usPerQuarter : 5e5, r = 0, i = n / 1e6 / e.division, a = 0;
	t.push({
		tick: 0,
		seconds: 0,
		secondsPerTick: i
	});
	for (let n of e.tempos) {
		if (n.tick <= 0) {
			i = n.usPerQuarter / 1e6 / e.division, t[0].secondsPerTick = i;
			continue;
		}
		r += (n.tick - a) * i, a = n.tick, i = n.usPerQuarter / 1e6 / e.division, t.push({
			tick: a,
			seconds: r,
			secondsPerTick: i
		});
	}
	return (e) => {
		let n = 0, r = t.length - 1;
		for (; n < r;) {
			let i = n + r + 1 >> 1;
			t[i].tick <= e ? n = i : r = i - 1;
		}
		let i = t[n];
		return i.seconds + (e - i.tick) * i.secondsPerTick;
	};
}
function r(e, t) {
	let n = 5e5;
	for (let r of e.tempos) {
		if (r.tick > t) break;
		n = r.usPerQuarter;
	}
	return 6e7 / n;
}
//#endregion
//#region skills/music-packages/a0b070/widgets/music-score/src/score.ts
var i = [
	0,
	0,
	1,
	1,
	2,
	3,
	3,
	4,
	4,
	5,
	5,
	6
], a = [
	0,
	1,
	0,
	1,
	0,
	0,
	1,
	0,
	1,
	0,
	1,
	0
], o = [
	0,
	1,
	1,
	2,
	2,
	3,
	4,
	4,
	5,
	5,
	6,
	6
], s = [
	0,
	-1,
	0,
	-1,
	0,
	0,
	-1,
	0,
	-1,
	0,
	-1,
	0
], c = [
	"C",
	"D",
	"E",
	"F",
	"G",
	"A",
	"B"
];
function l(e, t) {
	let n = t < 0, r = (e % 12 + 12) % 12, l = n ? o[r] : i[r], u = n ? s[r] : a[r], d = Math.floor((e - u) / 12) - 1;
	return {
		step: d * 7 + l,
		alter: u,
		name: `${c[l]}${u > 0 ? "#" : u < 0 ? "b" : ""}${d}`
	};
}
function u(e, t) {
	let n = e.timeSigs.length > 0 && e.timeSigs[0].tick === 0 ? e.timeSigs : [{
		tick: 0,
		numerator: 4,
		denominator: 4
	}, ...e.timeSigs], r = [];
	for (let i = 0; i < n.length; i++) {
		let a = n[i], o = i + 1 < n.length ? Math.min(n[i + 1].tick, t) : t, s = e.division * 4 * a.numerator / a.denominator;
		if (s > 0) {
			for (let e = a.tick; e <= o; e += s) r.push(e);
			if (r.length > 5e3) break;
		}
	}
	return r;
}
function d(e, t) {
	let i = n(e), a = [];
	e.tracks.forEach((t, n) => {
		if (t.notes.length === 0) return;
		let r = t.notes.map((e) => e.pitch), o = Math.min(...r), s = Math.max(...r), c = t.name || (e.tracks.filter((e) => e.notes.length > 0).length > 1 ? `Track ${n + 1}` : "Score"), l = (e) => ({
			start: i(e.tick),
			end: i(e.tick + e.durTicks),
			pitch: e.pitch
		});
		if (o < 55 && s > 67 && s - o > 24) {
			let e = t.notes.filter((e) => e.pitch >= 60).map(l), n = t.notes.filter((e) => e.pitch < 60).map(l);
			e.length > 0 && a.push({
				name: c,
				clef: "treble",
				notes: e
			}), n.length > 0 && a.push({
				name: e.length > 0 ? "" : c,
				clef: "bass",
				notes: n
			});
			return;
		}
		let u = r.slice().sort((e, t) => e - t)[r.length >> 1];
		a.push({
			name: c,
			clef: u >= 58 ? "treble" : "bass",
			notes: t.notes.map(l)
		});
	});
	let o = 0;
	for (let e of a) for (let t of e.notes) o = Math.max(o, t.end);
	return o = Math.max(o, i(e.endTick)), {
		title: t,
		duration: o,
		staves: a,
		bars: u(e, e.endTick).map(i).filter((e) => e <= o + .001),
		bpm: r(e, 0),
		keySf: e.keySf
	};
}
var f = {
	dark: {
		ink: "#e7e9ee",
		rule: "#414a5a",
		faint: "#8a93a5",
		accent: "#7fd8ff",
		sounding: "#ffd479"
	},
	light: {
		ink: "#14161a",
		rule: "#b9c0cc",
		faint: "#6b7383",
		accent: "#0b6e99",
		sounding: "#c2700a"
	}
}, p = 7, m = 84, h = 16, g = 56, _ = {
	treble: 30,
	bass: 18
}, v = {
	treble: 34,
	bass: 22
}, y = (e) => e.replace(/[&<>"]/g, (e) => ({
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	"\"": "&quot;"
})[e]);
function b(e, t, n) {
	let r = Math.max(160, t - 130 - h), i = 62;
	e.duration > 0 && e.duration / (r / i) > 36 && (i = Math.max(12, 36 * r / e.duration));
	let a = r / i, o = e.staves.map((t) => {
		let n = 0, r = 0;
		for (let i of t.notes) {
			let { step: a } = l(i.pitch, e.keySf), o = _[t.clef];
			n = Math.max(n, (a - (o + 8)) * (p / 2)), r = Math.max(r, (o - a) * (p / 2));
		}
		return {
			above: Math.min(Math.max(n + p * 2.6, 21), 84),
			below: Math.min(Math.max(r + p * 2.6, 21), 84)
		};
	}), s = o.reduce((e, t) => e + t.above + g + t.below, 0) + 14, c = [], u = 0, d = 0;
	for (; u < e.duration - 1e-6 && d++ < 500;) {
		let t = u + a, n = e.bars.filter((e) => e > u + a * .35 && e <= t + 1e-6).pop();
		n !== void 0 && (t = n), t <= u && (t = u + a), c.push({
			t0: u,
			t1: Math.min(t, e.duration),
			top: 0,
			bottom: 0
		}), u = t;
	}
	c.length === 0 && c.push({
		t0: 0,
		t1: Math.max(e.duration, 1),
		top: 0,
		bottom: 0
	});
	let f = [], v = 8;
	c.forEach((t, r) => {
		t.top = v;
		let a = (t.t1 - t.t0) * i, l = 130 + Math.max(a, 2);
		f.push(`<text x="4" y="${(v + 11).toFixed(1)}" fill="${n.faint}" font-size="10" font-family="ui-monospace,monospace">${C(t.t0)}</text>`);
		let u = v + 12;
		if (e.staves.forEach((r, a) => {
			let s = o[a], c = u + s.above, d = c + g;
			for (let e = 0; e < 5; e++) {
				let t = (c + e * p * 2).toFixed(1);
				f.push(`<line x1="90" y1="${t}" x2="${l.toFixed(1)}" y2="${t}" stroke="${n.rule}" stroke-width="1"/>`);
			}
			r.name && f.push(`<text x="${m}" y="${(c + g / 2 + 4).toFixed(1)}" text-anchor="end" fill="${n.faint}" font-size="11">${y(r.name)}</text>`), f.push(S(r.clef, 94, c));
			for (let r of e.bars) {
				if (r <= t.t0 + 1e-6 || r > t.t1 + 1e-6) continue;
				let e = (130 + (r - t.t0) * i - 3).toFixed(1);
				f.push(`<line x1="${e}" y1="${c.toFixed(1)}" x2="${e}" y2="${d.toFixed(1)}" stroke="${n.rule}" stroke-width="1"/>`);
			}
			f.push(`<line x1="90" y1="${c.toFixed(1)}" x2="90" y2="${d.toFixed(1)}" stroke="${n.rule}" stroke-width="1.4"/>`);
			for (let a of r.notes) a.end <= t.t0 + 1e-6 || a.start >= t.t1 - 1e-6 || f.push(x(a, r.clef, e, t, 130, i, d, n));
			u = d + s.below;
		}), r === c.length - 1) {
			let e = v + 12 + o[0].above, t = u - o[o.length - 1].below;
			f.push(`<line x1="${(l - 3).toFixed(1)}" y1="${e.toFixed(1)}" x2="${(l - 3).toFixed(1)}" y2="${t.toFixed(1)}" stroke="${n.rule}" stroke-width="1"/>`, `<line x1="${l.toFixed(1)}" y1="${e.toFixed(1)}" x2="${l.toFixed(1)}" y2="${t.toFixed(1)}" stroke="${n.rule}" stroke-width="2.4"/>`);
		}
		t.bottom = v + s, v += s;
	}), f.push(`<line class="cursor" x1="0" y1="0" x2="0" y2="0" stroke="${n.accent}" stroke-width="1.6" stroke-linecap="round" opacity="0.9"/>`);
	let b = Math.ceil(v + 6);
	return {
		svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${t}" height="${b}" viewBox="0 0 ${t} ${b}" font-family="ui-sans-serif,system-ui,sans-serif">${f.join("")}</svg>`,
		systems: c,
		x0: 130,
		pxPerSecond: i,
		width: t,
		height: b
	};
}
function x(e, t, n, r, i, a, o, s) {
	let { step: c, alter: u } = l(e.pitch, n.keySf), d = _[t], f = o - (c - d) * (p / 2), m = e.start < r.t0, h = i + Math.max(0, e.start - r.t0) * a, g = p * .66, y = p * .5, b = 60 / (n.bpm || 120), x = e.end - e.start >= b * 1.9, S = [], C = i + (Math.min(e.end, r.t1) - r.t0) * a, w = m ? h : h + g * .8;
	if (C > w + 1 && S.push(`<rect x="${w.toFixed(1)}" y="${(f - 1.1).toFixed(1)}" width="${(C - w).toFixed(1)}" height="2.2" rx="1.1" fill="${s.ink}" opacity="0.22"/>`), m) return `<g class="note held" data-s="${e.start.toFixed(4)}" data-e="${e.end.toFixed(4)}">${S.join("")}</g>`;
	for (let e = d + 10; e <= c; e += 2) {
		let t = (o - (e - d) * (p / 2)).toFixed(1);
		S.push(`<line x1="${(h - g * 1.7).toFixed(1)}" y1="${t}" x2="${(h + g * 1.7).toFixed(1)}" y2="${t}" stroke="${s.rule}" stroke-width="1"/>`);
	}
	for (let e = d - 2; e >= c; e -= 2) {
		let t = (o - (e - d) * (p / 2)).toFixed(1);
		S.push(`<line x1="${(h - g * 1.7).toFixed(1)}" y1="${t}" x2="${(h + g * 1.7).toFixed(1)}" y2="${t}" stroke="${s.rule}" stroke-width="1"/>`);
	}
	u !== 0 && !m && S.push(`<text x="${(h - g - 3).toFixed(1)}" y="${(f + 4).toFixed(1)}" text-anchor="end" fill="${s.ink}" font-size="13">${u > 0 ? "♯" : "♭"}</text>`);
	let T = c < v[t], E = (h + (T ? g * .92 : -4.2504)).toFixed(1), D = (f + (T ? -7 * 3.3 : p * 3.3)).toFixed(1);
	return S.push(`<line x1="${E}" y1="${f.toFixed(1)}" x2="${E}" y2="${D}" stroke="${s.ink}" stroke-width="1.3"/>`), S.push(`<ellipse cx="${h.toFixed(1)}" cy="${f.toFixed(1)}" rx="${g}" ry="${y}" transform="rotate(-20 ${h.toFixed(1)} ${f.toFixed(1)})" fill="${x ? "none" : s.ink}" stroke="${s.ink}" stroke-width="${x ? 1.5 : .8}"/>`), `<g class="note" data-s="${e.start.toFixed(4)}" data-e="${e.end.toFixed(4)}">${S.join("")}</g>`;
}
function S(e, t, n) {
	let r = e === "treble" ? n + p * 6.6 : n + p * 3.4, i = e === "treble" ? p * 7.2 : p * 4.8;
	return `<text x="${t}" y="${r.toFixed(1)}" font-size="${i.toFixed(1)}" fill="currentColor" font-family="serif">${e === "treble" ? "𝄞" : "𝄢"}</text>`;
}
function C(e) {
	(!Number.isFinite(e) || e < 0) && (e = 0);
	let t = Math.floor(e);
	return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}
function w(e, t) {
	let n = e.systems.find((e) => t >= e.t0 && t < e.t1) ?? (t >= (e.systems.at(-1)?.t1 ?? 0) ? e.systems.at(-1) : e.systems[0]);
	if (!n) return null;
	let r = Math.min(Math.max(t, n.t0), n.t1);
	return {
		x: e.x0 + (r - n.t0) * e.pxPerSecond,
		top: n.top + 10,
		bottom: n.bottom - 4
	};
}
function T(e, t, n) {
	let r = e.systems.find((e) => n >= e.top && n < e.bottom);
	if (!r) return null;
	let i = r.t0 + (t - e.x0) / e.pxPerSecond;
	return Math.min(Math.max(i, r.t0), r.t1);
}
//#endregion
//#region skills/music-packages/a0b070/widgets/music-score/src/index.ts
var E = [
	".wav",
	".mp3",
	".ogg",
	".flac",
	".m4a"
];
async function D(e, t) {
	let n = await e.fetchAssetUrl(t);
	try {
		return new Uint8Array(await (await fetch(n)).arrayBuffer());
	} finally {
		URL.revokeObjectURL(n);
	}
}
async function O(e, n) {
	let r = n.theme !== "light", i = f[r ? "dark" : "light"], a = n.path.split("/").pop() ?? "", o = a.replace(/\.score\.json$/i, "").replace(/\.json$/i, ""), s = {};
	try {
		s = JSON.parse(n.text);
	} catch {
		throw Error(`${a} is not valid JSON`);
	}
	let c = s.midi ?? `${o}.mid`, l = d(t(await D(n, c)), s.title ?? o.replace(/[-_]+/g, " "));
	if (l.staves.length === 0) throw Error(`${c} contains no notes`);
	let u = null, p = "";
	for (let e of s.audio ? [s.audio] : E.map((e) => o + e)) try {
		u = await n.fetchAssetUrl(e), p = e;
		break;
	} catch {}
	let m = document.createElement("div");
	m.style.cssText = "display:flex;flex-direction:column;gap:10px";
	let h = document.createElement("div");
	h.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap";
	let g = document.createElement("button");
	g.type = "button", g.textContent = "▶", g.setAttribute("aria-label", "Play"), g.style.cssText = `width:34px;height:34px;border-radius:999px;cursor:pointer;font:inherit;font-size:13px;
    border:1px solid ${i.rule};background:${r ? "#161a21" : "#fff"};color:inherit`, g.disabled = u === null;
	let _ = document.createElement("div");
	_.style.cssText = `position:relative;flex:1;min-width:140px;height:6px;border-radius:999px;cursor:pointer;
    background:${r ? "#232833" : "#e4e8ef"}`;
	let v = document.createElement("div");
	v.style.cssText = `position:absolute;inset:0 100% 0 0;border-radius:999px;background:${i.accent}`, _.appendChild(v);
	let y = document.createElement("span");
	y.style.cssText = "font:12px ui-monospace,monospace;opacity:.75;min-width:84px;text-align:right";
	let x = document.createElement("div");
	x.style.cssText = "display:flex;gap:10px;align-items:baseline;flex-wrap:wrap";
	let S = document.createElement("strong");
	S.style.cssText = "font-size:14px", S.textContent = l.title;
	let O = document.createElement("span");
	O.style.cssText = "font-size:12px;opacity:.6", O.textContent = `${Math.round(l.bpm)} bpm · ${l.staves.length} stave${l.staves.length === 1 ? "" : "s"} · ${C(l.duration)}`;
	let k = document.createElement("span");
	k.style.cssText = "font-size:12px;color:#e0a33a", x.append(S, O, k), h.append(g, _, y);
	let A = document.createElement("div");
	A.style.cssText = `max-height:460px;overflow:auto;border-radius:10px;color:${i.ink};
    background:${r ? "#10141b" : "#fff"};border:1px solid ${i.rule}`;
	let j = document.createElement("div");
	j.style.cssText = "font-size:11px;opacity:.5", j.textContent = u ? `click the score to seek · ${p}` : "no rendered audio beside this file — score only", m.append(x, h, A, j), e.replaceChildren(m);
	let M = b(l, 900, i), N = null, P = [], F = /* @__PURE__ */ new Set(), I = () => {
		let t = Math.max(360, Math.floor(A.clientWidth || e.clientWidth || 900) - 2);
		M = b(l, t, i), A.innerHTML = M.svg, N = A.querySelector(".cursor"), P = [...A.querySelectorAll("g.note")].map((e) => ({
			el: e,
			start: Number(e.dataset.s),
			end: Number(e.dataset.e)
		})), F = /* @__PURE__ */ new Set(), z(L());
	}, L = () => B ? B.currentTime : 0, R = -1, z = (e) => {
		let t = w(M, e);
		if (!N || !t) return;
		N.setAttribute("x1", t.x.toFixed(1)), N.setAttribute("x2", t.x.toFixed(1)), N.setAttribute("y1", t.top.toFixed(1)), N.setAttribute("y2", t.bottom.toFixed(1));
		for (let t of P) {
			let n = e >= t.start && e < t.end;
			if (n !== F.has(t.el)) {
				if (n) {
					F.add(t.el), t.el.setAttribute("fill", i.sounding), t.el.style.color = i.sounding;
					for (let e of t.el.children) e.getAttribute("fill") && e.getAttribute("fill") !== "none" && e.setAttribute("fill", i.sounding), e.getAttribute("stroke") && e.setAttribute("stroke", i.sounding);
				} else {
					F.delete(t.el), t.el.removeAttribute("fill");
					for (let e of t.el.children) e.getAttribute("fill") && e.getAttribute("fill") !== "none" && e.setAttribute("fill", i.ink), e.getAttribute("stroke") && e.setAttribute("stroke", e.tagName === "line" && e.getAttribute("stroke-width") === "1" ? i.rule : i.ink);
				}
			}
		}
		t.top !== R && (R = t.top, (t.bottom > A.scrollTop + A.clientHeight || t.top < A.scrollTop) && (A.scrollTop = Math.max(0, t.top - 12)));
		let n = V();
		y.textContent = `${C(e)} / ${C(n)}`, v.style.right = `${(100 - (n > 0 ? Math.min(e / n, 1) * 100 : 0)).toFixed(2)}%`;
	}, B = u ? new Audio(u) : null;
	B && (B.preload = "metadata");
	let V = () => B && Number.isFinite(B.duration) && B.duration > 0 ? B.duration : l.duration, H = 0, U = () => {
		z(L()), H = requestAnimationFrame(U);
	}, W = () => {
		H ||= requestAnimationFrame(U);
	}, G = () => {
		cancelAnimationFrame(H), H = 0;
	}, K = () => {
		g.textContent = "❚❚", g.setAttribute("aria-label", "Pause"), W();
	}, q = () => {
		g.textContent = "▶", g.setAttribute("aria-label", "Play"), G(), z(L());
	};
	g.addEventListener("click", () => {
		B && (B.paused ? B.play() : B.pause());
	}), B?.addEventListener("play", K), B?.addEventListener("pause", q), B?.addEventListener("ended", q), B?.addEventListener("seeked", () => z(L())), B?.addEventListener("timeupdate", () => {
		B.paused && z(L());
	}), B?.addEventListener("loadedmetadata", () => {
		let e = l.duration, t = B ? B.duration : e;
		Number.isFinite(t) && t < e - .35 ? k.textContent = `audio ends at ${C(t)}, score runs to ${C(e)} — the render is short` : Number.isFinite(t) && t > e + Math.max(3, e * .15) && (k.textContent = `audio ${C(t)} vs MIDI ${C(e)} — they may have drifted apart`), z(L());
	});
	let J = (e) => {
		B && (B.currentTime = Math.min(Math.max(e, 0), Math.max(V() - .01, 0)), z(B.currentTime));
	};
	_.addEventListener("click", (e) => {
		let t = _.getBoundingClientRect();
		J((e.clientX - t.left) / t.width * V());
	}), A.addEventListener("click", (e) => {
		let t = A.querySelector("svg");
		if (!t) return;
		let n = t.getBoundingClientRect(), r = T(M, e.clientX - n.left, e.clientY - n.top);
		r !== null && J(r);
	}), I();
	let Y = 0, X = new ResizeObserver(() => {
		let e = Math.floor(A.clientWidth);
		Math.abs(e - Y) < 8 || (Y = e, R = -1, I());
	});
	return X.observe(A), () => {
		G(), X.disconnect(), B?.pause(), u && URL.revokeObjectURL(u);
	};
}
//#endregion
export { O as mount };
