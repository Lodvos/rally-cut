"""Локальный сервер: анализ видео, редактор розыгрышей, экспорт."""
import os, re, json, threading, subprocess, traceback, time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store, signals, export, features, model, scoring, identity, render as scorerender

app = FastAPI(title="Настольный теннис — обработка матчей")
JOBS = {}          # name -> {stage, progress, done, error}
JOBS_LOCK = threading.Lock()


def set_job(name, **kw):
    with JOBS_LOCK:
        JOBS.setdefault(name, {}).update(kw)


def src_path(name):
    p = os.path.join(store.VIDEO_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(404, "нет такого видео")
    return p


# ---------- просмотр ----------

def make_proxy(name, meta):
    """Лёгкая копия для плеера: браузер не всегда умеет HEVC."""
    out = store.path(name, "proxy.mp4")
    if os.path.exists(out):
        return out
    tmp = out + ".part"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src_path(name),
         "-vf", "scale=-2:540", "-c:v", "h264_videotoolbox", "-b:v", "2500k",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         "-f", "mp4", tmp],
        check=True, capture_output=True)
    os.replace(tmp, out)
    return out


def ranged(path, request):
    size = os.path.getsize(path)
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path)
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    a = int(m.group(1)) if m.group(1) else 0
    b = int(m.group(2)) if m.group(2) else size - 1
    b = min(b, size - 1)
    n = b - a + 1

    def gen():
        with open(path, "rb") as f:
            f.seek(a)
            left = n
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(gen(), status_code=206, media_type="video/mp4",
                             headers={"Content-Range": f"bytes {a}-{b}/{size}",
                                      "Accept-Ranges": "bytes",
                                      "Content-Length": str(n)})


# ---------- анализ ----------

def run_analysis(name, params):
    try:
        set_job(name, stage="чтение видео", progress=0, done=False, error=None)
        meta = signals.probe(src_path(name))
        set_job(name, stage="создание копии для просмотра", progress=5)
        make_proxy(name, meta)
        clf = model.load()
        if clf is None:
            raise RuntimeError("нет обученной модели: запустите scripts/train.py")

        def prog(stage, pct):
            set_job(name, stage=stage, progress=pct)

        X, flights, ident = features.extract(src_path(name), meta, progress=prog)
        set_job(name, stage="разметка розыгрышей", progress=92)
        proba = model.predict(clf, X)
        rallies = model.rank_highlights(
            model.segments(proba, meta["duration"], params), X)
        set_job(name, stage="различаю игроков", progress=96)
        _save_identity(name, ident, len(X), rallies)
        store.save(name, "analysis.json", dict(
            meta=meta, params=params, flights=flights, rallies=rallies,
            proba=[round(float(v), 3) for v in proba], created=time.time()))
        store.save(name, "segments.json", dict(rallies=rallies, edited=False))
        set_job(name, stage="готово", progress=100, done=True)
    except Exception as e:
        traceback.print_exc()
        set_job(name, stage="ошибка", error=str(e), done=True)


def _save_identity(name, ident, n_slots, rallies=None):
    """Кто где стоит — по цвету формы, из этого же видео."""
    if not ident:
        return
    obs, shots = ident
    split = identity.split_players(obs)
    if not split:
        return
    identity.save_snapshots(shots, split, store.dir_for(name))
    info = dict(separation=round(split["separation"], 1),
                separable=split["separable"], reliable=split["reliable"])
    if split["reliable"]:
        who_left = identity.side_timeline(obs, split, n_slots, fps=features.FPS)
        if rallies:
            who_left = identity.align_switches(who_left, rallies, features.FPS)
        info["who_left"] = [int(v) for v in who_left]
        info["switches"] = identity.switch_points(who_left, features.FPS)
    store.save(name, "identity.json", info)


class AnalyzeReq(BaseModel):
    name: str
    params: dict = {}


@app.post("/api/analyze")
def api_analyze(r: AnalyzeReq):
    src_path(r.name)
    with JOBS_LOCK:
        j = JOBS.get(r.name)
        if j and not j.get("done"):
            return {"status": "уже выполняется"}
    threading.Thread(target=run_analysis, args=(r.name, r.params), daemon=True).start()
    return {"status": "запущено"}


