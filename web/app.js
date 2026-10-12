/* Kaiwa — client app */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = {
  get: (u) => fetch(u).then(r => r.json()),
  post: (u, b) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) }).then(r => r.json()),
  put: (u, b) => fetch(u, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) }).then(r => r.json()),
  del: (u) => fetch(u, { method: "DELETE" }).then(r => r.json()),
};

const icon = (n, cls = "") => `<svg class="icon ${cls}"><use href="/static/icons.svg#${n}"/></svg>`;
/* scenario id → Lucide icon */
const ICON_MAP = {
  ramen: "soup", izakaya: "beer", cafe: "coffee", sushi: "fish", konbini: "store",
  airport: "plane", train: "train-front", hotel: "hotel", directions: "map",
  taxi: "car-taxi-front", ryokan: "waves", clothes: "shirt", electronics: "smartphone",
  souvenir: "gift", intro: "hand", weather: "cloud-sun", weekend: "ferris-wheel",
  hobbies: "guitar", family: "users", barber: "scissors", gym: "dumbbell",
  interview: "briefcase", office: "building-2", classroom: "graduation-cap",
  phone_call: "phone", friends: "handshake", karaoke: "mic-vocal", date: "heart",
  festival: "sparkles", doctor: "stethoscope", pharmacy: "pill", lost: "shield-alert",
  bank: "landmark", post: "mailbox", apartment: "house", phone_contract: "signal",
};

const state = {
  profile: null,
  settings: {},
  session: null,      // {session_id, scenario, mode, title}
  busy: false,
  kanaInput: false,   // romaji → kana typing bound to the chat input
  audio: null,
  catalog: null,
};

