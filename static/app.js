const $ = s => document.querySelector(s);
const v = $("#v");
if (v && !v.parentElement.classList.contains("playerwrap")) {
  const wrap = document.createElement("div");
  wrap.className = "playerwrap";
  v.parentElement.insertBefore(wrap, v);
  wrap.appendChild(v);
}
let name = null, rallies = [], sel = -1, dur = 0, playQueue = null;

const fmt = t => {
  if (!isFinite(t)) return "0:00.00";
  const m = Math.floor(t / 60);
  return `${m}:${(t - 60 * m).toFixed(2).padStart(5, "0")}`;
};

let saveTimer = null;
function autosave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    if (!name) return;
    await api("/api/segments", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, rallies })
    });
    const st = $("#status");
    st.textContent = "сохранено"; st.className = "saveflash";
    setTimeout(() => { st.textContent = ""; st.className = "muted"; }, 1200);
  }, 600);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ---------- список видео ----------
async function loadVideos() {
  const list = await api("/api/videos");
  const el = $("#videoPicker");
  if (!list.length) { el.innerHTML = '<span class="muted">положите видео в папку video/</span>'; return; }
  el.innerHTML = `<select id="pick">${list.map(x =>
    `<option value="${x.name}">${x.name}${x.analyzed ? " ✓" : ""}</option>`).join("")}</select>`;
  const first = (list.find(x => x.analyzed) || list[0]).name;
  $("#pick").value = first;
  $("#pick").onchange = e => open(e.target.value);
  open(first);
}

async function open(n) {
  name = n; sel = -1; rallies = [];
  v.src = `/api/video/${encodeURIComponent(n)}`;
  v.playbackRate = parseFloat($("#rate")?.value || "1");
  try {
    const a = await api(`/api/analysis/${encodeURIComponent(n)}`);
    rallies = a.rallies; dur = a.meta.duration;
    $("#status").textContent = "";
  } catch {
    rallies = []; dur = 0;
    $("#status").textContent = "не анализировалось";
  }
  render();
  loadScore();
  loadClips();
}

// ---------- отрисовка ----------
const LONG_RALLY = 12;      // дольше — вероятно, склейка нескольких очков
const SHORT_GAP = 1.5;      // пауза короче — вероятно, розыгрыш разорван
const LET_RALLY = 2.6;      // короче — возможно, переподача (мяч задел сетку)

function flags(i) {
  const r = rallies[i], out = [];
  if (r.end - r.start > LONG_RALLY) out.push(["склейка?", "длинный розыгрыш — возможно, несколько очков подряд"]);
  if (i > 0 && r.start - rallies[i - 1].end < SHORT_GAP) out.push(["разрыв?", "очень короткая пауза — возможно, один розыгрыш разрезан"]);
  if (r.end - r.start < LET_RALLY) out.push(["переподача?", "очень короткий эпизод — возможно, мяч задел сетку на подаче"]);
  if (r.conf !== undefined && r.conf < 0.6) out.push(["слабо", `уверенность ${r.conf}`]);
  return out;
}

