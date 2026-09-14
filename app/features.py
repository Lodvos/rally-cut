"""Признаки для распознавания розыгрышей: мяч, движение игроков, звук.

Один проход по видео даёт и траектории мяча, и картину движения.
Частота признаков — FPS точек в секунду.
"""
import numpy as np, cv2, subprocess
from scipy.signal import stft
from . import ball, signals

FPS = 10                 # частота сетки признаков
MW, MH = 480, 270        # разрешение для анализа движения
FG_THR = 28              # порог отделения фигур от фона
MV_THR = 16              # порог межкадрового движения
N_FEAT = 20

NAMES = ["fg_all", "mv_all",
         "fgL", "yL", "xL", "mvL", "fgC", "yC", "xC", "mvC", "fgR", "yR", "xR", "mvR",
         "ball_on", "ball_cnt", "ball_len", "ball_dist", "audio", "audio_peak"]


def background(path, duration):
    """Фон сцены — медиана редких кадров."""
    step = max(1.0, duration / 400)
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"fps=1/{step},scale={MW}:{MH},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    fr = np.frombuffer(p.stdout, np.uint8)
    n = len(fr) // (MW * MH)
    if n == 0:
        return np.zeros((MH, MW), np.float32)
    return np.median(fr[:n * MW * MH].reshape(n, MH, MW).astype(np.float32), axis=0)