/* ============================================================ navigation */
function show(view) {
  $$(".view").forEach(v => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  if (view === "home") loadHome();
  if (view === "dict") $("#dict-search").focus();
  if (view === "progress") loadProgress();
  if (view === "settings") loadSettings();
  updateResumePill(view);
}
$$(".nav-btn").forEach(b => b.addEventListener("click", () => show(b.dataset.view)));

/* an active chat isn't lost when you switch tabs — tap the pill to get back */
const sessionTitle = () =>
  (state.session && state.session.scenario && state.session.scenario.title) ||
  $("#chat-title").textContent;
function updateResumePill(view) {
  const active = state.session && state.session.mode !== "call";
  $("#resume-pill").classList.toggle("hidden", !(active && view !== "chat"));
  if (active) $("#resume-title").textContent = sessionTitle();
}
$("#resume-pill").addEventListener("click", () => show("chat"));

/* starting a new session over an active one asks first (report / discard / stay) */
let pendingStart = null;
function guardActiveSession(startFn) {
  pendingStart = startFn;
  $("#switch-title").textContent = sessionTitle();
  $("#switch-modal").classList.remove("hidden");
}
$("#switch-cancel").addEventListener("click", () => {
  pendingStart = null;
  $("#switch-modal").classList.add("hidden");
  show("chat");
});
$("#switch-skip").addEventListener("click", () => {
  $("#switch-modal").classList.add("hidden");
  state.session = null;
  const go = pendingStart; pendingStart = null;
  if (go) go();
});
$("#switch-report").addEventListener("click", () => {
  $("#switch-modal").classList.add("hidden");
  showSessionSummary();       // pendingStart continues after "Done ✓"
});

/* =============================================================== startup */
async function boot() {
  state.profile = await api.get("/api/profile");
  state.settings = state.profile.settings;
  setKanaInput(state.settings.romaji_input !== false);
  loadIme();
  updateHealth();
  setInterval(updateHealth, 20000);
  if (!state.profile.name) startOnboarding();
  loadHome();
}

async function updateHealth() {
  try {
    const h = await api.get("/api/health");
    const dot = ok => `<span class="${ok ? "ok" : "bad"}">●</span>`;
    $("#health").innerHTML =
      `${dot(h.claude)} Claude ${h.model.replace("claude-", "")}<br>` +
      `${dot(h.learner.available)} ${h.learner.available ? `lesson ${h.learner.lesson} profile` : "no learner profile"}<br>` +
      `${dot(h.whisper)} ${h.whisper ? "whisper" : "no speech model"} (voice in)<br>` +
      `${dot(h.voicevox || h.aivis)} ${[h.aivis && "Aivis", h.voicevox && "VOICEVOX"].filter(Boolean).join(" + ") || "no voice engine"} (voice out)`;
    updateSetupBanner(h);
  } catch { $("#health").innerHTML = `<span class="bad">●</span> server offline`; }
}

/* what's missing before you can practice: a learner profile and an API key */
function updateSetupBanner(h) {
  const missing = [];
  if (!h.learner.available) missing.push(esc(h.learner.reason));
  if (!h.claude) missing.push("No Anthropic API key — add one in Settings (or ANTHROPIC_API_KEY in .env).");
  const b = $("#setup-banner");
  b.classList.toggle("hidden", !missing.length);
  b.innerHTML = missing.map(m => `${icon("shield-alert")} ${m}`).join("<br>");
}

/* ============================================================ onboarding */
function startOnboarding() {
  $("#onboard-modal").classList.remove("hidden");
}
$("#ob-start").addEventListener("click", async () => {
  const name = $("#ob-name").value.trim() || "friend";
  const interests = $("#ob-interests").value.trim();
  state.profile = await api.put("/api/profile", { name, interests });
  $("#onboard-modal").classList.add("hidden");
  loadHome();
});

/* ================================================================== home */
async function loadHome() {
  const p = state.profile || await api.get("/api/profile");
  const hour = new Date().getHours();
  const greet = hour < 11 ? "おはよう" : hour < 18 ? "こんにちは" : "こんばんは";
  $("#greeting").textContent = `${greet}、${p.name || ""}さん！`;

  api.get("/api/dashboard").then(d => {
    $("#home-stats").innerHTML =
      `<div class="chip">${icon("flame", "accent")} ${d.streak} day streak</div>` +
      `<div class="chip">${icon("timer")} ${d.total_minutes} min</div>`;
  });

  if (!state.catalog) state.catalog = await api.get("/api/scenarios");
  const { scenarios } = state.catalog;
  const cats = {};
  scenarios.forEach(s => (cats[s.category] ||= []).push(s));
  $("#scenario-cats").innerHTML = Object.entries(cats).map(([cat, list]) =>
    `<div class="cat-title">${cat}</div><div class="scen-grid">${list.map(scenCard).join("")}</div>`
  ).join("");

  $$("#scenario-cats .scen-card").forEach(c =>
    c.addEventListener("click", () => startSession("roleplay", { scenario_id: c.dataset.id })));
}

function scenCard(s) {
  return `<div class="scen-card" data-id="${s.id}">
    <span class="scen-icon">${icon(ICON_MAP[s.id] || "message-circle")}</span>
    <h4>${esc(s.title)}</h4><p>${esc(s.description)}</p>
  </div>`;
}

$("#card-free").addEventListener("click", () => startSession("free_chat", {}));
$("#card-custom").addEventListener("click", () => $("#custom-modal").classList.remove("hidden"));
$("#cr-cancel").addEventListener("click", () => $("#custom-modal").classList.add("hidden"));
$("#cr-start").addEventListener("click", () => {
  const custom = {
    title: $("#cr-title").value.trim() || "Custom Roleplay",
    ai_role: $("#cr-ai").value.trim(), user_role: $("#cr-user").value.trim(),
    setting: $("#cr-setting").value.trim(), description: $("#cr-desc").value.trim(),
  };
  $("#custom-modal").classList.add("hidden");
  startSession("roleplay", { custom });
});

/* ================================================================== chat */
async function startSession(mode, opts) {
  if (state.session) return guardActiveSession(() => startSession(mode, opts));
  const res = await api.post("/api/sessions", { mode, ...opts });
  state.session = { ...res, mode };
  const scen = res.scenario;
  $("#chat-title").textContent = scen ? `${scen.title}` : "Free Chat 💬";
  $("#chat-sub").textContent = scen ? (scen.setting || scen.description || "") : "Talk about anything!";
  $("#messages").innerHTML = "";
  $("#hints").classList.add("hidden");
  $("#tgl-furigana").checked = state.settings.furigana !== false;
  $("#tgl-romaji").checked = !!state.settings.romaji;
  applyReadingToggles();
  show("chat");
  sendChat("");   // AI opens the conversation
}

$("#chat-back").addEventListener("click", () => show("home"));
$("#tgl-furigana").addEventListener("change", applyReadingToggles);
$("#tgl-romaji").addEventListener("change", applyReadingToggles);
function applyReadingToggles() {
  $("#messages").classList.toggle("no-furigana", !$("#tgl-furigana").checked);
  $("#messages").classList.toggle("show-romaji", $("#tgl-romaji").checked);
}

function msgEl(role) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="who">${role === "ai" ? "カイワ Kaiwa" : "You"}</div><div class="bubble"></div>`;
  $("#messages").appendChild(div);
  return div;
}
const scrollDown = () => { const m = $("#messages"); m.scrollTop = m.scrollHeight; };

function renderTokens(tokens) {
  return tokens.map(t => {
    const ruby = t.ruby.map(seg =>
      seg.r ? `<ruby>${esc(seg.t)}<rt>${esc(seg.r)}</rt></ruby>` : esc(seg.t)
    ).join("");
    return t.word ? `<span class="tok" data-w="${esc(t.surface)}">${ruby}</span>` : ruby;
  }).join("");
}
const esc = s => s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* SSE pump for /api/chat (the voice call has its own, sentence-by-sentence) */
async function streamChat(text, h) {
  const resp = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.session.session_id, text }),
  });
  if (!resp.ok) throw new Error((await resp.json()).error || resp.statusText);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "", raw = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!line.startsWith("data: ")) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.user_message_id && h.onUser) h.onUser(ev.user_message_id);
      if (ev.error) { h.onError(ev.error); return; }
      if (ev.delta) { raw += ev.delta; h.onDelta(raw); }
      // learner-constrained replies arrive whole: `speak` is the kanji original
      // (for TTS and lookups); the bubble renders ev.tokens (known kanji only)
      if (ev.done) h.onDone(ev, ev.speak || raw);
    }
  }
}

async function sendChat(text) {
  if (state.busy || !state.session) return;
  state.busy = true;
  $("#hints").classList.add("hidden");

  let userDiv = null;
  if (text) {
    userDiv = msgEl("user");
    $(".bubble", userDiv).textContent = text;
    scrollDown();
  }
  const aiDiv = msgEl("ai");
  const bubble = $(".bubble", aiDiv);
  bubble.innerHTML = `<span class="typing"><span></span><span></span><span></span></span>`;
  scrollDown();

  let started = false;
  try {
    await streamChat(text, {
      onUser: mid => { if (userDiv) queueCorrection(mid, userDiv); },
      onError: msg => { bubble.textContent = "⚠️ " + msg; },
      onDelta: raw => {
        if (!started) { bubble.textContent = ""; started = true; }
        bubble.textContent = raw;
        scrollDown();
      },
      onDone: (ev, raw) => finishAiMessage(aiDiv, ev, raw),
    });
  } catch (e) {
    bubble.textContent = "⚠️ " + e.message;
  }
  state.busy = false;
  scrollDown();
}

function finishAiMessage(aiDiv, ev, raw) {
  const bubble = $(".bubble", aiDiv);
  bubble.innerHTML = renderTokens(ev.tokens) +
    `<div class="romaji-line">${esc(ev.romaji || "")}</div>` +
    `<div class="trans-line hidden"></div>`;
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  actions.innerHTML = `
    <button class="act-play" title="Play">${icon("volume-2")} Play</button>
    <button class="act-slow" title="Play slowly">${icon("turtle")} Slow</button>
    <button class="act-trans" title="Translate">${icon("languages")} Translate</button>`;
  aiDiv.appendChild(actions);

  $(".act-play", actions).addEventListener("click", () => playTTS(raw, 1.0));
  $(".act-slow", actions).addEventListener("click", () => playTTS(raw, 0.7));
  $(".act-trans", actions).addEventListener("click", () => translateMsg(ev.message_id, aiDiv));
  $$(".tok", bubble).forEach(tok =>
    tok.addEventListener("click", e => wordPopup(e, tok.dataset.w, raw)));

  if (state.settings.auto_play !== false) playTTS(raw, 1.0);
  if (state.settings.auto_translate) translateMsg(ev.message_id, aiDiv);
}

async function translateMsg(mid, aiDiv) {
  const line = $(".trans-line", aiDiv);
  if (!line.classList.contains("hidden")) { line.classList.add("hidden"); return; }
  line.classList.remove("hidden");
  if (!line.textContent) {
    line.textContent = "…";
    const r = await api.post("/api/translate", { message_id: mid });
    line.textContent = r.translation || "(translation failed)";
  }
  scrollDown();
}

function playTTS(text, speed) {
  if (state.audio) { state.audio.pause(); state.audio = null; }
  const s = (state.settings.speed || 1.0) * speed;
  const v = encodeURIComponent(state.settings.voice || "");
  state.audio = new Audio(`/api/tts?text=${encodeURIComponent(text)}&speed=${s}&voice=${v}`);
  state.audio.play().catch(() => {});
}

/* corrections (async, after user message) */
async function queueCorrection(mid, userDiv) {
  try {
    const c = await api.post("/api/correct", { message_id: mid });
    const div = document.createElement("div");
    if (!c.has_errors) {
      div.className = "correction good";
      div.innerHTML = icon("check") + " " + esc(c.praise || "Perfect!");
    } else {
      div.className = "correction fix";
      div.innerHTML =
        c.errors.map(e =>
          `<div class="fixline">${icon("pencil")} <span class="wrong">${esc(e.wrong || "")}</span> → ` +
          `<span class="right">${esc(e.right || "")}</span><br><span class="why">${esc(e.explanation || "")}</span></div>`
        ).join("") +
        (c.praise ? `<div class="praise">${icon("trophy")} ${esc(c.praise)}</div>` : "");
    }
    userDiv.appendChild(div);
    scrollDown();
  } catch { /* silent */ }
}

/* hints */
$("#hint-btn").addEventListener("click", async () => {
  if (!state.session) return;
  const h = $("#hints");
  if (!h.classList.contains("hidden")) { h.classList.add("hidden"); return; }  // toggle off
  h.classList.remove("hidden");
  h.innerHTML = `<div class="hint-card">${icon("lightbulb")} thinking…</div>`;
  const r = await api.post("/api/hint", { session_id: state.session.session_id });
  h.innerHTML = ((r.suggestions || []).map(s =>
    `<div class="hint-card" data-t="${esc(s.japanese)}">${esc(s.japanese)}` +
    `<span class="en">${esc(s.romaji || "")} — ${esc(s.english || "")}</span></div>`).join("")
    || `<div class="hint-card">Couldn't think of hints, sorry!</div>`) +
    `<button class="hint-close" title="Close hints">${icon("x")}</button>`;
  $(".hint-close", h).addEventListener("click", () => h.classList.add("hidden"));
  $$(".hint-card", h).forEach(c => c.addEventListener("click", () => {
    if (c.dataset.t) { $("#chat-input").value = c.dataset.t; h.classList.add("hidden"); $("#chat-input").focus(); }
  }));
});