function render() {
  if (typeof clipsActive === "function" && clipsActive()) { renderClips(); return; }
  const tl = $("#timeline");
  [...tl.querySelectorAll(".seg")].forEach(e => e.remove());
  const total = dur || v.duration || 1;
  rallies.forEach((r, i) => {
    const d = document.createElement("div");
    d.className = "seg" + (i === sel ? " sel" : "") + (r.open ? " open" : "");
    d.style.left = (100 * r.start / total) + "%";
    d.style.width = Math.max(0.15, 100 * (r.end - r.start) / total) + "%";
    d.onclick = e => { e.stopPropagation(); select(i); v.currentTime = r.start; };
    tl.appendChild(d);
  });
  const onlySus = $("#onlySus")?.checked;
  $("#list").innerHTML = rallies.map((r, i) => {
    const f = flags(i);
    if (onlySus && !f.length) return "";
    return `<li class="${i === sel ? "sel" : ""}${r.open ? " open" : ""}${f.length ? " sus" : ""}" data-i="${i}">
       <span class="n">${i + 1}</span>
       <span class="t">${fmt(r.start)} → ${fmt(r.end)}</span>
       ${f.map(([k, tip]) => `<span class="flag" title="${tip}">${k}</span>`).join("")}
       <span class="d">${r.open ? "идёт…" : (r.end - r.start).toFixed(1) + "с"}</span>
     </li>`;
  }).join("");
  $("#list").querySelectorAll("li").forEach(li => {
    li.onclick = () => { const i = +li.dataset.i; select(i); v.currentTime = rallies[i].start; };
  });
  const nsus = rallies.reduce((a, _, i) => a + (flags(i).length ? 1 : 0), 0);
  const susEl = $("#susCount");
  if (susEl) susEl.textContent = nsus ? `требуют проверки: ${nsus}` : "";
  const sum = rallies.reduce((a, r) => a + r.end - r.start, 0);
  $("#counts").textContent = rallies.length
    ? `${rallies.length} розыгрышей, ${Math.round(sum)} с из ${Math.round(total)} с (${Math.round(100 * sum / total)}%)`
    : "";
  $("#segInfo").textContent = sel >= 0
    ? `розыгрыш ${sel + 1}: ${fmt(rallies[sel].start)} → ${fmt(rallies[sel].end)}`
    : "розыгрыш не выбран";
}

function select(i) {
  sel = Math.max(-1, Math.min(rallies.length - 1, i));
  render();
  const li = $("#list").querySelector("li.sel");
  if (li) li.scrollIntoView({ block: "nearest" });
}

v.addEventListener("timeupdate", () => {
  const total = dur || v.duration || 1;
  $("#cursor").style.left = (100 * v.currentTime / total) + "%";
  $("#clock").textContent = fmt(v.currentTime);
  if (playQueue) {
    const r = playQueue.list[playQueue.i];
    if (r && v.currentTime >= r.end - 0.02) {
      playQueue.i++;
      const nx = playQueue.list[playQueue.i];
      if (nx) { v.currentTime = nx.start; } else { playQueue = null; v.pause(); }
    }
  }
});

$("#timeline").onclick = e => {
  const rect = e.currentTarget.getBoundingClientRect();
  v.currentTime = (dur || v.duration) * (e.clientX - rect.left) / rect.width;
};

// ---------- анализ ----------
$("#btnAnalyze").onclick = async () => {
  if (!name) return;
  await api("/api/analyze", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  });
  $("#progress").hidden = false;
  const timer = setInterval(async () => {
    const p = await api(`/api/progress/${encodeURIComponent(name)}`);
    $("#progressBar").style.width = (p.progress || 0) + "%";
    $("#progressText").textContent = `${p.stage || ""} ${p.progress || 0}%`;
    if (p.done) {
      clearInterval(timer);
      $("#progress").hidden = true;
      if (p.error) { $("#status").textContent = "ошибка: " + p.error; return; }
      await open(name);
    }
  }, 700);
};

$("#btnReset").onclick = async () => {
  if (!name || !confirm("Вернуть автоматическую разметку? Ваши правки для этого видео будут потеряны.")) return;
  await api(`/api/reset/${encodeURIComponent(name)}`, { method: "POST" });
  await open(name);
  hint("возвращена автоматическая разметка");
};

$("#btnClear").onclick = () => {
  if (!rallies.length || !confirm("Удалить все розыгрыши и размечать с нуля?")) return;
  rallies = []; sel = -1; render(); autosave();
  hint("список очищен — нажимайте N в начале розыгрыша и O в конце");
};

$("#rate").onchange = e => { v.playbackRate = parseFloat(e.target.value); };
$("#onlySus").onchange = () => render();

