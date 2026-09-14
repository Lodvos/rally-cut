"""Признаки из поз игроков.

YOLO-pose находит людей в кадре; из всех выбираются двое играющих за нашим
столом (самые крупные фигуры слева и справа). По их скелетам считается, что
игрок делает: стоит в стойке, тянется за мячом, подаёт, уходит от стола.
"""
import numpy as np, subprocess, os

MODEL = "yolo11n-pose.pt"
W, H = 960, 540
IMGSZ, BATCH = 640, 32
CONF = 0.35
MIN_HEIGHT = 90          # фигура ниже — зритель или игрок с соседнего стола

# индексы ключевых точек COCO
NOSE, LSH, RSH, LEL, REL, LWR, RWR, LHP, RHP, LKN, RKN, LAN, RAN = 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16

PER_PLAYER = 11
NAMES = []
for side in ("L", "R"):
    NAMES += [f"{side}_x", f"{side}_y", f"{side}_h", f"{side}_vx", f"{side}_vy",
              f"{side}_speed", f"{side}_wrist_up", f"{side}_wrist_sp",
              f"{side}_lean", f"{side}_stance", f"{side}_seen"]
NAMES += ["pair_dist", "pair_speed", "n_people"]
N_FEAT = len(NAMES)


def frames_bgr(path):
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={W}:{H}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3 * 8)
    n = W * H * 3
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        p.stdout.close()
        if p.poll() is None:
            p.kill()
        p.wait()