/* composer */
$("#send-btn").addEventListener("click", () => submitInput());
$("#chat-input").addEventListener("keydown", e => {
  if (e.key === "Tab" && imeCands.length) { e.preventDefault(); acceptKanji(imeCands[0]); return; }
  if (e.key === "Enter") submitInput();
});
function submitInput(convert = state.kanaInput) {
  const inp = $("#chat-input");
  let t = inp.value.trim();
  if (convert) t = wanakana.toKana(t);   // finish a trailing "n" → ん
  if (!t || state.busy) return;
  inp.value = "";
  updateKanjiBar();
  sendChat(t);
}

/* romaji → kana typing (WanaKana): most learners don't have a Japanese keyboard */
function setKanaInput(on) {
  const inp = $("#chat-input"), btn = $("#kana-btn");
  if (on && !state.kanaInput) wanakana.bind(inp, { IMEMode: true });
  if (!on && state.kanaInput) wanakana.unbind(inp);
  state.kanaInput = on;
  btn.textContent = on ? "あ" : "A";
  btn.classList.toggle("on", on);
  btn.title = on ? "Typing romaji → kana (click to type English)" : "Typing English (click for romaji → kana)";
  inp.placeholder = on ? "Type romaji → かな   nn = ん · - = ー · CAPS = カタカナ"
                       : "Type in Japanese or English…";
}
$("#kana-btn").addEventListener("click", () => {
  setKanaInput(!state.kanaInput);
  api.post("/api/settings", { romaji_input: state.kanaInput });
  updateKanjiBar();
  $("#chat-input").focus();
});

/* kana → kanji: suggest learned kanji for the kana just before the cursor
   (たべ → 食べ, えいが → 映画). Only kanji the learner knows, so choosing the
   right one is recall practice. Tab takes the first suggestion. */