$("#btnPlaySeg").onclick = () => {
  if (sel < 0) return;
  playQueue = { list: [rallies[sel]], i: 0 };
  v.currentTime = rallies[sel].start; v.play();
};
$("#btnPlayAll").onclick = () => {
  if (!rallies.length) return;
  playQueue = { list: rallies.slice(), i: 0 };
  v.currentTime = rallies[0].start; v.play();
};

// ---------- правка клавишами ----------
function sortFix() {
  rallies.sort((a, b) => a.start - b.start);
  rallies.forEach((r, i) => r.id = i + 1);
}
function hint(t) { $("#hint").textContent = t; }

document.addEventListener("keydown", e => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  const step = e.shiftKey ? 0.2 : 2;
  const r = rallies[sel];
  switch (e.key) {
    case " ": e.preventDefault(); v.paused ? v.play() : v.pause(); break;
    case "ArrowRight": e.preventDefault(); v.currentTime += step; break;
    case "ArrowLeft": e.preventDefault(); v.currentTime -= step; break;
    case "ArrowDown": e.preventDefault(); select(sel + 1); if (rallies[sel]) v.currentTime = rallies[sel].start; break;
    case "ArrowUp": e.preventDefault(); select(sel - 1); if (rallies[sel]) v.currentTime = rallies[sel].start; break;
    case "i": case "I": case "ш": case "Ш":
      if (r) { r.start = +v.currentTime.toFixed(3); sortFix(); render(); hint("начало обновлено"); autosave(); } break;
    case "o": case "O": case "щ": case "Щ": {
      const open = rallies.find(x => x.open) || r;
      if (open) {
        open.end = Math.max(open.start + 0.2, +v.currentTime.toFixed(3));
        delete open.open; render(); hint(`розыгрыш ${(open.end - open.start).toFixed(1)}с записан`);
        autosave();
      }
      break;
    }
    case "n": case "N": case "т": case "Т": {
      const t0 = +v.currentTime.toFixed(3);
      rallies.push({ start: t0, end: t0 + 0.5, flights: 0, score: 0, open: true });
      sortFix(); select(rallies.findIndex(x => x.start === t0));
      hint("начало отмечено — нажмите O в конце розыгрыша"); autosave(); break;
    }
    case "Backspace": case "Delete":
      if (r) { e.preventDefault(); rallies.splice(sel, 1); select(Math.min(sel, rallies.length - 1)); hint("удалён"); autosave(); } break;
    case "[": if (r) { r.start = Math.max(0, r.start - 0.1); render(); autosave(); } break;
    case "]": if (r) { r.start = Math.min(r.end - 0.2, r.start + 0.1); render(); autosave(); } break;
    case ",": if (r) { r.end = Math.max(r.start + 0.2, r.end - 0.1); render(); autosave(); } break;
    case ".": if (r) { r.end = r.end + 0.1; render(); autosave(); } break;
  }
});

// ---------- вкладки ----------
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  ["list", "clips", "score", "export", "help"].forEach(k => $("#tab-" + k).hidden = (k !== t.dataset.tab));
  if (t.dataset.tab === "clips") { drawTimeline(); } else { render(); }
});

