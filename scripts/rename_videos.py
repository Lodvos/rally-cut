#!/usr/bin/env python
"""Приведение имён видео к единому виду: <дата>_<номер в этот день>[_счёт][_суффикс].

Дата стоит первой, чтобы обычная сортировка по имени давала порядок
сначала по дню съёмки, потом по номеру матча внутри дня.

Дата съёмки берётся как более ранняя из метаданных файла и времени изменения:
метаданные иногда переписываются при копировании с телефона.

  python scripts/rename_videos.py          — показать план
  python scripts/rename_videos.py --apply  — переименовать
"""
import sys, os, re, json, subprocess, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import store

EXT = (".mov", ".mp4", ".m4v", ".mkv")
SCORE_SUFFIX = re.compile(r"_(\d)-(\d)$")
# производные файлы: <матч>_short и т.п. наследуют имя исходника
DERIVED = {"1_short.MOV": "1_long.mov"}


def shot_time(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip().split(";")[0]
    meta = None
    if out:
        try:
            meta = dt.datetime.fromisoformat(out.replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    return min([t for t in (meta, mtime) if t])


def score_in_name(name):
    m = SCORE_SUFFIX.search(strip_parts(os.path.splitext(name)[0])[0] + "")
    m = SCORE_SUFFIX.search(os.path.splitext(name)[0].replace("_short", ""))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def known_score(name):
    """Счёт матча: из разметки, из текущего имени или из сохранённой пометки."""
    sc = store.load(name, "score.json") or {}
    winners = sc.get("winners") or []
    if winners and all(w for w in winners):
        from app import scoring
        setup = scoring.Setup(**{k: v for k, v in (sc.get("setup") or {}).items()
                                 if k in scoring.Setup.__annotations__})
        summary = scoring.summary(scoring.timeline(winners, setup), setup)
        if summary["games_a"] or summary["games_b"]:
            return f"{summary['games_a']}-{summary['games_b']}"
    in_name = score_in_name(name)
    if in_name:
        return in_name
    src = store.load(name, "source.json") or {}
    if src.get("known_score"):
        return src["known_score"].replace(":", "-")
    return None


def strip_parts(stem):
    """Убирает из имени уже проставленные счёт и суффикс вида _short."""
    suffix = ""
    for tail in ("_short", "_highlights", "_rallies"):
        if stem.endswith(tail):
            suffix, stem = tail, stem[: -len(tail)]
            break
    stem = SCORE_SUFFIX.sub("", stem)
    return stem, suffix


def plan():
    files = [f for f in sorted(os.listdir(store.VIDEO_DIR))
             if f.lower().endswith(EXT) and not f.startswith(".")]
    times = {f: shot_time(os.path.join(store.VIDEO_DIR, f)) for f in files}
    derived = dict(DERIVED)
    for f in files:
        stem, suffix = strip_parts(os.path.splitext(f)[0])
        if suffix == "_short":
            parent = next((g for g in files
                           if strip_parts(os.path.splitext(g)[0]) == (stem, "")), None)
            if parent:
                derived[f] = parent
    matches = [f for f in files if f not in derived]
    by_day = {}
    for f in matches:
        by_day.setdefault(times[f].date(), []).append(f)

    mapping = {}
    for day, fs in sorted(by_day.items()):
        for i, f in enumerate(sorted(fs, key=lambda x: times[x]), 1):
            base = f"{day.isoformat()}_{i}"
            sc = known_score(f)
            if sc:
                base += f"_{sc}"
            mapping[f] = base + os.path.splitext(f)[1].lower()
    for src, parent in derived.items():
        if src in files and parent in mapping:
            stem = os.path.splitext(mapping[parent])[0]
            mapping[src] = f"{stem}_short" + os.path.splitext(src)[1].lower()
    return mapping, times


SCORE_RE = re.compile(r"\((\d)[_:](\d)\)")


def apply(mapping):
    """Переименование вместе с папкой результатов анализа.

    Счёт, зашитый в старое имя вида (2_3), сохраняем — пригодится для сверки
    с тем, что получится при разметке.
    """
    for old, new in mapping.items():
        if old == new:
            continue
        src = os.path.join(store.VIDEO_DIR, old)
        dst = os.path.join(store.VIDEO_DIR, new)
        if os.path.exists(dst):
            print(f"  пропуск: {new} уже есть"); continue
        old_data, new_data = store.dir_for(old), os.path.join(store.DATA_DIR, store.key(new))
        os.rename(src, dst)
        if os.path.isdir(old_data) and os.listdir(old_data) and not os.path.exists(new_data):
            os.rename(old_data, new_data)
        note = store.load(old, "source.json") or {}
        note.setdefault("original_name", old)      # первое имя не затираем
        m = SCORE_RE.search(old) or SCORE_SUFFIX.search(os.path.splitext(old)[0])
        if m and not note.get("known_score"):
            note["known_score"] = f"{m.group(1)}:{m.group(2)}"
        store.save(new, "source.json", note)
        print(f"  {old} → {new}" + (f"   счёт из имени: {note['known_score']}" if m else ""))


if __name__ == "__main__":
    mapping, times = plan()
    print("план переименования:\n")
    for old, new in sorted(mapping.items(), key=lambda kv: kv[1]):
        print(f"  {old:24s} → {new:24s}  (снято {times[old]:%Y-%m-%d %H:%M})")
    if "--apply" in sys.argv:
        print("\nпереименование:")
        apply(mapping)
    else:
        print("\nдля применения: python scripts/rename_videos.py --apply")