let imeTable = {}, imeMax = 0, imeCands = [];
async function loadIme() {
  const r = await api.get("/api/ime").catch(() => null);
  imeTable = (r && r.table) || {};
  imeMax = Math.max(0, ...Object.keys(imeTable).map(k => k.length));
}
function updateKanjiBar() {
  const inp = $("#chat-input"), bar = $("#kanji-bar");
  imeCands = [];
  if (state.kanaInput) {
    const run = (inp.value.slice(0, inp.selectionStart).match(/[ぁ-ゖー]+$/) || [""])[0];
    for (let n = Math.min(run.length, imeMax); n >= 1; n--) {   // longest match first
      const kana = run.slice(-n);
      (imeTable[kana] || []).forEach(kanji => imeCands.push({ kana, kanji }));
    }
  }
  imeCands = imeCands.slice(0, 8);
  bar.classList.toggle("hidden", !imeCands.length);
  bar.innerHTML = imeCands.map((c, i) =>
    `<button type="button" data-i="${i}">${esc(c.kanji)}<small>${esc(c.kana)}</small></button>`).join("");
}
function acceptKanji(c) {
  const inp = $("#chat-input"), pos = inp.selectionStart, start = pos - c.kana.length;
  inp.value = inp.value.slice(0, start) + c.kanji + inp.value.slice(pos);
  inp.setSelectionRange(start + c.kanji.length, start + c.kanji.length);
  inp.focus();
  updateKanjiBar();
}
$("#kanji-bar").addEventListener("mousedown", e => e.preventDefault());  // keep focus + cursor in the input
$("#kanji-bar").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (b) acceptKanji(imeCands[+b.dataset.i]);
});
// deferred so WanaKana has already turned the keystroke into kana
["input", "click", "keyup"].forEach(ev =>
  $("#chat-input").addEventListener(ev, () => setTimeout(updateKanjiBar)));