// ---------- экспорт ----------
$("#exMode").onchange = e => { $("#exLimitRow").hidden = e.target.value !== "highlights"; };
$("#btnExport").onclick = async () => {
  const mode = $("#exMode").value;
  const body = {
    name, mode, quality: $("#exQuality").value,
    rallies: mode === "full" ? [{ start: 0, end: dur || v.duration }] : rallies,
    limit_seconds: mode === "highlights" ? 60 * parseFloat($("#exLimit").value || "3") : 0
  };
  const res = await api("/api/export", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  $("#exProgress").hidden = false;
  const timer = setInterval(async () => {
    const p = await api(`/api/progress/${encodeURIComponent("export:" + name)}`);
    $("#exBar").style.width = (p.progress || 0) + "%";
    $("#exText").textContent = `${p.stage || ""} ${p.progress || 0}%`;
    if (p.done) {
      clearInterval(timer); $("#exProgress").hidden = true;
      $("#exResult").innerHTML = p.error ? `ошибка: ${p.error}`
        : `готово: <a href="/api/download/${encodeURIComponent(res.file.split("/").pop())}">скачать</a>`;
    }
  }, 800);
};

loadVideos();

// ---------- счёт ----------
let score = null;          // {setup, winners, states, summary}
let marking = false;       // режим разметки победителей
let markIdx = 0;

function setupFromForm() {
  return {
    name_a: $("#nameA").value.trim() || "Игрок A",
    name_b: $("#nameB").value.trim() || "Игрок B",
    a_starts_left: $("#startSide").value === "a",
    server_a_first: $("#firstServer").value === "a",
    games_to_win: parseInt($("#format").value, 10),
  };
}

function formFromSetup(s) {
  $("#nameA").value = s.name_a; $("#nameB").value = s.name_b;
  $("#startSide").value = s.a_starts_left ? "a" : "b";
  $("#firstServer").value = s.server_a_first ? "a" : "b";
  $("#format").value = String(s.games_to_win);
}

async function loadScore() {
  if (!name) return;
  try {
    score = await api(`/api/score/${encodeURIComponent(name)}`);
    formFromSetup(score.setup);
    await loadPlayers();
    renderScore();
  } catch { score = null; }
}

async function loadPlayers() {
  const box = $("#whoBox");
  try {
    const info = await api(`/api/players/${encodeURIComponent(name)}`);
    if (!info.available) { box.hidden = true; $("#startSideRow").hidden = false; return; }
    box.hidden = false;
    // если игроки одеты похоже, различать их по картинке нельзя — спрашиваем
    $("#startSideRow").hidden = !!info.reliable;
    $("#ph1").src = `/api/player_photo/${encodeURIComponent(name)}/1`;
    $("#ph2").src = `/api/player_photo/${encodeURIComponent(name)}/2`;
    const me = score && score.me_player;
    document.querySelectorAll(".photo").forEach(b =>
      b.classList.toggle("sel", +b.dataset.p === me));
    const el = $("#whoNote");
    if (!info.reliable) {
      el.className = "warn";
      el.textContent = "игроки одеты похоже — различить их по видео не удаётся, " +
        "поэтому укажите сторону вручную: стороны будут меняться по правилам, после каждого гейма";
    } else {
      const n = (info.switches || []).length;
      const word = n % 10 === 1 && n % 100 !== 11 ? "раз" : "раза";
      el.className = "muted";
      el.textContent = n
        ? `по видео игроки менялись сторонами ${n} ${word}`
        : "смен сторон в видео не найдено";
    }
  } catch { box.hidden = true; }
}

document.addEventListener("click", async e => {
  const b = e.target.closest(".photo");
  if (!b || !name) return;
  const me = +b.dataset.p;
  score = await api("/api/score", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, setup: setupFromForm(),
                           winners: (score && score.winners) || [], me_player: me })
  });
  document.querySelectorAll(".photo").forEach(x =>
    x.classList.toggle("sel", +x.dataset.p === me));
  renderScore();
  hint("игрок выбран — стороны теперь определяются по видео");
});

async function saveScore() {
  score = await api("/api/score", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, setup: setupFromForm(),
                           winners: (score && score.winners) || [],
                           me_player: (score && score.me_player) || null })
  });
  renderScore();
}

$("#btnSetup").onclick = async () => {
  await saveScore();
  $("#setupBox").open = false;
  hint("настройка сохранена");
};

function sideNames(st) {
  // кто где стоит в этот момент матча
  const A = score.setup.name_a, B = score.setup.name_b;
  return st.a_left ? { left: A, right: B, leftIsA: true } : { left: B, right: A, leftIsA: false };
}

