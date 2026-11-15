(function () {
  const D = window.TimersDial, $ = id => document.getElementById(id);
  const root = $("tm"), grid = $("tm-grid"), base = root.dataset.baseUrl;
  let state = D.localize(JSON.parse($("tm-initial").textContent));
  const cards = new Map();  // id -> {el, state}

  const CONTROLS = {
    run: [["pause", "fa-pause", "", "pri"], ["add", "", "+1 min"], ["reset", "fa-undo", ""]],
    pause: [["resume", "fa-play", "", "pri"], ["add", "", "+1 min"], ["reset", "fa-undo", ""]],
    ring: [["silence", "fa-bell-slash", "Silence", "warn"], ["restart", "fa-undo", "Restart"]],
    done: [["restart", "fa-undo", "Restart"]],
  };

  function toast(msg) {
    const el = $("tm-toast");
    el.textContent = msg; el.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, 3500);
  }
  function apply(data) { state = D.localize(data); render(true); }
  async function act(url, body) {
    try { apply(await D.post(url, body)); return true; } catch (e) { toast(e.message); return false; }
  }

  // ---- cards -------------------------------------------------------------
  function controls(t) {
    return CONTROLS[t.state].map(([a, icon, label, cls]) =>
      `<button data-act="${a}" class="${cls || ""}">${icon ? `<i class="fa-solid ${icon}"></i>` : ""}${label}</button>`).join("");
  }
  function makeCard(t) {
    const el = document.createElement("div");
    el.className = "tm-card"; el.dataset.id = t.id;
    el.innerHTML = `<div class="tm-head"><span class="tm-kind"><i class="fa-solid fa-clock"></i></span>
      <span class="tm-name"></span>
      <button class="tm-x" data-act="remove" aria-label="Close"><i class="fa-solid fa-times"></i></button></div>
      <div class="tm-dial"></div><div class="tm-ctl"></div>`;
    return {el, state: null};
  }

  // Long names scroll like a music-app title: hold, slide left, loop seamlessly.
  const MQ_HOLD = 3000, MQ_SPEED = 30, MQ_GAP = 48;
  const mqObs = window.ResizeObserver && new ResizeObserver(es => es.forEach(e => marquee(e.target)));
  function marquee(nm, text) {
    if (text !== undefined) nm.dataset.text = text;
    text = nm.dataset.text || "";
    if (nm._mq) { nm._mq.cancel(); nm._mq = null; }
    nm.classList.remove("mq");
    nm.innerHTML = '<span class="tm-mq"></span>';
    const inner = nm.firstChild; inner.textContent = text;
    if (mqObs && !nm._obs) { mqObs.observe(nm); nm._obs = true; }
    if (!nm.clientWidth || inner.offsetWidth <= nm.clientWidth) return;
    const dist = inner.offsetWidth + MQ_GAP;
    const copy = inner.cloneNode(true); copy.style.paddingLeft = MQ_GAP + "px";
    nm.appendChild(copy); nm.classList.add("mq");
    const move = dist / MQ_SPEED * 1000, total = MQ_HOLD + move;
    const frames = [{transform: "translateX(0)", offset: 0}, {transform: "translateX(0)", offset: MQ_HOLD / total},
      {transform: `translateX(-${dist}px)`, offset: 1}];
    nm._mq = {anims: [inner, copy].map(s => s.animate(frames, {duration: total, iterations: Infinity,
      delay: Math.random() * 1500})), cancel() { this.anims.forEach(a => a.cancel()); }};
  }
  function render(full) {
    const seen = new Set();
    state.timers.forEach((raw, i) => {
      const t = D.live(raw);
      seen.add(t.id);
      let c = cards.get(t.id);
      if (!c) { c = makeCard(t); cards.set(t.id, c); }
      if (grid.children[i] !== c.el) grid.insertBefore(c.el, grid.children[i] || null);
      if (c.state !== t.state) {
        c.state = t.state;
        c.el.querySelector(".tm-ctl").innerHTML = controls(t);
        c.el.classList.toggle("ring", t.state === "ring");
        c.el.classList.toggle("done", t.state === "done");
      }
      c.el.classList.toggle("alarm", !!t.at);
      const nm = c.el.querySelector(".tm-name");
      if (nm.title !== t.name) { nm.title = t.name; marquee(nm, t.name); }
      c.el.querySelector(".tm-dial").innerHTML = D.dial(t, 280);
    });
    for (const [id, c] of cards) if (!seen.has(id)) { c.el.remove(); cards.delete(id); }
    const n = state.timers.length;
    $("tm-empty").hidden = n > 0;
    $("tm-count").textContent = n ? `${n} of ${state.max}` : "";
    $("tm-new").disabled = n >= state.max;
    if (full) renderPresets();
  }
  grid.addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const id = b.closest(".tm-card").dataset.id;
    act(`${base}api/timers/${id}/${b.dataset.act}/`);
  });

  // ---- presets -----------------------------------------------------------
  function spkNote(p) {
    if (!Array.isArray(p.speakers)) return "";
    const n = p.speakers.length;
    return n ? ` · ${n} speaker${n > 1 ? "s" : ""}` : " · screens only";
  }
  function renderPresets() {
    const ps = state.presets;
    $("tm-chips").innerHTML = ps.map(p =>
      `<button class="tm-chip" data-start="${p.id}"><i class="fa-solid fa-stopwatch"></i>${D.esc(p.name)}<small>${D.fmt(p.seconds)}</small></button>`).join("");
    $("tm-presets").innerHTML = ps.length ? ps.map(p =>
      `<div class="tm-pre"><div><b>${D.esc(p.name)}</b><small>${D.fmt(p.seconds)}${spkNote(p)}</small></div>
        <button class="go" data-start="${p.id}">Start</button>
        <button class="del" data-del="${p.id}" aria-label="Delete"><i class="fa-solid fa-trash"></i></button></div>`).join("")
      : `<p class="tm-note">Tick <em>Save as a preset</em> when starting a timer to keep it here, e.g. an egg timer.</p>`;
  }
  document.addEventListener("click", async e => {
    const s = e.target.closest("[data-start]"), d = e.target.closest("[data-del]");
    if (s) { if (await act(`${base}api/presets/${s.dataset.start}/start/`)) closeSheet(); }
    else if (d) act(`${base}api/presets/${d.dataset.del}/delete/`);
  });

  // ---- new-timer sheet ---------------------------------------------------
  const ed = {mode: "dur", secs: 0, h: 6, m: 0, pm: true};
  const pad = n => String(n).padStart(2, "0");
  function defaultAt() {
    const d = new Date(Date.now() + 60 * 60 * 1000);
    d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
    ed.pm = d.getHours() >= 12; ed.h = d.getHours() % 12 || 12; ed.m = d.getMinutes() % 60;
  }
  function digit(key, val, label) {
    return `<div class="d"><button data-step="${key}:1"><i class="fa-solid fa-chevron-up"></i></button><b>${val}</b>
      <button data-step="${key}:-1"><i class="fa-solid fa-chevron-down"></i></button><small>${label}</small></div>`;
  }
  function drawEditor() {
    const big = $("tm-big");
    if (ed.mode === "dur") {
      const h = Math.floor(ed.secs / 3600), m = Math.floor(ed.secs % 3600 / 60), s = ed.secs % 60;
      big.innerHTML = digit("h", pad(h), "HOURS") + '<span class="c">:</span>' + digit("m", pad(m), "MIN")
        + '<span class="c">:</span>' + digit("s", pad(s), "SEC");
    } else {
      big.innerHTML = digit("ah", ed.h, "HOUR") + '<span class="c">:</span>' + digit("am", pad(ed.m), "MIN")
        + `<div class="tm-ampm"><button data-pm="0" class="${ed.pm ? "" : "on"}">AM</button><button data-pm="1" class="${ed.pm ? "on" : ""}">PM</button></div>`;
    }
    $("tm-seg").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.mode === ed.mode));
    $("tm-quick").hidden = ed.mode !== "dur";
    $("tm-save-row").hidden = ed.mode !== "dur";
  }
  const STEP = {h: 3600, m: 60, s: 1};
  $("tm-big").addEventListener("click", e => {
    const st = e.target.closest("[data-step]"), pm = e.target.closest("[data-pm]");
    if (pm) ed.pm = pm.dataset.pm === "1";
    if (st) {
      const [k, dir] = st.dataset.step.split(":"), n = +dir;
      if (STEP[k]) {
        const unit = STEP[k], cur = Math.floor(ed.secs / unit) % (k === "h" ? 24 : 60);
        const next = (cur + n + (k === "h" ? 24 : 60)) % (k === "h" ? 24 : 60);
        ed.secs += (next - cur) * unit;
      } else if (k === "ah") ed.h = (ed.h + n + 11) % 12 + 1;
      else if (k === "am") ed.m = (ed.m + n * 5 + 60) % 60;
    }
    drawEditor();
  });
  $("tm-quick").addEventListener("click", e => {
    const b = e.target.closest("[data-add]");
    if (!b) return;
    const add = +b.dataset.add;
    ed.secs = add ? Math.min(ed.secs + add, 24 * 3600 - 1) : 0;
    drawEditor();
  });
  $("tm-seg").addEventListener("click", e => {
    const b = e.target.closest("[data-mode]");
    if (b) { ed.mode = b.dataset.mode; drawEditor(); }
  });
  function openSheet() {
    Object.assign(ed, {mode: "dur", secs: 0, spk: new Set(spk ? spk.dflt : []), spkTouched: false}); defaultAt();
    $("tm-name").value = ""; $("tm-save").checked = false; $("tm-err").textContent = "";
    drawEditor(); renderPresets(); drawSpeakers();
    $("tm-sheet").hidden = false;
    loadSpeakers();
  }
  function closeSheet() { $("tm-sheet").hidden = true; }

  // ---- speakers ----------------------------------------------------------
  let spk = null;  // {list: [[id, label]], dflt: [ids]} once fetched
  async function loadSpeakers() {
    if (spk) return;
    try {
      const r = await fetch(root.dataset.speakersUrl, {credentials: "same-origin"});
      if (!r.ok) return;
      const data = await r.json();
      spk = {list: data.speakers || [], dflt: data.default || []};
      if (!$("tm-sheet").hidden && !ed.spkTouched) ed.spk = new Set(spk.dflt);
      drawSpeakers();
    } catch (e) { /* leave the row hidden */ }
  }
  function drawSpeakers() {
    const row = $("tm-spk-row");
    // offline speakers are only offered when already picked, to keep the list short
    const shown = spk ? spk.list.filter(([id, label]) => !/ - offline$/.test(label) || ed.spk.has(id)) : [];
    row.hidden = !shown.length;
    $("tm-spks").innerHTML = shown.map(([id, label]) =>
      `<button data-spk="${D.esc(id)}" class="${ed.spk.has(id) ? "on" : ""}"><i class="fa-solid fa-volume-high"></i>${D.esc(label.replace(/ \((Sonos|Alexa)\)/, ""))}</button>`).join("");
  }
  $("tm-spks").addEventListener("click", e => {
    const b = e.target.closest("[data-spk]");
    if (!b) return;
    const id = b.dataset.spk;
    ed.spk.has(id) ? ed.spk.delete(id) : ed.spk.add(id);
    ed.spkTouched = true;
    drawSpeakers();
  });
  $("tm-new").addEventListener("click", openSheet);
  $("tm-cancel").addEventListener("click", closeSheet);
  $("tm-sheet").addEventListener("click", e => { if (e.target.id === "tm-sheet") closeSheet(); });
  $("tm-start").addEventListener("click", async () => {
    const body = {name: $("tm-name").value.trim()};
    if (ed.mode === "dur") {
      if (!ed.secs) { $("tm-err").textContent = "Set a duration first."; return; }
      body.seconds = ed.secs; body.save = $("tm-save").checked;
    } else {
      body.at = `${pad((ed.h % 12) + (ed.pm ? 12 : 0))}:${pad(ed.m)}`;
    }
    if (ed.spkTouched) body.speakers = [...ed.spk];
    try { apply(await D.post(root.dataset.createUrl, body)); closeSheet(); }
    catch (e) { $("tm-err").textContent = e.message; }
  });

  // ---- refresh -----------------------------------------------------------
  async function poll() {
    try {
      const r = await fetch(root.dataset.stateUrl, {credentials: "same-origin"});
      if (r.ok) apply(await r.json());
    } catch (e) { /* keep ticking locally */ }
  }
  render(true);
  setInterval(() => render(false), 1000);
  setInterval(poll, 2000);
})();