/* end session */
$("#end-session").addEventListener("click", showSessionSummary);
async function showSessionSummary() {
  if (!state.session) return;
  const inner = $("#summary-inner");
  inner.innerHTML = `<h2>${icon("clipboard-list")} Session Report</h2><p class='sub'>Kaiwa is writing your report…</p>`;
  $("#summary-modal").classList.remove("hidden");
  const s = await api.post(`/api/sessions/${state.session.session_id}/end`);
  inner.innerHTML = `
    <h2>${icon("clipboard-list")} Session Report</h2>
    <div class="summary-stats">
      <div class="chip">${icon("message-circle")} ${s.stats?.turns ?? 0} replies</div>
      <div class="chip">${icon("pencil")} ${s.stats?.corrections ?? 0} corrections</div>
    </div>
    <p>${esc(s.summary || "")}</p>
    ${s.strengths?.length ? `<h3 class="sect">${icon("trophy")} Strengths</h3><ul class="sum-list">${s.strengths.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${s.areas_to_improve?.length ? `<h3 class="sect">${icon("target")} Work on next</h3><ul class="sum-list">${s.areas_to_improve.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${s.new_words?.length ? `<h3 class="sect">${icon("sparkles")} Words worth learning</h3>` + s.new_words.map(w =>
      `<div class="new-word-row"><span class="w">${esc(w.word || "")}</span>
       <span class="r">${esc(w.reading || "")}</span><span class="m">${esc(w.meaning || "")}</span></div>`).join("") : ""}
    <div class="modal-btns"><button class="btn primary" id="sum-close">Done ✓</button></div>`;
  $("#sum-close").addEventListener("click", () => {
    $("#summary-modal").classList.add("hidden");
    state.session = null;
    const go = pendingStart; pendingStart = null;
    if (go) go(); else show("home");
  });
}

/* ============================================================ word popup */
async function wordPopup(e, word, sentence) {
  const pop = $("#word-pop"), inner = $("#word-pop-inner");
  pop.classList.remove("hidden");
  const x = Math.min(e.clientX, window.innerWidth - 310);
  const y = Math.min(e.clientY + 12, window.innerHeight - 260);
  inner.style.left = x + "px"; inner.style.top = y + "px";
  inner.innerHTML = `<span class="w">${esc(word)}</span><div class="loading">looking up…</div>`;
  const info = await api.post("/api/word", { word, sentence });
  inner.innerHTML = `
    <span class="w">${esc(word)}</span><span class="r">${esc(info.reading || "")}</span>
    <div class="rom">${esc(info.romaji || "")}</div>
    <div class="m">${esc(info.meaning || "")}</div>
    ${info.notes ? `<div class="note">${icon("info")} ${esc(info.notes)}</div>` : ""}
    ${info.example ? `<div class="ex">${esc(info.example)}<br><i>${esc(info.example_en || "")}</i></div>` : ""}
    <div class="pop-btns">
      <button class="btn small" id="pop-play">${icon("volume-2")}</button>
    </div>`;
  $("#pop-play").addEventListener("click", ev => { ev.stopPropagation(); playTTS(word, 1.0); });
}
$("#word-pop").addEventListener("click", e => {
  if (e.target.id === "word-pop") $("#word-pop").classList.add("hidden");
});

/* ============================================================= recording */
let rec = null;
$("#mic-btn").addEventListener("click", async () => {
  const btn = $("#mic-btn");
  if (rec) {                                  // stop & transcribe
    const { ctx, proc, stream, chunks, rate } = rec;
    rec = null;
    btn.classList.remove("recording");
    proc.disconnect(); stream.getTracks().forEach(t => t.stop()); ctx.close();
    const wav = encodeWav(chunks, rate);
    btn.innerHTML = icon("loader", "spin");
    try {
      const fd = new FormData();
      fd.append("audio", new Blob([wav], { type: "audio/wav" }), "in.wav");
      const r = await fetch("/api/stt", { method: "POST", body: fd }).then(r => r.json());
      btn.innerHTML = icon("mic");
      if (r.text) { $("#chat-input").value = r.text; submitInput(false); }  // already Japanese
    } catch { btn.innerHTML = icon("mic"); }
    return;
  }
  try {                                       // start
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    proc.onaudioprocess = ev => chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
    src.connect(proc); proc.connect(ctx.destination);
    rec = { ctx, proc, stream, chunks, rate: ctx.sampleRate };
    btn.classList.add("recording"); btn.innerHTML = icon("square");
  } catch { alert("Microphone access denied."); }
});

function encodeWav(chunks, inRate) {
  let len = chunks.reduce((a, c) => a + c.length, 0);
  const input = new Float32Array(len);
  let off = 0;
  chunks.forEach(c => { input.set(c, off); off += c.length; });
  // downsample to 16k
  const outRate = 16000, ratio = inRate / outRate;
  const outLen = Math.floor(len / ratio);
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const v = input[Math.floor(i * ratio)];
    pcm[i] = Math.max(-1, Math.min(1, v)) * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const dv = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); dv.setUint32(4, 36 + pcm.length * 2, true); ws(8, "WAVE");
  ws(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true); dv.setUint32(24, outRate, true);
  dv.setUint32(28, outRate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  ws(36, "data"); dv.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return buf;
}

/* ============================================================ voice call */
/* Half-duplex "phone call": the tutor's reply is synthesized and spoken
   sentence-by-sentence WHILE it streams; when it finishes, the mic opens
   automatically and simple energy-based VAD detects when you stop talking. */
const call = {
  active: false, muted: false, captions: true,
  queue: [], playing: false, streamDone: true, interrupted: false,
  listening: false, mic: null, vad: null,
  audio: null, timer: null, t0: 0,
};
const SILENT_WAV = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQQAAAAAAA==";

$("#card-call").addEventListener("click", startCall);
$("#call-hangup").addEventListener("click", endCall);
$("#call-avatar").addEventListener("click", interruptAI);
$("#call-mute").addEventListener("click", () => {
  call.muted = !call.muted;
  $("#call-mute").innerHTML = icon(call.muted ? "mic-off" : "mic");
  $("#call-mute").classList.toggle("muted-on", call.muted);
  if (call.listening) {
    call.vad = freshVad();
    callStatus(call.muted ? "muted" : "listening", call.muted ? "Muted" : "Your turn — just speak");
  }
});
$("#call-cc").addEventListener("click", () => {
  call.captions = !call.captions;
  $("#call-cc").classList.toggle("active", call.captions);
  $("#call-overlay").classList.toggle("no-cc", !call.captions);
});

async function startCall() {
  if (state.session) return guardActiveSession(startCall);
  const res = await api.post("/api/sessions", { mode: "call" });
  state.session = { ...res, mode: "call" };
  Object.assign(call, {
    active: true, muted: false, captions: true,
    queue: [], playing: false, streamDone: true, interrupted: false, listening: false,
  });
  $("#call-caption").textContent = "";
  $("#call-mute").innerHTML = icon("mic");
  $("#call-mute").classList.remove("muted-on");
  $("#call-cc").classList.add("active");
  $("#call-overlay").classList.remove("hidden", "no-cc");
  call.t0 = Date.now();
  $("#call-timer").textContent = "0:00";
  call.timer = setInterval(() => {
    const s = Math.floor((Date.now() - call.t0) / 1000);
    $("#call-timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
  if (!call.audio) call.audio = new Audio();
  call.audio.src = SILENT_WAV;                 // unlock audio inside the tap (iOS)
  call.audio.play().catch(() => {});
  await ensureMic();                           // ask permission inside the tap too
  callStatus("thinking", "Calling…");
  callTurn("");                                // Kaiwa opens the call
}

async function endCall() {
  call.active = false;
  clearInterval(call.timer);
  call.queue = []; call.playing = false; call.listening = false;
  if (call.audio) call.audio.pause();
  if (call.mic) {
    try {
      call.mic.proc.disconnect();
      call.mic.stream.getTracks().forEach(t => t.stop());
      call.mic.ctx.close();
    } catch { /* already closed */ }
    call.mic = null;
  }
  $("#call-overlay").classList.add("hidden");
  showSessionSummary();
}

function callStatus(cls, text) {
  $("#call-avatar").className = "call-avatar" + (cls ? " " + cls : "");
  $("#call-status").textContent = text;
}

function showCaption(text, user) {
  const c = $("#call-caption");
  c.innerHTML = (user ? `<span class="you">You</span> ` : "") + esc(text);
  c.classList.toggle("user", !!user);
}

/* --- AI turn: stream, split into sentences, speak each as it completes --- */
async function callTurn(text) {
  if (!call.active) return;
  call.streamDone = false;
  call.interrupted = false;
  callStatus("thinking", "Kaiwa is thinking…");
  let pending = "";
  try {
    const resp = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session.session_id, text }),
    });
    if (!resp.ok) throw new Error((await resp.json()).error || resp.statusText);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.user_message_id)  // keep the mistake-memory pipeline fed
          api.post("/api/correct", { message_id: ev.user_message_id }).catch(() => {});
        if (ev.error) throw new Error(ev.error);
        if (ev.delta) {
          pending += ev.delta;
          const [sents, rest] = splitSentences(pending);
          pending = rest;
          sents.forEach(enqueueSpeech);
        }
        if (ev.done && ev.speak) {  // learner-constrained reply: speak the original,
          const [said] = splitSentences(ev.speak + "\n");  // caption what the learner can read
          const [shown] = splitSentences((ev.display || ev.speak) + "\n");
          said.forEach((s, i) => enqueueSpeech(s, shown[i] || s));
        }
        if (ev.done && pending.trim()) { enqueueSpeech(pending.trim()); pending = ""; }
      }
    }
  } catch (e) {
    if (!call.active) return;
    call.streamDone = true;
    callStatus("", "Hiccup: " + e.message);
    startListening(true);
    return;
  }
  call.streamDone = true;
  pumpSpeech();   // if everything already played (or was interrupted), move on
}

function splitSentences(text) {
  const out = [];
  let cur = "";
  for (const ch of text) {
    cur += ch;
    if ("。！？!?\n".includes(ch)) {
      const t = cur.trim();
      if (t && /[^。！？!?\s]/.test(t)) out.push(t);
      cur = "";
    }
  }
  return [out, cur];
}