function renderScore() {
  if (!score) return;
  const s = score.summary;
  const mm = score.side_mismatch || [];
  const note = mm.length ? ` · расхождение со сторонами в видео на очках: ${mm.slice(0, 5).map(i => i + 1).join(", ")}${mm.length > 5 ? "…" : ""}` : "";
  $("#scoreSummary").className = mm.length ? "warn" : "muted";
  $("#scoreSummary").textContent = score.winners.some(w => w)
    ? `${score.setup.name_a} ${s.games_a} : ${s.games_b} ${score.setup.name_b}` +
      (s.finished ? ` — победил ${s.winner}` : "") +
      `  · размечено ${score.winners.filter(w => w).length} из ${score.winners.length}` + note
    : "счёт не размечен";

  $("#pointList").innerHTML = score.states.map((st, i) => {
    const w = score.winners[i];
    const nm = sideNames(st);
    const who = w === "a" ? score.setup.name_a : w === "b" ? score.setup.name_b : "—";
    return `<li class="${i === markIdx && marking ? "now" : ""}${w ? "" : " unset"}" data-i="${i}">
      <span class="n">${i + 1}</span>
      <span class="srv">${st.server_a ? "●" : ""}</span>
      <span class="sc">${st.points_a}:${st.points_b}</span>
      <span class="who">${who}</span>
      <span class="d">г${st.game} ${st.games_a}:${st.games_b}</span>
    </li>`;
  }).join("");
  $("#pointList").querySelectorAll("li").forEach(li => {
    li.onclick = () => { markIdx = +li.dataset.i; playPoint(); };
  });
}

function playPoint() {
  const r = rallies[markIdx];
  if (!r) { stopMarking(); return; }
  select(markIdx);
  playQueue = { list: [r], i: 0 };
  v.currentTime = r.start; v.play();
  const st = score.states[markIdx] || { a_left: score.setup.a_starts_left };
  const nm = sideNames(st);
  $("#markHint").textContent =
    `очко ${markIdx + 1} из ${rallies.length}: ← ${nm.left}   |   ${nm.right} →   ` +
    `(пробел — повторить)`;
  showSides(nm);
  renderScore();
  const li = $("#pointList").querySelector("li.now");
  if (li) li.scrollIntoView({ block: "nearest" });
}

function showSides(nm) {
  let wrap = document.querySelector(".playerwrap");
  if (!wrap) return;
  wrap.querySelectorAll(".sidelabel").forEach(e => e.remove());
  if (!marking) return;
  for (const [cls, text] of [["l", "← " + nm.left], ["r", nm.right + " →"]]) {
    const d = document.createElement("div");
    d.className = "sidelabel " + cls; d.textContent = text;
    wrap.appendChild(d);
  }
  wrap.classList.add("marking");
}

function startMarking() {
  if (!rallies.length) { hint("сначала нужна разметка розыгрышей"); return; }
  if (!score) { hint("сначала сохраните настройку матча"); return; }
  if (!score.winners) score.winners = [];
  marking = true;
  markIdx = score.winners.findIndex(w => !w);
  if (markIdx < 0) markIdx = 0;
  $("#btnMark").textContent = "Закончить разметку";
  playPoint();
}

function stopMarking() {
  marking = false;
  $("#btnMark").textContent = "Разметить очки";
  $("#markHint").textContent = "";
  const wrap = document.querySelector(".playerwrap");
  if (wrap) { wrap.classList.remove("marking"); wrap.querySelectorAll(".sidelabel").forEach(e => e.remove()); }
  renderScore();
}

$("#btnMark").onclick = () => marking ? stopMarking() : startMarking();

async function setWinner(side) {
  if (!score) return;
  if (!score.winners) score.winners = [];
  const st = score.states[markIdx];
  const leftIsA = st ? st.a_left : score.setup.a_starts_left;
  const who = (side === "left") === leftIsA ? "a" : "b";
  while (score.winners.length < rallies.length) score.winners.push(null);
  score.winners[markIdx] = who;
  await saveScore();
  markIdx++;
  if (markIdx >= rallies.length || (score.summary && score.summary.finished)) {
    stopMarking(); hint("разметка счёта закончена");
  } else playPoint();
}

