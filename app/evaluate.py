"""Сравнение найденных розыгрышей с эталоном."""
import numpy as np


def overlap(a, b):
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def score(pred, truth, min_iou=0.3):
    """Полнота, точность и аккуратность границ."""
    P = [(p["start"], p["end"]) for p in pred]
    T = [(t[0], t[1]) if isinstance(t, (list, tuple)) else (t["start"], t["end"]) for t in truth]
    matched, used = [], set()
    for ti, t in enumerate(T):
        best, bi = 0.0, None
        for pi, p in enumerate(P):
            if pi in used:
                continue
            ov = overlap(p, t)
            iou = ov / (max(p[1], t[1]) - min(p[0], t[0]) + 1e-9)
            if ov > 0 and iou > best:
                best, bi = iou, pi
        if bi is not None and best >= min_iou:
            used.add(bi); matched.append((ti, bi, best))
    rec = len(matched) / max(1, len(T))
    prec = len(matched) / max(1, len(P))
    # сколько эталонных розыгрышей склеено в один предсказанный
    merges = sum(1 for pi in used if sum(1 for t in T if overlap(P[pi], t) > 0.3) > 1)
    ds = [P[pi][0] - T[ti][0] for ti, pi, _ in matched]
    de = [P[pi][1] - T[ti][1] for ti, pi, _ in matched]
    # покрытие по времени
    total_t = sum(b - a for a, b in T)
    covered = sum(sum(overlap(p, t) for p in P) for t in T)
    return dict(
        truth=len(T), pred=len(P), matched=len(matched),
        recall=round(rec, 3), precision=round(prec, 3),
        merged=merges,
        start_err=round(float(np.median(ds)), 2) if ds else None,
        end_err=round(float(np.median(de)), 2) if de else None,
        time_recall=round(covered / max(1e-9, total_t), 3),
    )


def report(s):
    return (f"эталон {s['truth']}, найдено {s['pred']}, совпало {s['matched']}  |  "
            f"полнота {s['recall']:.0%}, точность {s['precision']:.0%}, "
            f"склеек {s['merged']}  |  время: покрыто {s['time_recall']:.0%}, "
            f"сдвиг начала {s['start_err']}с, конца {s['end_err']}с")
