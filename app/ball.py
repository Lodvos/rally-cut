"""Детекция мяча: яркое компактное пятно, летящее по гладкой траектории.

Игровой «коридор» (зона, где реально летает мяч на нашем столе) определяется
автоматически по самым надёжным траекториям — это отсекает соседние столы.
"""
import numpy as np, cv2, subprocess

W, H = 960, 540
DIFF_THR = 20
AREA = (6, 200)
MAX_SIDE = 34
MIN_V, MAX_V = 6, 60
GATE = 22
MAX_MISS = 2
MIN_PTS = 5
MIN_DISP = 55
MAX_FIT_ERR = 4.0
BRIGHT_PCT = 99.2


def frames(path, extra=()):
    cmd = ["ffmpeg", "-v", "error", *extra, "-i", path,
           "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, bufsize=W * H * 8)
    n = W * H
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(H, W)
    finally:
        p.stdout.close()
        if p.poll() is None:          # потребитель мог прерваться раньше
            p.kill()
        p.wait()


def candidates(prev, cur, nxt, bright):
    a, b, c = prev.astype(np.int16), cur.astype(np.int16), nxt.astype(np.int16)
    d = np.minimum(np.abs(b - a), np.abs(b - c)).astype(np.uint8)
    m = ((d > DIFF_THR) & (cur > bright)).astype(np.uint8)
    n, lab, st, ct = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for j in range(1, n):
        if not (AREA[0] <= st[j, cv2.CC_STAT_AREA] <= AREA[1]):
            continue
        if st[j, cv2.CC_STAT_WIDTH] > MAX_SIDE or st[j, cv2.CC_STAT_HEIGHT] > MAX_SIDE:
            continue
        x, y = int(ct[j][0]), int(ct[j][1])
        out.append((float(ct[j][0]), float(ct[j][1]),
                    float(cur[max(0, y - 2):y + 3, max(0, x - 2):x + 3].max())))
    return out


def fit_error(pts):
    t = np.array([p[0] for p in pts], float)
    x = np.array([p[1] for p in pts]); y = np.array([p[2] for p in pts])
    t = t - t[0]
    ex = np.sqrt(((np.polyval(np.polyfit(t, x, 1), t) - x) ** 2).mean())
    ey = np.sqrt(((np.polyval(np.polyfit(t, y, 2), t) - y) ** 2).mean())
    return ex, ey


class Tracker:
    def __init__(self):
        self.active, self.done, self.prev = [], [], []

    def step(self, i, cands):
        used = set()
        for tr in self.active:
            lt, lx, ly = tr["pts"][-1][:3]
            vx, vy = tr["v"]
            px, py = lx + vx * (i - lt), ly + vy * (i - lt)
            best, bd = None, 1e9
            for k, (x, y, b) in enumerate(cands):
                if k in used:
                    continue
                dist = np.hypot(x - px, y - py)
                if dist < bd and dist < GATE:
                    best, bd = k, dist
            if best is None:
                tr["miss"] += 1
                continue
            x, y, b = cands[best]; used.add(best)
            dt = max(1, i - lt)
            tr["v"] = ((x - lx) / dt, (y - ly) / dt)
            tr["pts"].append((i, x, y, b)); tr["miss"] = 0

        for tr in self.active[:]:
            if tr["miss"] > MAX_MISS:
                self.active.remove(tr); self._retire(tr)

        for k, (x, y, b) in enumerate(cands):
            if k in used:
                continue
            for (px, py, pb) in self.prev:
                if MIN_V <= np.hypot(x - px, y - py) <= MAX_V:
                    self.active.append({"pts": [(i - 1, px, py, pb), (i, x, y, b)],
                                        "v": (x - px, y - py), "miss": 0})
                    break
        self.prev = cands

    def _retire(self, tr):
        p = tr["pts"]
        if len(p) < MIN_PTS:
            return
        if np.hypot(p[-1][1] - p[0][1], p[-1][2] - p[0][2]) < MIN_DISP:
            return
        ex, ey = fit_error(p)
        if ex > MAX_FIT_ERR or ey > MAX_FIT_ERR:
            return
        self.done.append(p)

    def finish(self):
        for tr in self.active:
            self._retire(tr)
        self.active = []
        self.done.sort(key=lambda p: -len(p))
        keep, occ = [], set()
        for p in self.done:
            k = {(q[0], round(q[1] / 8), round(q[2] / 8)) for q in p}
            if len(k & occ) > 0.3 * len(k):
                continue
            occ |= k; keep.append(p)
        keep.sort(key=lambda p: p[0][0])
        return keep


def play_corridor(tracks, min_pts=8, min_bright=None):
    """Зона игры по самым надёжным траекториям: маска, куда реально летает мяч."""
    heat = np.zeros((H, W), np.float32)
    for p in tracks:
        if len(p) < min_pts:
            continue
        if min_bright is not None and np.median([q[3] for q in p]) < min_bright:
            continue
        for k in range(1, len(p)):
            cv2.line(heat, (int(p[k - 1][1]), int(p[k - 1][2])),
                     (int(p[k][1]), int(p[k][2])), 1.0, 3)
    if heat.max() == 0:
        return np.ones((H, W), np.uint8)
    heat = cv2.GaussianBlur(heat, (0, 0), 25)
    mask = (heat > 0.12 * heat.max()).astype(np.uint8)
    return cv2.dilate(mask, np.ones((31, 31), np.uint8))


def in_corridor(p, mask, frac=0.6):
    hit = sum(1 for q in p if mask[int(np.clip(q[2], 0, H - 1)),
                                   int(np.clip(q[1], 0, W - 1))])
    return hit >= frac * len(p)


def detect(path, fps, progress=None):
    tk = Tracker()
    buf, bright, i = [], None, 0
    for f in frames(path):
        buf.append(f)
        if bright is None:
            bright = float(np.clip(np.percentile(f, BRIGHT_PCT), 140, 235))
        if len(buf) < 3:
            continue
        tk.step(i, candidates(buf[0], buf[1], buf[2], bright))
        buf.pop(0); i += 1
        if i % 1800 == 0:
            bright = float(np.clip(np.percentile(buf[-1], BRIGHT_PCT), 140, 235))
            if progress:
                progress(i)
    tracks = tk.finish()
    strict = float(np.clip(np.percentile(buf[-1], 99.7), 150, 240))
    mask = play_corridor(tracks, min_pts=8, min_bright=strict)
    out = []
    for p in tracks:
        if not in_corridor(p, mask):
            continue
        out.append(dict(start=p[0][0] / fps, end=p[-1][0] / fps, n=len(p),
                        bright=round(float(np.median([q[3] for q in p])), 1),
                        pts=[(round(q[0] / fps, 3), round(q[1], 1), round(q[2], 1)) for q in p]))
    return out, mask