function enqueueSpeech(text, caption = text) {
  if (!call.active || call.interrupted) return;
  const speed = state.settings.speed || 1.0;
  const voice = encodeURIComponent(state.settings.voice || "");
  const url = `/api/tts?text=${encodeURIComponent(text)}&speed=${speed}&voice=${voice}`;
  // start fetching immediately — next sentence synthesizes while this one plays
  call.queue.push({ text, caption, blob: fetch(url).then(r => r.ok ? r.blob() : null).catch(() => null) });
  pumpSpeech();
}

async function pumpSpeech() {
  if (!call.active || call.playing) return;
  const item = call.queue.shift();
  if (!item) {
    if (call.streamDone && !call.listening) startListening();
    return;
  }
  call.playing = true;
  callStatus("speaking", "Kaiwa is speaking — tap the circle to interrupt");
  showCaption(item.caption, false);
  const blob = await item.blob;
  if (!call.active || call.interrupted) { call.playing = false; return; }
  if (!blob) { call.playing = false; pumpSpeech(); return; }
  const src = URL.createObjectURL(blob);
  call.audio.src = src;
  call.audio.onended = () => { URL.revokeObjectURL(src); call.playing = false; pumpSpeech(); };
  call.audio.onerror = () => { URL.revokeObjectURL(src); call.playing = false; pumpSpeech(); };
  call.audio.play().catch(() => { call.playing = false; pumpSpeech(); });
}

function interruptAI() {
  if (!call.active || (!call.playing && call.queue.length === 0)) return;
  call.interrupted = true;      // stream keeps finishing silently so the reply is saved
  call.queue = [];
  call.audio.pause();
  call.playing = false;
  startListening();
}

/* --- listening: energy-based VAD, auto-stop on ~1.2s of silence --- */
async function ensureMic() {
  if (call.mic) return true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = ev => vadProcess(new Float32Array(ev.inputBuffer.getChannelData(0)));
    src.connect(proc); proc.connect(ctx.destination);
    call.mic = { stream, ctx, proc, rate: ctx.sampleRate };
    return true;
  } catch { return false; }
}

const freshVad = () => ({ started: false, chunks: [], preroll: [], voiced: 0, silence: 0, floor: 0.006 });

async function startListening(afterError) {
  if (!call.active || call.listening) return;
  if (!(await ensureMic())) { callStatus("", "Mic blocked — check browser permissions"); return; }
  if (call.mic.ctx.state === "suspended") call.mic.ctx.resume();
  call.vad = freshVad();
  call.listening = true;
  if (call.muted) callStatus("muted", "Muted");
  else if (!afterError) callStatus("listening", "Your turn — just speak");
}

function vadProcess(buf) {
  if (!call.active || !call.listening || call.muted) return;
  const v = call.vad, ms = (buf.length / call.mic.rate) * 1000;
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  const rms = Math.sqrt(sum / buf.length);
  if (!v.started) {
    v.floor = Math.min(0.02, v.floor * 0.95 + rms * 0.05);   // adapt to room noise
    v.preroll.push(buf);
    if (v.preroll.length > 8) v.preroll.shift();              // keep ~0.7s pre-roll
    v.voiced = rms > Math.max(v.floor * 3, 0.012) ? v.voiced + ms : 0;
    if (v.voiced >= 180) {
      v.started = true;
      v.chunks = v.preroll.splice(0);
      callStatus("listening live", "Listening…");
    }
  } else {
    v.chunks.push(buf);
    v.silence = rms < Math.max(v.floor * 2.5, 0.01) ? v.silence + ms : 0;
    if (v.silence >= 1200 || v.chunks.length * ms > 30000) finishUtterance();
  }
}

async function finishUtterance() {
  const v = call.vad;
  call.listening = false;
  if (v.chunks.length * (4096 / call.mic.rate) * 1000 < 400) { startListening(); return; }
  callStatus("thinking", "Got it…");
  const wav = encodeWav(v.chunks, call.mic.rate);
  let text = "";
  try {
    const fd = new FormData();
    fd.append("audio", new Blob([wav], { type: "audio/wav" }), "in.wav");
    const r = await fetch("/api/stt", { method: "POST", body: fd }).then(r => r.json());
    text = (r.text || "").trim();
  } catch { /* fall through to retry */ }
  if (!call.active) return;
  if (!text) {
    startListening(true);
    callStatus("listening", "Didn't catch that — try again");
    return;
  }
  showCaption(text, true);
  while (!call.streamDone && call.active)   // barge-in: let the previous reply finish saving
    await new Promise(r => setTimeout(r, 150));
  callTurn(text);
}

/* ============================================================== progress */
async function loadProgress() {
  const d = await api.get("/api/dashboard");
  const stat = (n, l) => `<div class="stat-card"><div class="num">${n}</div><div class="lbl">${l}</div></div>`;
  $("#progress-stats").innerHTML =
    stat(`${d.streak}${icon("flame", "accent")}`, "day streak") + stat(d.total_minutes, "minutes practiced") +
    stat(d.sessions_count, "sessions") + stat(d.messages_spoken, "things you said") +
    stat(d.mistakes_logged, "mistakes caught");
  state.recentSessions = d.recent_sessions;
  state.sessShown = SESS_PAGE;
  renderRecentSessions();
  const max = Math.max(1, ...d.mistake_categories.map(c => c.n));
  $("#mistake-cats").innerHTML = d.mistake_categories.length ? d.mistake_categories.map(c => `
    <div class="mcat"><span class="lbl">${esc(c.category || "other")}</span>
    <div class="bar" style="width:${(c.n / max) * 260}px"></div><span class="n">${c.n}</span></div>`).join("")
    : `<p class="sub">No mistakes logged yet. (That's either very good or very quiet.)</p>`;
}

