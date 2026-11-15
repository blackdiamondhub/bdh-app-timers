/* Timers dial ("Hybrid 2": metal bezel, minute ticks, glowing progress ring).
   PERFORMANCE: the hub displays have weak GPUs. Dials are redrawn at most once
   a second and nothing animates continuously. */
(function () {
  const COLORS = {
    run: ["#5ba4e6", "#7ce0ff"], pause: ["#f0b44c", "#ffd98a"],
    ring: ["#ff6a4c", "#ffb35c"], done: ["#5fcf8f", "#9be8b9"],
  };
  const pad = n => String(n).padStart(2, "0");
  function fmt(s) {
    s = Math.max(0, Math.ceil(s));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return h ? `${h}:${pad(m)}:${pad(x)}` : `${m}:${pad(x)}`;
  }
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  let gid = 0;

  // t: {name, state, left, total, at, ends}
  function dial(t, size) {
    const id = "tmg" + (gid++), [a, b] = COLORS[t.state] || COLORS.run, c = 130, r = 92, C = 2 * Math.PI * r;
    const over = t.state === "ring" || t.state === "done";
    const f = over ? 1 : Math.max(0, Math.min(1, t.total ? t.left / t.total : 0));
    const ang = (f * 360 - 90) * Math.PI / 180;
    let ticks = "";
    for (let i = 0; i < 60; i++) {
      const g = i * 6 * Math.PI / 180, maj = i % 5 === 0, r1 = maj ? 110 : 114, r2 = 121, lit = i / 60 < f - 1e-9;
      ticks += `<line x1="${(c + r1 * Math.sin(g)).toFixed(1)}" y1="${(c - r1 * Math.cos(g)).toFixed(1)}" x2="${(c + r2 * Math.sin(g)).toFixed(1)}" y2="${(c - r2 * Math.cos(g)).toFixed(1)}" stroke="${lit ? b : (maj ? "#4a5d7a" : "#2c3b55")}" stroke-opacity="${lit ? (maj ? 1 : .7) : 1}" stroke-width="${maj ? 2.6 : 1.3}" stroke-linecap="round"/>`;
    }
    const sub = t.state === "ring" ? "TIME'S UP" : t.state === "done" ? "DONE" : t.state === "pause" ? "PAUSED"
      : t.at && t.ends ? `ENDS ${t.ends}` : `OF ${fmt(t.total)}`;
    const txt = over ? "0:00" : fmt(t.left);
    const arc = `cx="${c}" cy="${c}" r="${r}" fill="none" stroke="url(#${id})" stroke-width="20" stroke-linecap="round" stroke-dasharray="${(C * f).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 ${c} ${c})"`;
    return `<svg width="${size}" height="${size}" viewBox="0 0 260 260" role="img" aria-label="${esc(t.name)} ${txt}"><defs>
<linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${b}"/><stop offset="1" stop-color="${a}"/></linearGradient>
<radialGradient id="${id}f" cx=".5" cy=".38" r=".7"><stop offset="0" stop-color="#1f3052"/><stop offset="1" stop-color="#0c1424"/></radialGradient>
<linearGradient id="${id}b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a5f82"/><stop offset=".5" stop-color="#1b2840"/><stop offset="1" stop-color="#3b4e6e"/></linearGradient>
<filter id="${id}g" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7"/></filter></defs>
<circle cx="${c}" cy="${c}" r="128" fill="url(#${id}b)"/><circle cx="${c}" cy="${c}" r="124" fill="url(#${id}f)"/>${ticks}
<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="20"/>
${f > 0 ? `<circle ${arc} filter="url(#${id}g)" opacity=".75"/><circle ${arc}/>` : ""}
${f > 0 && f < 1 ? `<circle cx="${(c + r * Math.cos(ang)).toFixed(1)}" cy="${(c + r * Math.sin(ang)).toFixed(1)}" r="7" fill="#fff"/>` : ""}
<text x="${c}" y="${c + 6}" text-anchor="middle" font-size="${txt.length > 5 ? 40 : 46}" font-weight="300" fill="#eef3fa" style="font-variant-numeric:tabular-nums">${txt}</text>
<text x="${c}" y="${c + 34}" text-anchor="middle" font-size="11" letter-spacing="2" fill="${b}" font-weight="600">${esc(sub)}</text></svg>`;
  }

  function csrf() {
    const el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }
  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }

  // Keeps server state ticking locally between polls.
  function localize(state) {
    const t0 = Date.now() / 1000;
    for (const t of state.timers) t._end = t.state === "run" ? t0 + t.left : null;
    return state;
  }
  function live(t) {
    if (t._end == null) return t;
    const left = t._end - Date.now() / 1000;
    return left > 0 ? Object.assign({}, t, {left}) : Object.assign({}, t, {left: 0, state: "ring"});
  }

  window.TimersDial = {dial, fmt, esc, post, localize, live};
})();