def motion_features(path, bg, n_slots, fps_video):
    """Доля фигур и движения по третям кадра + их центры масс."""
    out = np.zeros((n_slots, 14), np.float32)
    cnt = np.zeros(n_slots, np.float32)
    step = max(1, int(round(fps_video / FPS)))
    prev = None
    i = 0
    for f in ball.frames(path):
        if i % step:
            i += 1
            continue
        slot = int(i / fps_video * FPS)
        i += 1
        if slot >= n_slots:
            break
        g = cv2.resize(f, (MW, MH), interpolation=cv2.INTER_AREA).astype(np.float32)
        fgm = np.abs(g - bg) > FG_THR
        mvm = (np.abs(g - prev) > MV_THR) if prev is not None else np.zeros_like(fgm)
        prev = g
        row = [fgm.mean(), mvm.mean()]
        for x0, x1 in ((0, MW // 3), (MW // 3, 2 * MW // 3), (2 * MW // 3, MW)):
            sub = fgm[:, x0:x1]
            row.append(sub.mean())
            ys, xs = np.nonzero(sub)
            row.append(ys.mean() / MH if len(ys) else 0.5)
            row.append(xs.mean() / (x1 - x0) if len(xs) else 0.5)
            row.append(mvm[:, x0:x1].mean())
        out[slot] += row
        cnt[slot] += 1
    cnt[cnt == 0] = 1
    return out / cnt[:, None]


def ball_features(flights, n_slots):
    on = np.zeros(n_slots, np.float32)
    ln = np.zeros(n_slots, np.float32)
    cnt = np.zeros(n_slots, np.float32)
    for f in flights:
        a, b = int(f["start"] * FPS), int(f["end"] * FPS) + 1
        a, b = max(0, a), min(n_slots, b)
        on[a:b] = 1
        ln[a:b] = np.maximum(ln[a:b], f["n"])
        cnt[max(0, a - 10):min(n_slots, b + 10)] += 1
    idx = np.flatnonzero(on)
    dist = np.full(n_slots, 9.9, np.float32)
    if len(idx):
        grid = np.arange(n_slots)
        pos = np.searchsorted(idx, grid)
        left = idx[np.clip(pos - 1, 0, len(idx) - 1)]
        right = idx[np.clip(pos, 0, len(idx) - 1)]
        dist = np.minimum(np.abs(grid - left), np.abs(grid - right)) / FPS
    return np.column_stack([on, cnt, ln, dist])


def audio_features(path, n_slots):
    y = signals.audio_mono(path)
    f, t, Z = stft(y, fs=signals.SR, nperseg=512, noverlap=384)
    e = np.abs(Z)[(f >= 2000) & (f < 7000)].mean(axis=0)
    hop = t[1] - t[0]
    out = np.zeros((n_slots, 2), np.float32)
    for i in range(n_slots):
        a, b = int(i / FPS / hop), int((i + 1) / FPS / hop)
        seg = e[a:b] if b > a else e[a:a + 1]
        if len(seg) == 0:
            seg = np.array([1e-8])
        out[i] = (np.log(seg.mean() + 1e-8), np.log(seg.max() + 1e-8))
    return out


def scan(path, meta, bg, progress=None):
    """Один проход по видео: траектории мяча и признаки движения."""
    n_slots = int(meta["duration"] * FPS) + 1
    fps_video = meta["fps"]
    step = max(1, int(round(fps_video / FPS)))

    tk = ball.Tracker()
    mo = np.zeros((n_slots, 14), np.float32)
    cnt = np.zeros(n_slots, np.float32)
    buf, bright, prev, i = [], None, None, 0
    total = max(1, int(meta["duration"] * fps_video))

    for f in ball.frames(path):
        if bright is None:
            bright = float(np.clip(np.percentile(f, ball.BRIGHT_PCT), 140, 235))
        buf.append(f)
        if len(buf) == 3:
            tk.step(i, ball.candidates(buf[0], buf[1], buf[2], bright))
            buf.pop(0)
            i += 1
            if i % 1800 == 0:
                bright = float(np.clip(np.percentile(buf[-1], ball.BRIGHT_PCT), 140, 235))
                if progress:
                    progress("поиск мяча и движения", 15 + int(65 * i / total))
        if (i + 1) % step == 0:
            slot = int(i / fps_video * FPS)
            if slot < n_slots:
                g = cv2.resize(f, (MW, MH), interpolation=cv2.INTER_AREA).astype(np.float32)
                fgm = np.abs(g - bg) > FG_THR
                mvm = (np.abs(g - prev) > MV_THR) if prev is not None else np.zeros_like(fgm)
                prev = g
                row = [fgm.mean(), mvm.mean()]
                for x0, x1 in ((0, MW // 3), (MW // 3, 2 * MW // 3), (2 * MW // 3, MW)):
                    sub = fgm[:, x0:x1]
                    row.append(sub.mean())
                    ys, xs = np.nonzero(sub)
                    row.append(ys.mean() / MH if len(ys) else 0.5)
                    row.append(xs.mean() / (x1 - x0) if len(xs) else 0.5)
                    row.append(mvm[:, x0:x1].mean())
                mo[slot] += row
                cnt[slot] += 1

    tracks = tk.finish()
    mask = ball.play_corridor(tracks, min_pts=8,
                              min_bright=float(np.clip(bright + 15, 150, 240)))
    flights = [dict(start=p_[0][0] / fps_video, end=p_[-1][0] / fps_video, n=len(p_),
                    pts=[(round(q[0] / fps_video, 3), round(q[1], 1), round(q[2], 1))
                         for q in p_])
               for p_ in tracks if ball.in_corridor(p_, mask)]
    cnt[cnt == 0] = 1
    return flights, mo / cnt[:, None], n_slots


def extract(path, meta, progress=None, with_pose=True):
    """Полный набор признаков и найденные траектории мяча."""
    if progress: progress("фон сцены", 5)
    bg = background(path, meta["duration"])
    flights, mo, n = scan(path, meta, bg, progress)
    if progress: progress("звук", 70)
    au = audio_features(path, n)
    bl = ball_features(flights, n)
    X = np.column_stack([mo, bl, au]).astype(np.float32)
    ident = None
    if with_pose:
        from . import pose
        if progress: progress("позы игроков", 75)
        P, obs, shots = pose.extract(path, meta, fps_out=FPS, progress=progress,
                                     collect_identity=True)
        ident = (obs, shots)
        P = P[:len(X)] if len(P) >= len(X) else np.vstack(
            [P, np.repeat(P[-1:], len(X) - len(P), axis=0)])
        X = np.column_stack([X, P]).astype(np.float32)
    return X, flights, ident


def mirror_all(X):
    """Зеркалит и базовые признаки, и признаки поз, если они есть."""
    base = mirror(X[:, :N_FEAT])
    if X.shape[1] <= N_FEAT:
        return base
    from . import pose
    return np.column_stack([base, pose.mirror(X[:, N_FEAT:])])


def mirror(X):
    """Зеркальное отражение сцены: игроки меняются сторонами."""
    M = X.copy()
    for a, b in ((2, 10), (3, 11), (5, 13)):
        M[:, a], M[:, b] = X[:, b].copy(), X[:, a].copy()
    M[:, 4], M[:, 12] = 1 - X[:, 12], 1 - X[:, 4]
    M[:, 8] = 1 - X[:, 8]
    return M


OFFSETS = (-15, -8, -3, 0, 3, 8, 15)


def with_context(X):
    """К каждому моменту добавляем картину за ±1.5 секунды."""
    return np.hstack([np.roll(X, -o, axis=0) for o in OFFSETS])