@app.get("/api/progress/{name}")
def api_progress(name: str):
    with JOBS_LOCK:
        return JOBS.get(name, {"stage": "не запускалось", "progress": 0, "done": False})


@app.get("/api/videos")
def api_videos():
    return store.videos()


@app.get("/api/analysis/{name}")
def api_analysis(name: str):
    a = store.load(name, "analysis.json")
    if not a:
        raise HTTPException(404, "видео ещё не анализировалось")
    seg = store.load(name, "segments.json") or {"rallies": a["rallies"]}
    return {"meta": a["meta"], "rallies": seg["rallies"],
            "flights": [{"start": f["start"], "end": f["end"], "n": f["n"]}
                        for f in a["flights"]]}


class SegReq(BaseModel):
    name: str
    rallies: list


@app.post("/api/segments")
def api_segments(r: SegReq):
    """Сохранение розыгрышей. Размеченные победители переносятся по времени,
    иначе удаление одного розыгрыша сдвинуло бы весь счёт."""
    old = (store.load(r.name, "segments.json") or {}).get("rallies", [])
    sc = store.load(r.name, "score.json") or {}
    winners = sc.get("winners") or []
    if old and winners:
        moved = []
        for seg in r.rallies:
            mid = (seg["start"] + seg["end"]) / 2
            hit = None
            for j, o in enumerate(old):
                if j < len(winners) and o["start"] <= mid <= o["end"]:
                    hit = winners[j]; break
            moved.append(hit)
        sc["winners"] = moved
        store.save(r.name, "score.json", sc)
    store.save(r.name, "segments.json", {"rallies": r.rallies, "edited": True})
    return {"status": "сохранено", "n": len(r.rallies)}


@app.post("/api/reset/{name}")
def api_reset(name: str):
    """Вернуть автоматическую разметку, отбросив ручные правки."""
    a = store.load(name, "analysis.json")
    if not a:
        raise HTTPException(404, "видео ещё не анализировалось")
    store.save(name, "segments.json", {"rallies": a["rallies"], "edited": False})
    return {"status": "сброшено", "n": len(a["rallies"])}


# ---------- счёт ----------

class ScoreReq(BaseModel):
    name: str
    setup: dict = {}
    winners: list = []
    me_player: int | None = None


def _setup_of(name, override=None):
    saved = store.load(name, "score.json") or {}
    data = {**(saved.get("setup") or {}), **(override or {})}
    allowed = {k: v for k, v in data.items() if k in scoring.Setup.__annotations__}
    return scoring.Setup(**allowed)


@app.get("/api/score/{name}")
def api_score(name: str):
    saved = store.load(name, "score.json") or {}
    setup = _setup_of(name)
    seg = store.load(name, "segments.json") or {}
    rallies = seg.get("rallies", [])
    winners = (saved.get("winners") or [])[:len(rallies)]
    winners += [None] * (len(rallies) - len(winners))
    states = scoring.timeline(winners, setup)
    states, mismatches = _apply_sides(name, states, rallies, setup)
    return {"setup": asdict_setup(setup), "winners": winners,
            "me_player": saved.get("me_player"),
            "states": states, "summary": scoring.summary(states, setup),
            "side_mismatch": mismatches}


def asdict_setup(s):
    return {k: getattr(s, k) for k in scoring.Setup.__annotations__}


def _apply_sides(name, states, rallies, setup):
    """Стороны берём из видео, а не из арифметики геймов."""
    ident = store.load(name, "identity.json")
    saved = store.load(name, "score.json") or {}
    me = saved.get("me_player")          # 1 или 2 — кто из снимков это игрок A
    if not ident or not me:
        return states, []
    if not ident.get("reliable"):
        return states, []
    wl = ident.get("who_left") or []
    fps = features.FPS
    mismatches = []
    for st in states:
        i = st["rally"]
        if i >= len(rallies):
            break
        slot = int((rallies[i]["start"] + rallies[i]["end"]) / 2 * fps)
        if slot >= len(wl):
            continue
        a_left_video = (wl[slot] + 1) == me       # слева стоит игрок A?
        if a_left_video != st["a_left"]:
            mismatches.append(st["rally"])
        st["a_left"] = a_left_video
    return states, mismatches