document.addEventListener("keydown", e => {
  if (!marking) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); setWinner("left"); }
  else if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); setWinner("right"); }
  else if (e.key === " ") { e.preventDefault(); e.stopPropagation(); playPoint(); }
  else if (e.key === "Backspace") {
    e.preventDefault(); e.stopPropagation();
    markIdx = Math.max(0, markIdx - 1); playPoint();
  }
  else if (e.key === "x" || e.key === "X" || e.key === "ч" || e.key === "Ч") {
    e.preventDefault(); e.stopPropagation(); dropAsLet();
  }
}, true);

async function dropAsLet() {
  // переподача: это не очко и в видео не нужна — убираем эпизод целиком
  if (!rallies[markIdx]) return;
  rallies.splice(markIdx, 1);
  if (score && score.winners) score.winners.splice(markIdx, 1);
  await api("/api/segments", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, rallies })
  });
  score = await api(`/api/score/${encodeURIComponent(name)}`);
  render();
  if (markIdx >= rallies.length) { stopMarking(); hint("разметка закончена"); return; }
  hint("переподача убрана");
  playPoint();
}

// ---------- упражнения (тренировки) ----------
let clips = [], clipSel = -1, clipMode = false, drag = null;

const clipsActive = () => !$("#tab-clips").hidden;

async function loadClips() {
  if (!name) return;
  try {
    const d = await api(`/api/clips/${encodeURIComponent(name)}`);
    clips = d.clips || [];
    if (!dur) dur = d.duration;
    renderClips();
  } catch { clips = []; }
}

let clipSaveTimer = null;
function saveClips() {
  clearTimeout(clipSaveTimer);
  clipSaveTimer = setTimeout(async () => {
    await api("/api/clips", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, clips })
    });
    const el = $("#clipStatus");
    el.textContent = "сохранено"; el.className = "saveflash";
    setTimeout(() => { el.textContent = ""; el.className = "muted"; }, 1200);
  }, 500);
}

function renderClips() {
  if (clipsActive()) drawTimeline();
  const total = clips.reduce((a, c) => a + c.end - c.start, 0);
  $("#clipList").innerHTML = clips.map((c, i) => `
    <li class="${i === clipSel ? "sel" : ""}" data-i="${i}">
      <span class="n">${i + 1}</span>
      <input class="title" data-i="${i}" placeholder="название упражнения"
             value="${(c.title || "").replace(/"/g, "&quot;")}">
      <span class="tc">${fmt(c.start)}</span>
      <span class="dur">${(c.end - c.start).toFixed(1)}с</span>
    </li>`).join("");
  $("#clipList").querySelectorAll("li").forEach(li => {
    li.onclick = e => {
      if (e.target.tagName === "INPUT") return;
      clipSel = +li.dataset.i; v.currentTime = clips[clipSel].start; renderClips();
    };
  });
  $("#clipList").querySelectorAll("input.title").forEach(inp => {
    inp.oninput = () => { clips[+inp.dataset.i].title = inp.value; saveClips(); };
  });
  $("#clipStatus").textContent = clips.length
    ? `${clips.length} упражнений, ${Math.round(total / 60)} мин` : "";
}

// полоса под видео показывает либо розыгрыши, либо упражнения
function drawTimeline() {
  const tl = $("#timeline");
  [...tl.querySelectorAll(".seg")].forEach(e => e.remove());
  const total = dur || v.duration || 1;
  clips.forEach((c, i) => {
    const d = document.createElement("div");
    d.className = "seg clip" + (i === clipSel ? " sel" : "");
    d.style.left = (100 * c.start / total) + "%";
    d.style.width = Math.max(0.4, 100 * (c.end - c.start) / total) + "%";
    d.innerHTML = '<div class="grip l"></div><div class="grip r"></div>';
    d.onmousedown = e => {
      const grip = e.target.closest(".grip");
      clipSel = i; renderClips();
      if (!grip) { v.currentTime = c.start; return; }
      e.preventDefault(); e.stopPropagation();
      drag = { i, edge: grip.classList.contains("l") ? "start" : "end" };
      tl.classList.add("dragging");
    };
    tl.appendChild(d);
  });
}