def pick_players(boxes, confs):
    """Двое играющих: самые крупные фигуры, разнесённые по сторонам кадра."""
    good = [i for i in range(len(boxes))
            if confs[i] >= CONF and (boxes[i][3] - boxes[i][1]) >= MIN_HEIGHT]
    if not good:
        return None, None
    # играющие ближе к камере, поэтому их фигуры крупнее остальных
    good.sort(key=lambda i: -(boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
    top = good[:2]
    cx = {i: (boxes[i][0] + boxes[i][2]) / 2 for i in top}
    if len(top) == 1:
        only = top[0]
        return (only, None) if cx[only] < W / 2 else (None, only)
    a, b = sorted(top, key=lambda i: cx[i])
    return a, b


def player_row(box, kp, prev):
    """11 чисел про одного игрока; prev — его центр на прошлом шаге."""
    if box is None:
        return [0.5, 0.5, 0.0, 0, 0, 0, 0, 0, 0, 0, 0], None
    x0, y0, x1, y1 = box
    h = (y1 - y0) / H
    cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
    vx = vy = 0.0
    if prev is not None:
        vx, vy = cx - prev[0], cy - prev[1]
    speed = float(np.hypot(vx, vy))

    def pt(i):
        return kp[i] if kp is not None and i < len(kp) and kp[i][0] > 0 else None

    sh_l, sh_r, hp_l, hp_r = pt(LSH), pt(RSH), pt(LHP), pt(RHP)
    wr_l, wr_r, an_l, an_r = pt(LWR), pt(RWR), pt(LAN), pt(RAN)

    # рука поднята выше плеч — замах, подача, удар
    wrist_up = 0.0
    sh_y = np.mean([p[1] for p in (sh_l, sh_r) if p is not None]) if (sh_l is not None or sh_r is not None) else None
    if sh_y is not None:
        ws = [p[1] for p in (wr_l, wr_r) if p is not None]
        if ws:
            wrist_up = float((sh_y - min(ws)) / max(1.0, y1 - y0))

    # скорость кистей — главный признак активной игры
    wrist_sp = 0.0
    if prev is not None and prev[2] is not None:
        cur = [p for p in (wr_l, wr_r) if p is not None]
        old = prev[2]
        if cur and old:
            d = [np.hypot(c[0] - o[0], c[1] - o[1]) for c, o in zip(cur, old[:len(cur)])]
            wrist_sp = float(np.mean(d)) / max(1.0, y1 - y0)

    # наклон корпуса: в стойке игрок согнут вперёд
    lean = 0.0
    if sh_l is not None and sh_r is not None and hp_l is not None and hp_r is not None:
        sx = (sh_l[0] + sh_r[0]) / 2; sy = (sh_l[1] + sh_r[1]) / 2
        hx = (hp_l[0] + hp_r[0]) / 2; hy = (hp_l[1] + hp_r[1]) / 2
        lean = float(abs(sx - hx) / max(1.0, abs(sy - hy) + 1e-6))

    # ширина стойки: расставленные ноги — готовность играть
    stance = 0.0
    if an_l is not None and an_r is not None:
        stance = float(abs(an_l[0] - an_r[0]) / max(1.0, y1 - y0))

    wrists = [p for p in (wr_l, wr_r) if p is not None]
    return ([cx, cy, h, vx, vy, speed, wrist_up, wrist_sp, lean, stance, 1.0],
            (cx, cy, wrists))


def torso_color(frame, box):
    """Средний цвет формы: верхняя треть фигуры без краёв."""
    x0, y0, x1, y1 = [int(v) for v in box]
    h = y1 - y0
    a, b = y0 + int(0.18 * h), y0 + int(0.48 * h)
    dx = int(0.22 * (x1 - x0))
    patch = frame[max(0, a):max(a + 1, b), max(0, x0 + dx):max(x0 + dx + 1, x1 - dx)]
    if patch.size == 0:
        return None
    return np.median(patch.reshape(-1, 3), axis=0).astype(np.float32)


def extract(path, meta, fps_out=10, progress=None, collect_identity=False):
    """Признаки поз на сетке fps_out точек в секунду.

    collect_identity=True дополнительно собирает цвет формы игроков и их снимки,
    чтобы потом различать их по внешнему виду, а не по счёту.
    """
    from ultralytics import YOLO
    model = YOLO(MODEL)
    step = max(1, int(round(meta["fps"] / fps_out)))
    n_slots = int(meta["duration"] * fps_out) + 1
    out = np.zeros((n_slots, N_FEAT), np.float32)
    filled = np.zeros(n_slots, bool)

    batch, slots = [], []
    prevL = prevR = None
    total = max(1, n_slots)
    obs = []          # (slot, сторона, цвет формы)
    shots = []        # кадры-кандидаты для снимков игроков

    def flush():
        nonlocal batch, slots, prevL, prevR
        if not batch:
            return
        raw = batch
        res = model.predict(batch, device="mps", imgsz=IMGSZ, conf=CONF,
                            verbose=False, batch=BATCH)
        for slot, r in zip(slots, res):
            b = r.boxes.xyxy.cpu().numpy() if r.boxes is not None else np.zeros((0, 4))
            c = r.boxes.conf.cpu().numpy() if r.boxes is not None else np.zeros(0)
            k = r.keypoints.xy.cpu().numpy() if r.keypoints is not None else None
            li, ri = pick_players(b, c)
            rowL, prevL = player_row(b[li] if li is not None else None,
                                     k[li] if (k is not None and li is not None) else None, prevL)
            rowR, prevR = player_row(b[ri] if ri is not None else None,
                                     k[ri] if (k is not None and ri is not None) else None, prevR)
            pair_dist = abs(rowL[0] - rowR[0]) if (li is not None and ri is not None) else 0.0
            pair_speed = rowL[5] + rowR[5]
            n_people = float(len([1 for cc in c if cc >= CONF]))
            if slot < n_slots:
                out[slot] = rowL + rowR + [pair_dist, pair_speed, n_people]
                filled[slot] = True
            if collect_identity:
                frame = raw[slots.index(slot)] if slot in slots else None
                for side, idx in (("L", li), ("R", ri)):
                    if idx is None or frame is None:
                        continue
                    col = torso_color(frame, b[idx])
                    if col is None:
                        continue
                    obs.append((slot, side, col))
                    area = (b[idx][2] - b[idx][0]) * (b[idx][3] - b[idx][1])
                    shots.append((area, slot, side, col,
                                  frame[max(0, int(b[idx][1])):int(b[idx][3]),
                                        max(0, int(b[idx][0])):int(b[idx][2])].copy()))
        batch, slots = [], []

    i = 0
    for f in frames_bgr(path):
        if i % step == 0:
            slot = int(i / meta["fps"] * fps_out)
            if slot < n_slots:
                batch.append(f); slots.append(slot)
                if len(batch) >= BATCH:
                    flush()
                    if progress and slot % 300 == 0:
                        progress("позы игроков", int(100 * slot / total))
        i += 1
    flush()

    # пропуски заполняем предыдущим наблюдением
    last = None
    for j in range(n_slots):
        if filled[j]:
            last = out[j]
        elif last is not None:
            out[j] = last
    if collect_identity:
        return out, obs, shots
    return out


def mirror(X):
    """Игроки меняются сторонами: левый блок ↔ правый, координаты x отражаются."""
    M = X.copy()
    L, R = slice(0, PER_PLAYER), slice(PER_PLAYER, 2 * PER_PLAYER)
    M[:, L], M[:, R] = X[:, R].copy(), X[:, L].copy()
    for off in (0, PER_PLAYER):
        M[:, off] = 1 - M[:, off]          # x
        M[:, off + 3] = -M[:, off + 3]     # vx
    return M