@app.post("/api/score")
def api_score_save(r: ScoreReq):
    setup = _setup_of(r.name, r.setup)
    prev = store.load(r.name, "score.json") or {}
    me = r.me_player if r.me_player is not None else prev.get("me_player")
    store.save(r.name, "score.json", {"setup": asdict_setup(setup),
                                      "winners": r.winners, "me_player": me})
    states = scoring.timeline(r.winners, setup)
    rallies = (store.load(r.name, "segments.json") or {}).get("rallies", [])
    states, mismatches = _apply_sides(r.name, states, rallies, setup)
    return {"setup": asdict_setup(setup), "winners": r.winners, "me_player": me,
            "states": states, "summary": scoring.summary(states, setup),
            "side_mismatch": mismatches}


@app.get("/api/players/{name}")
def api_players(name: str):
    ident = store.load(name, "identity.json")
    if not ident:
        return {"available": False}
    have = [i for i in (1, 2) if os.path.exists(store.path(name, f"player{i}.jpg"))]
    return {"available": bool(have), "players": have,
            "reliable": bool(ident.get("reliable")),
            "switches": ident.get("switches", []),
            "separable": ident.get("separable"),
            "separation": ident.get("separation")}


@app.get("/api/player_photo/{name}/{idx}")
def api_player_photo(name: str, idx: int):
    p = store.path(name, f"player{idx}.jpg")
    if not os.path.exists(p):
        raise HTTPException(404, "снимок не найден")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/video/{name}")
def api_video(name: str, request: Request):
    p = store.path(name, "proxy.mp4")
    if not os.path.exists(p):
        p = src_path(name)
    return ranged(p, request)


# ---------- разбор: заметки по розыгрышам ----------

class NotesReq(BaseModel):
    name: str
    notes: dict = {}          # номер розыгрыша (строкой) -> текст
    tags: dict = {}           # номер розыгрыша (строкой) -> список меток


@app.get("/api/notes/{name}")
def api_notes(name: str):
    d = store.load(name, "notes.json") or {}
    return {"notes": d.get("notes", {}), "tags": d.get("tags", {}),
            "presets": NOTE_TAGS}


@app.post("/api/notes")
def api_notes_save(r: NotesReq):
    store.save(r.name, "notes.json", {"notes": r.notes, "tags": r.tags})
    filled = sum(1 for v in r.notes.values() if (v or "").strip())
    return {"status": "сохранено", "n": filled}


NOTE_TAGS = ["ошибка подачи", "приём", "накат", "топспин", "подрезка",
             "блок", "не дотянулся", "в сетку", "за стол", "хороший розыгрыш"]