document.addEventListener("mousemove", e => {
  if (!drag) return;
  const tl = $("#timeline"), rect = tl.getBoundingClientRect();
  const total = dur || v.duration || 1;
  let t = total * (e.clientX - rect.left) / rect.width;
  t = Math.max(0, Math.min(total, t));
  const c = clips[drag.i];
  if (drag.edge === "start") c.start = Math.min(t, c.end - 0.3);
  else c.end = Math.max(t, c.start + 0.3);
  v.currentTime = drag.edge === "start" ? c.start : c.end;
  renderClips();
});

document.addEventListener("mouseup", () => {
  if (!drag) return;
  drag = null;
  $("#timeline").classList.remove("dragging");
  clips.sort((a, b) => a.start - b.start);
  saveClips(); renderClips();
});

function clipStart() {
  const t = +v.currentTime.toFixed(2);
  clips.push({ start: t, end: Math.min((dur || v.duration), t + 30), title: "" });
  clips.sort((a, b) => a.start - b.start);
  clipSel = clips.findIndex(c => c.start === t);
  saveClips(); renderClips();
  hint("начало отмечено — нажмите O в конце упражнения");
}

function clipEnd() {
  if (clipSel < 0) return;
  clips[clipSel].end = Math.max(clips[clipSel].start + 0.3, +v.currentTime.toFixed(2));
  saveClips(); renderClips();
  hint(`упражнение ${(clips[clipSel].end - clips[clipSel].start).toFixed(0)}с`);
}

$("#btnClipNew").onclick = clipStart;
$("#btnClipEnd").onclick = clipEnd;

document.addEventListener("keydown", e => {
  if (!clipsActive() || marking) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.key === "n" || e.key === "N" || e.key === "т" || e.key === "Т") {
    e.preventDefault(); e.stopPropagation(); clipStart();
  } else if (e.key === "o" || e.key === "O" || e.key === "щ" || e.key === "Щ") {
    e.preventDefault(); e.stopPropagation(); clipEnd();
  } else if ((e.key === "Backspace" || e.key === "Delete") && clipSel >= 0) {
    e.preventDefault(); e.stopPropagation();
    clips.splice(clipSel, 1); clipSel = Math.min(clipSel, clips.length - 1);
    saveClips(); renderClips(); hint("упражнение удалено");
  }
}, true);

$("#btnClipExport").onclick = async () => {
  if (!clips.length) { hint("сначала разметьте упражнения"); return; }
  const btn = $("#btnClipExport");
  if (btn.disabled) return;
  btn.disabled = true;
  const res = await api("/api/export_clips", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, quality: $("#clipQuality").value })
  });
  $("#clipProgress").hidden = false;
  const timer = setInterval(async () => {
    const p = await api(`/api/progress/${encodeURIComponent("clips:" + name)}`);
    $("#clipBar").style.width = (p.progress || 0) + "%";
    $("#clipText").textContent = `${p.stage || ""} ${p.progress || 0}%`;
    if (p.done) {
      clearInterval(timer); $("#clipProgress").hidden = true; btn.disabled = false;
      $("#clipResult").innerHTML = p.error
        ? `ошибка: ${p.error}`
        : `<div class="muted">готово, ${(p.files || []).length} файлов в папке output/${(p.dir || "").split("/").pop()}</div>`
          + `<div class="files">` + (p.files || []).map(f =>
              `<a href="/api/download_clip/${encodeURIComponent(name)}/${encodeURIComponent(f)}">${f}</a>`).join("") + `</div>`;
    }
  }, 800);
};
