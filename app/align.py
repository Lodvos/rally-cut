"""Восстановление разметки из пары «полный матч + ручная нарезка».

Каждый кадр нарезки ищется в исходнике; непрерывные участки соответствия —
это и есть розыгрыши, оставленные при монтаже.
"""
import numpy as np, subprocess

FPS = 10
SW, SH = 48, 27
MERGE_GAP = 1.2      # разрыв короче — артефакт сопоставления, а не пауза
MIN_LEN = 0.4


def signature(path):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"fps={FPS},scale={SW}:{SH},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    a = np.frombuffer(p.stdout, np.uint8)
    n = len(a) // (SW * SH)
    a = a[:n * SW * SH].reshape(n, SW * SH).astype(np.float32)
    a -= a.mean(axis=1, keepdims=True)
    a /= (np.linalg.norm(a, axis=1, keepdims=True) + 1e-6)
    return a


def align(full_path, cut_path):
    """Возвращает (сегменты в исходнике, качество совпадения)."""
    S, L = signature(cut_path), signature(full_path)
    best = np.empty(len(S), int)
    score = np.empty(len(S), np.float32)
    for i in range(0, len(S), 500):
        sim = S[i:i + 500] @ L.T
        best[i:i + 500] = sim.argmax(axis=1)
        score[i:i + 500] = sim.max(axis=1)

    runs, a = [], 0
    for i in range(1, len(best)):
        if best[i] - best[i - 1] != 1:
            runs.append((a, i - 1)); a = i
    runs.append((a, len(best) - 1))

    segs = [[best[i] / FPS, best[j] / FPS] for i, j in runs]
    merged = [segs[0]] if segs else []
    for s in segs[1:]:
        if s[0] - merged[-1][1] <= MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], s[1])
        else:
            merged.append(s)
    merged = [s for s in merged if s[1] - s[0] >= MIN_LEN]
    return merged, float(np.median(score))
