"""Модель «идёт розыгрыш / пауза» и сборка сегментов из её выхода."""
import os, json, pickle, numpy as np
from scipy.ndimage import median_filter
from . import features, store

MODEL_PATH = os.path.join(store.ROOT, "data", "model.pkl")

# thr=None — порог подбирается под уверенность модели на конкретном видео:
# в незнакомых условиях (другой зал, ракурс) она менее уверена, и фиксированный
# порог отрезал бы половину розыгрышей
POST = dict(thr=None, thr_factor=0.25, thr_min=0.15, thr_max=0.45,
            smooth=13, fill=1.4, min_len=1.6, pre_roll=0.0, post_roll=0.3,
            split_len=9.0,    # сегмент длиннее — ищем внутри границу очков
            split_thr=0.45,   # провал ниже — считаем паузой между очками
            split_min=1.5)    # обе части должны быть не короче


def train(datasets, **kw):
    """datasets: [(X, labels)] — признаки и разметка по сетке FPS."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    Xs, ys = [], []
    for X, lab in datasets:
        C = features.with_context(X)
        M = features.with_context(features.mirror_all(X))
        Xs += [C, M]; ys += [lab, lab]          # учим обе ориентации сцены
    clf = HistGradientBoostingClassifier(
        max_iter=kw.get("max_iter", 300), learning_rate=0.08, max_depth=6)
    clf.fit(np.vstack(Xs), np.concatenate(ys))
    return clf


def save(clf, path=MODEL_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(clf, f)


def load(path=MODEL_PATH):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def predict(clf, X):
    return clf.predict_proba(features.with_context(X))[:, 1]


def _split_long(runs, p, c, fps):
    """Длинный отрезок — обычно несколько очков подряд при быстрой подаче.
    Режем его по самым глубоким провалам уверенности."""
    fine = median_filter(p, size=5)
    out = []
    queue = list(runs)
    while queue:
        a, b = queue.pop(0)
        if (b - a) / fps <= c["split_len"]:
            out.append([a, b]); continue
        lo = int(a + c["split_min"] * fps)
        hi = int(b - c["split_min"] * fps)
        if hi <= lo:
            out.append([a, b]); continue
        inner = fine[lo:hi]
        k = int(np.argmin(inner))
        if inner[k] > c["split_thr"]:
            out.append([a, b]); continue
        cut = lo + k
        queue.insert(0, [cut, b])          # правую часть тоже проверяем
        out.append([a, cut])
    out.sort()
    return out


def segments(p, duration, post=None):
    """Вероятности → границы розыгрышей."""
    c = {**POST, **(post or {})}
    fps = features.FPS
    thr = c["thr"]
    if thr is None:
        peak = float(np.percentile(p, 97))
        thr = float(np.clip(c["thr_factor"] * peak, c["thr_min"], c["thr_max"]))
    q = median_filter(p, size=int(c["smooth"]))
    on = q > thr
    runs, a = [], None
    for i, v in enumerate(on):
        if v and a is None:
            a = i
        if not v and a is not None:
            runs.append([a, i]); a = None
    if a is not None:
        runs.append([a, len(on)])

    merged = []
    for r in runs:                                  # короткий провал — не пауза
        if merged and (r[0] - merged[-1][1]) <= c["fill"] * fps:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    merged = _split_long(merged, p, {**c, "thr": thr}, fps)

    out = []
    for a_, b_ in merged:
        if (b_ - a_) / fps < c["min_len"]:
            continue
        s = max(0.0, a_ / fps - c["pre_roll"])
        e = min(duration, b_ / fps + c["post_roll"])
        seg = dict(start=round(s, 2), end=round(e, 2),
                   conf=round(float(p[a_:b_].mean()), 3))
        out.append(seg)

    for a_, b_ in zip(out, out[1:]):                # не даём сегментам наезжать
        if b_["start"] < a_["end"]:
            mid = (a_["end"] + b_["start"]) / 2
            a_["end"], b_["start"] = round(mid - 0.05, 2), round(mid + 0.05, 2)

    for i, r in enumerate(out, 1):
        r["id"] = i
    return out


def rank_highlights(rallies, X):
    """Зрелищность: длина розыгрыша и громкость реакции сразу после него."""
    fps = features.FPS
    audio_peak = X[:, features.NAMES.index("audio_peak")]
    base = float(np.median(audio_peak))
    spread = float(np.percentile(audio_peak, 90) - np.percentile(audio_peak, 10)) or 1.0
    durations = [r["end"] - r["start"] for r in rallies] or [1.0]
    dmax = max(durations)
    for r in rallies:
        a = int(r["end"] * fps)
        b = min(len(audio_peak), int((r["end"] + 2.5) * fps))
        reaction = (float(audio_peak[a:b].max()) - base) / spread if b > a else 0.0
        length = (r["end"] - r["start"]) / dmax
        r["reaction"] = round(reaction, 2)
        r["score"] = round(0.65 * length + 0.35 * max(0.0, min(1.5, reaction)), 3)
    return rallies
