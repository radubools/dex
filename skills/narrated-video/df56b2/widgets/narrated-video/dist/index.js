//#region skills/narrated-video/e1801d/widgets/narrated-video/src/index.ts
function e(e) {
	let t = [], n = (e) => {
		let t = e.trim().split(":").map(Number);
		return t.some(Number.isNaN) ? NaN : t.length === 3 ? t[0] * 3600 + t[1] * 60 + t[2] : t[0] * 60 + t[1];
	};
	for (let r of e.replace(/\r/g, "").split("\n\n")) {
		let e = r.split("\n").filter(Boolean), i = e.find((e) => e.includes("-->"));
		if (!i) continue;
		let [a, o] = i.split("-->"), s = n(a), c = n(o);
		if (Number.isNaN(s) || Number.isNaN(c)) continue;
		let l = e.slice(e.indexOf(i) + 1).join(" ").trim();
		l && t.push({
			start: s,
			end: c,
			text: l
		});
	}
	return t;
}
async function t(t, n) {
	let r = n.theme !== "light", i = n.path.split("/").pop() ?? "video", a = document.createElement("div");
	a.style.cssText = "display:flex;flex-direction:column;gap:10px";
	let o = document.createElement("video");
	o.controls = !0, o.playsInline = !0, o.preload = "metadata";
	let s = await n.fetchAssetUrl(n.path.split("/").pop() ?? "");
	o.src = s, o.style.cssText = `width:100%;border-radius:10px;background:${r ? "#0e1117" : "#f2f4f8"}`;
	let c = document.createElement("p");
	c.style.cssText = "margin:0;min-height:2.6em;font-size:15px;line-height:1.45;opacity:.92";
	let l = document.createElement("div");
	l.style.cssText = "display:flex;flex-wrap:wrap;gap:6px", a.append(o, c, l), t.replaceChildren(a);
	let u = [], d = i.replace(/\.(mp4|webm|mov)$/i, ".vtt");
	try {
		u = e(await n.fetchAsset(d));
	} catch {
		c.textContent = "";
	}
	let f = (e) => {
		let t = u.find((t) => e >= t.start && e <= t.end);
		c.textContent = t?.text ?? "";
	}, p = () => f(o.currentTime);
	if (o.addEventListener("timeupdate", p), u.length > 0) {
		let e = document.createElement("span");
		e.style.cssText = "font-size:12px;opacity:.6;width:100%", e.textContent = `${u.length} section${u.length === 1 ? "" : "s"} — click to replay`, l.appendChild(e), u.forEach((e, t) => {
			let n = document.createElement("button");
			n.textContent = String(t + 1), n.title = e.text, n.style.cssText = `min-width:30px;min-height:28px;border-radius:8px;cursor:pointer;
        border:1px solid ${r ? "#2a2f3a" : "#c9ced8"};
        background:${r ? "#161a21" : "#fff"};color:inherit;font:inherit;font-size:12px`, n.addEventListener("click", () => {
				o.currentTime = e.start + .01, o.play();
			}), l.appendChild(n);
		});
	}
	return () => {
		o.removeEventListener("timeupdate", p), o.pause(), o.removeAttribute("src"), o.load(), URL.revokeObjectURL(s);
	};
}
//#endregion
export { t as mount, e as parseVtt };