const SESS_PAGE = 8;
function renderRecentSessions() {
  const list = state.recentSessions || [];
  if (!list.length) {
    $("#recent-sessions").innerHTML = `<p class="sub">No sessions yet — start one from Home!</p>`;
    return;
  }
  const slice = list.slice(0, state.sessShown);   // slice index == index into state.recentSessions
  const more = list.length - slice.length;
  $("#recent-sessions").innerHTML = slice.map((s, i) => `
    <div class="session-item expandable" data-i="${i}">
      <div class="head"><span>${esc(s.title)} <small>(${s.mode.replace("_", " ")})</small></span>
      <span class="when">${new Date(s.started_at * 1000).toLocaleDateString()} · ${s.minutes} min <span class="chev">▾</span></span></div>
      ${s.summary?.summary ? `<div class="summ">${esc(s.summary.summary)}</div>` : ""}
      <div class="sess-detail hidden">${sessionDetailHTML(s)}
        <button class="btn small sess-pdf" data-i="${i}">${icon("clipboard-list")} Save as PDF</button>
      </div>
    </div>`).join("") +
    (more ? `<button class="btn small show-more" id="sess-more">Show more (${more})</button>` : "");
}

function sessionDetailHTML(s) {
  const sum = s.summary || {};
  let h = "";
  if (sum.stats) h += `<p class="sub">${sum.stats.turns ?? "?"} turns · ${sum.stats.corrections ?? 0} corrections</p>`;
  if (sum.strengths?.length) h += `<h4>Strengths</h4><ul>${sum.strengths.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (sum.areas_to_improve?.length) h += `<h4>To improve</h4><ul>${sum.areas_to_improve.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (sum.new_words?.length) h += `<h4>New words</h4><ul>${sum.new_words.map(w =>
    `<li><b>${esc(w.word || "")}</b>${w.reading && w.reading !== w.word ? `（${esc(w.reading)}）` : ""} — ${esc(w.meaning || "")}</li>`).join("")}</ul>`;
  return h || `<p class="sub">No detailed report was saved for this session.</p>`;
}

$("#recent-sessions").addEventListener("click", e => {
  if (e.target.closest("#sess-more")) {
    state.sessShown += SESS_PAGE; renderRecentSessions(); return;
  }
  const pdfBtn = e.target.closest(".sess-pdf");
  if (pdfBtn) {
    printSessionReport(state.recentSessions[+pdfBtn.dataset.i]);
    return;
  }
  const item = e.target.closest(".session-item");
  if (item) {
    item.querySelector(".sess-detail")?.classList.toggle("hidden");
    item.classList.toggle("open");
  }
});

function printSessionReport(s) {
  const sum = s.summary || {};
  $("#print-report").innerHTML = `
    <h1>Kaiwa 会話 — Session Report</h1>
    <p class="meta">${esc(s.title)} (${s.mode.replace("_", " ")}) ·
      ${new Date(s.started_at * 1000).toLocaleString()} · ${s.minutes} min</p>
    ${sum.summary ? `<p>${esc(sum.summary)}</p>` : ""}
    ${sessionDetailHTML(s)}`;
  window.print(); // user picks "Save as PDF" in the dialog
}

/* ============================================================== settings */
async function loadSettings() {
  const p = await api.get("/api/profile");
  state.profile = p; state.settings = p.settings;
  $("#set-name").value = p.name || "";
  $("#set-interests").value = p.interests || "";
  renderAISettings(); // async

  const v = await api.get("/api/voices");
  const cur = p.settings.voice || v.default;
  $("#set-voice").innerHTML = v.voices.map(x =>
    `<option value="${x.id}" ${x.id === cur ? "selected" : ""}>${x.label} (${x.engine})</option>`).join("");
  $("#aivis-hint").classList.toggle("hidden", v.voices.some(x => x.engine === "AivisSpeech"));

  $("#set-speed").value = p.settings.speed || 1.0;
  $("#speed-val").textContent = `${$("#set-speed").value}×`;
  $("#set-intonation").value = p.settings.intonation ?? 1.0;
  $("#intonation-val").textContent = `${$("#set-intonation").value}×`;
  $("#set-autoplay").checked = p.settings.auto_play !== false;
  $("#set-autotranslate").checked = !!p.settings.auto_translate;
  renderLearnerStatus(); // async

  renderPhoneSetup(); // async, fills its own card
  renderBackupStatus();
}

async function renderLearnerStatus() {
  const l = await api.get("/api/learner").catch(() => null);
  if (!l) return;
  $("#learner-status").textContent = !l.available ? l.reason
    : `${l.textbook}, lesson ${l.lesson} · ${l.grammar} grammar points · ${l.words} words · ${l.kanji} kanji words`;
}

async function renderAISettings() {
  const a = await api.get("/api/ai");
  $("#set-model").innerHTML = a.models.map(m =>
    `<option ${m === a.model ? "selected" : ""}>${m}</option>`).join("");
  $("#set-apikey").value = "";
  $("#set-apikey").placeholder = a.key_saved ? "•••••• saved — paste to replace"
    : a.has_key ? "using ANTHROPIC_API_KEY from .env" : "Paste your Anthropic API key";
}

async function renderBackupStatus() {
  const b = await api.get("/api/backup/status").catch(() => null);
  if (!b) return;
  $("#set-backup-freq").value = b.freq;
  $("#backup-status").textContent =
    `Backups go to ${b.dir}` +
    (b.last ? ` — last backup: ${new Date(b.last * 1000).toLocaleString()}` : " — no backup yet");
}
$("#backup-now").addEventListener("click", async () => {
  const r = await api.post("/api/backup/now", {});
  if (r.error) { $("#backup-status").textContent = r.error; return; }
  renderBackupStatus();
});
$("#import-btn").addEventListener("click", () => $("#import-file").click());
$("#import-file").addEventListener("change", async () => {
  const f = $("#import-file").files[0];
  if (!f) return;
  if (!confirm("Importing replaces ALL current data (a safety copy is kept). Continue?")) return;
  const fd = new FormData();
  fd.append("file", f);
  const resp = await fetch("/api/backup/import", { method: "POST", body: fd });
  const r = await resp.json();
  if (r.error) { $("#backup-status").textContent = r.error; return; }
  location.reload(); // everything (profile, streak, history) just changed
});
$("#reset-data").addEventListener("click", async () => {
  if (!confirm("Delete ALL data — profile, streak, and history?")) return;
  if (!confirm("Really sure? This wipes everything back to a fresh install.\n(A safety copy is kept next to the database.)")) return;
  const r = await api.post("/api/reset", {});
  if (r.error) { $("#backup-status").textContent = r.error; return; }
  location.reload(); // fresh db → onboarding starts over
});

async function renderPhoneSetup() {
  const box = $("#phone-setup");
  const t = await api.get("/api/setup/phone").catch(() => null);
  if (!t) { box.innerHTML = `<p class="sub">Couldn't check phone status.</p>`; return; }
  if (t.url) {
    const qr = qrcode(0, "M");
    qr.addData(t.url);
    qr.make();
    box.innerHTML = `
      <p class="sub">Install <b>Tailscale</b> on your phone, sign in with the <b>same account</b> as this computer, then scan:</p>
      <div class="qr-wrap">${qr.createSvgTag({ cellSize: 4, margin: 2 })}</div>
      <p class="sub"><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.url)}</a></p>
      <p class="sub">Add it to your home screen for an app-like experience.</p>`;
  } else if (t.installed) {
    box.innerHTML = `
      <p class="sub">Tailscale is installed but not connected.</p>
      <p class="sub">1. Open the Tailscale app on this computer and sign in.<br>
      2. Restart Kaiwa (run.sh) so the secure phone link starts.<br>
      3. Come back here for your QR code.</p>`;
  } else {
    box.innerHTML = `
      <p class="sub">Kaiwa runs on this computer — your phone connects to it as a remote screen.
      The free <b>Tailscale</b> app makes that connection secure from anywhere:</p>
      <p class="sub">1. <a href="https://tailscale.com/download" target="_blank" rel="noopener">Install Tailscale ↗</a> on this computer and sign in.<br>
      2. Install it on your phone and sign in with the same account.<br>
      3. Restart Kaiwa (run.sh), then come back here for your QR code.</p>`;
  }
}
$("#set-speed").addEventListener("input", () => $("#speed-val").textContent = `${$("#set-speed").value}×`);
$("#set-intonation").addEventListener("input", () => $("#intonation-val").textContent = `${$("#set-intonation").value}×`);
/* voice preview: speak a sample with the currently selected (unsaved) voice + sliders */
let previewAudio = null;
$("#voice-preview").addEventListener("click", () => {
  const btn = $("#voice-preview");
  if (previewAudio) { previewAudio.pause(); previewAudio = null; }
  const v = $("#set-voice").value;
  const s = parseFloat($("#set-speed").value) || 1.0;
  const i = parseFloat($("#set-intonation").value) || 1.0;
  btn.innerHTML = icon("loader", "spin");
  const reset = () => { btn.innerHTML = icon("volume-2"); };
  previewAudio = new Audio(`/api/tts?text=${encodeURIComponent("こんにちは、カイワです。よろしくお願いします！")}&speed=${s}&intonation=${i}&voice=${encodeURIComponent(v)}`);
  previewAudio.addEventListener("playing", reset);
  previewAudio.addEventListener("error", reset);
  previewAudio.play().catch(reset);
});
$("#save-settings").addEventListener("click", async () => {
  await api.put("/api/profile", {
    name: $("#set-name").value.trim(),
    interests: $("#set-interests").value.trim(),
  });
  const patch = {
    claude_model: $("#set-model").value,
    voice: $("#set-voice").value,
    speed: parseFloat($("#set-speed").value),
    intonation: parseFloat($("#set-intonation").value),
    auto_play: $("#set-autoplay").checked,
    auto_translate: $("#set-autotranslate").checked,
    furigana: $("#tgl-furigana").checked,
    romaji: $("#tgl-romaji").checked,
  };
  patch.backup_freq = $("#set-backup-freq").value;
  const keyVal = $("#set-apikey").value.trim();
  if (keyVal) patch.api_key_anthropic = keyVal;  // only send a key when the user typed one
  state.settings = await api.post("/api/settings", patch);
  state.profile = await api.get("/api/profile");
  renderAISettings();
  updateHealth();
  $("#settings-saved").classList.remove("hidden");
  setTimeout(() => $("#settings-saved").classList.add("hidden"), 1800);
});

/* ============================================================ dictionary */
let dictTimer = null;
$("#dict-search").addEventListener("input", () => {
  clearTimeout(dictTimer);
  dictTimer = setTimeout(dictSearch, 250);
});
async function dictSearch() {
  const q = $("#dict-search").value.trim();
  const box = $("#dict-results");
  if (!q) { box.innerHTML = ""; return; }
  const d = await api.get(`/api/dictionary?q=${encodeURIComponent(q)}`);
  if (q !== $("#dict-search").value.trim()) return; // stale response
  if (!d.results.length) {
    box.innerHTML = `<p class="sub">No matches for “${esc(q)}”.</p>`;
    return;
  }
  box.innerHTML = d.results.map(r => `
    <div class="dict-entry">
      <div class="dict-jp">
        <span class="dict-form">${esc(r.form)}</span>
        ${r.reading && r.reading !== r.form ? `<span class="dict-reading">${esc(r.reading)}</span>` : ""}
      </div>
      <div class="dict-def">${esc(r.meaning)}${r.common ? ' <span class="dict-common">common</span>' : ""}</div>
    </div>`).join("");
}
boot();