@app.post("/api/review/{name}")
def api_review(name: str):
    """Собирает разбор матча в текстовый файл: таймкоды, счёт и мои заметки."""
    seg = store.load(name, "segments.json") or {}
    rallies = seg.get("rallies", [])
    if not rallies:
        raise HTTPException(400, "нет размеченных розыгрышей")
    nd = store.load(name, "notes.json") or {}
    notes, tags = nd.get("notes", {}), nd.get("tags", {})
    sc = store.load(name, "score.json") or {}
    setup = _setup_of(name)
    winners = (sc.get("winners") or [])[:len(rallies)]
    winners += [None] * (len(rallies) - len(winners))
    states = scoring.timeline(winners, setup)
    states, _ = _apply_sides(name, states, rallies, setup)
    by_rally = {st["rally"]: st for st in states}

    def mmss(t):
        m = int(t // 60)
        return f"{m}:{t - 60 * m:05.2f}"

    lines = [f"# Разбор матча: {name}", ""]
    if any(winners):
        s = scoring.summary(states, setup)
        lines += [f"Счёт: {setup.name_a} {s['games_a']} : {s['games_b']} {setup.name_b}", ""]
    lines += ["| № | время | длит. | счёт | выиграл | метки | комментарий |",
              "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rallies):
        st = by_rally.get(i)
        score_txt = f"{st['points_a']}:{st['points_b']}" if st else ""
        w = winners[i]
        who = setup.name_a if w == "a" else setup.name_b if w == "b" else ""
        key = str(i)
        lines.append("| %d | %s | %.1f с | %s | %s | %s | %s |" % (
            i + 1, mmss(r["start"]), r["end"] - r["start"], score_txt, who,
            ", ".join(tags.get(key, [])), (notes.get(key) or "").replace("|", "/")))

    commented = [i for i in range(len(rallies))
                 if (notes.get(str(i)) or "").strip() or tags.get(str(i))]
    lines += ["", f"Розыгрышей: {len(rallies)}, с комментариями: {len(commented)}"]

    os.makedirs(store.OUT_DIR, exist_ok=True)
    out = os.path.join(store.OUT_DIR, os.path.splitext(name)[0] + "_разбор.md")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return {"status": "готово", "file": out, "commented": len(commented)}


# ---------- упражнения (тренировки) ----------

class ClipsReq(BaseModel):
    name: str
    clips: list = []


@app.get("/api/clips/{name}")
def api_clips(name: str):
    data = store.load(name, "clips.json") or {"clips": []}
    meta = (store.load(name, "analysis.json") or {}).get("meta")
    if not meta:
        meta = signals.probe(src_path(name))
    return {"clips": data["clips"], "duration": meta["duration"]}


@app.post("/api/clips")
def api_clips_save(r: ClipsReq):
    clips = []
    for i, c in enumerate(r.clips, 1):
        clips.append(dict(id=i, start=round(float(c["start"]), 2),
                          end=round(float(c["end"]), 2),
                          title=(c.get("title") or "").strip()))
    clips.sort(key=lambda c: c["start"])
    store.save(r.name, "clips.json", {"clips": clips})
    return {"status": "сохранено", "n": len(clips)}


SAFE_NAME = re.compile(r"[^\w\-.() ]+", re.UNICODE)
DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def video_date(name):
    """Дата съёмки: из имени, если она там есть, иначе из самого файла."""
    m = DATE_IN_NAME.search(name)
    if m:
        return m.group(1)
    import datetime as dt
    path = os.path.join(store.VIDEO_DIR, name)
    stamps = [dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()]
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip().split(";")[0]
    if out:
        try:
            stamps.append(dt.datetime.fromisoformat(out.replace("Z", "+00:00")).astimezone())
        except ValueError:
            pass
    return min(stamps).date().isoformat()


def clip_filename(video, index, title):
    """Имя ролика: дата съёмки, номер по порядку и название упражнения."""
    tail = SAFE_NAME.sub("", title or "").strip().replace(" ", "_")
    tail = re.sub(r"_{2,}", "_", tail).strip("_")
    return f"{video_date(video)}_{index:02d}" + (f"_{tail}" if tail else "") + ".mp4"


class ClipExportReq(BaseModel):
    name: str
    quality: str = "12M"


@app.post("/api/export_clips")
def api_export_clips(r: ClipExportReq):
    """Каждое упражнение — отдельный файл, названный по упражнению."""
    data = store.load(r.name, "clips.json") or {"clips": []}
    clips = data["clips"]
    if not clips:
        raise HTTPException(400, "нет размеченных упражнений")
    out_dir = os.path.join(store.OUT_DIR, video_date(r.name) + "_тренировка")
    os.makedirs(out_dir, exist_ok=True)
    key = "clips:" + r.name

    with JOBS_LOCK:                       # второй запуск не нужен: файлы те же
        j = JOBS.get(key)
        if j and not j.get("done"):
            return {"status": "уже выполняется", "dir": out_dir, "n": len(clips)}
        JOBS[key] = {"stage": "подготовка", "progress": 0, "done": False, "error": None}

    total = sum(c["end"] - c["start"] for c in clips) or 1.0

    paths = [os.path.join(out_dir, clip_filename(r.name, i, c.get("title")))
             for i, c in enumerate(clips, 1)]

    def work():
        try:
            set_job(key, stage=f"нарезка, {len(clips)} упражнений")
            export.cut_many(src_path(r.name), clips, paths, r.quality,
                            on_done=lambda k, n: set_job(
                                key, progress=int(100 * k / n),
                                stage=f"готово {k} из {n}"))
            set_job(key, stage="готово", progress=100, done=True,
                    files=[os.path.basename(f) for f in paths], dir=out_dir)
        except Exception as e:
            traceback.print_exc()
            set_job(key, stage="ошибка", error=str(e), done=True)

    threading.Thread(target=work, daemon=True).start()
    return {"status": "запущено", "dir": out_dir, "n": len(clips)}


@app.get("/api/download_clip/{name}/{filename}")
def api_download_clip(name: str, filename: str):
    p = os.path.join(store.OUT_DIR, video_date(name) + "_тренировка", filename)
    if not os.path.exists(p):
        raise HTTPException(404, "файл не найден")
    return FileResponse(p, filename=filename, media_type="video/mp4")


# ---------- экспорт ----------

class ExportReq(BaseModel):
    name: str
    mode: str = "rallies"        # rallies | highlights | full | rallies_score | full_score
    rallies: list = []
    limit_seconds: float = 0.0
    quality: str = "12M"


@app.post("/api/export")
def api_export(r: ExportReq):
    if r.mode in ("rallies_score", "full_score"):
        return _export_with_score(r)
    segs = r.rallies
    if r.mode == "highlights":
        segs = sorted(segs, key=lambda s: -(s.get("score", 0)))
        if r.limit_seconds:
            acc, sel = 0.0, []
            for s in segs:
                d = s["end"] - s["start"]
                if acc + d > r.limit_seconds:
                    continue
                acc += d; sel.append(s)
            segs = sel
        segs = sorted(segs, key=lambda s: s["start"])
    if not segs:
        raise HTTPException(400, "нет сегментов для экспорта")
    os.makedirs(store.OUT_DIR, exist_ok=True)
    base = os.path.splitext(r.name)[0]
    out = os.path.join(store.OUT_DIR, f"{base}_{r.mode}.mp4")

    def work():
        try:
            set_job("export:" + r.name, stage="нарезка", progress=0, done=False, error=None)
            export.build(src_path(r.name), segs, out, r.quality,
                         progress=lambda i, n: set_job("export:" + r.name,
                                                       progress=int(100 * i / n)))
            set_job("export:" + r.name, stage="готово", progress=100, done=True, file=out)
        except Exception as e:
            traceback.print_exc()
            set_job("export:" + r.name, stage="ошибка", error=str(e), done=True)

    threading.Thread(target=work, daemon=True).start()
    return {"status": "запущено", "file": out}


def _export_with_score(r: ExportReq):
    """Экспорт с табло: на каждый кусок накладывается счёт на этот момент."""
    setup = _setup_of(r.name)
    saved = store.load(r.name, "score.json") or {}
    rallies = r.rallies or (store.load(r.name, "segments.json") or {}).get("rallies", [])
    winners = (saved.get("winners") or [])[:len(rallies)]
    winners += [None] * (len(rallies) - len(winners))
    if not rallies:
        raise HTTPException(400, "нет розыгрышей")
    if all(w is None for w in winners):
        raise HTTPException(400, "счёт не размечен: отметьте победителей очков")

    meta = signals.probe(src_path(r.name))
    states = scoring.timeline(winners, setup)
    plan = (scorerender.plan_rallies(states, rallies, setup) if r.mode == "rallies_score"
            else scorerender.plan_full(states, rallies, meta["duration"], setup))

    os.makedirs(store.OUT_DIR, exist_ok=True)
    base = os.path.splitext(r.name)[0]
    out = os.path.join(store.OUT_DIR, f"{base}_{r.mode}.mp4")

    def work():
        key = "export:" + r.name
        try:
            set_job(key, stage="наложение счёта", progress=0, done=False, error=None)
            scorerender.build(src_path(r.name), plan, setup, out, r.quality,
                              width=meta["width"] or 1920,
                              progress=lambda i, n: set_job(key, progress=int(100 * i / n)))
            set_job(key, stage="готово", progress=100, done=True, file=out)
        except Exception as e:
            traceback.print_exc()
            set_job(key, stage="ошибка", error=str(e), done=True)

    threading.Thread(target=work, daemon=True).start()
    return {"status": "запущено", "file": out}


@app.get("/api/download/{name}")
def api_download(name: str):
    p = os.path.join(store.OUT_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(404, "файл не найден")
    return FileResponse(p, filename=name, media_type="video/mp4")


class NoCacheStatic(StaticFiles):
    """Интерфейс меняется часто — браузер не должен держать старую версию."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


app.mount("/", NoCacheStatic(directory=os.path.join(store.ROOT, "static"), html=True),
          name="static")
