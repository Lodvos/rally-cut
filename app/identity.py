"""Кто из игроков где стоит — по цвету формы, а не по счёту.

Форма у игроков разная, поэтому наблюдения за весь матч делятся на две группы,
и в каждый момент известно, кто слева, а кто справа. Это не зависит от того,
правильно ли размечен счёт.
"""
import numpy as np, cv2, os

MIN_OBS = 30
MIN_SEPARABLE = 0.70   # если игроков реже различаем — форма у них слишком похожа
MIN_HOLD = 45.0     # стороны меняются между геймами, а не каждые полминуты
BREAK_GAP = 12.0    # пауза длиннее — перерыв между геймами
BREAK_SEARCH = 90.0 # как далеко искать такой перерыв от оценённого момента


def split_players(obs):
    """obs: [(slot, 'L'|'R', цвет)] → (центр цвета игрока 1, центр игрока 2)."""
    if len(obs) < MIN_OBS:
        return None
    X = np.array([o[2] for o in obs], np.float32)
    # две группы по цвету формы
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(X, 2, None, crit, 8, cv2.KMEANS_PP_CENTERS)
    labels = labels.ravel()
    if min((labels == 0).sum(), (labels == 1).sum()) < MIN_OBS // 3:
        return None
    sep = float(np.linalg.norm(centers[0] - centers[1]))

    # главная проверка: двое игроков из одного кадра должны попадать
    # в разные группы. Если этого не происходит — форма у них похожая,
    # и различать их по цвету нельзя.
    by_slot = {}
    for k, (slot, side, col) in enumerate(obs):
        by_slot.setdefault(slot, {})[side] = labels[k]
    pairs = [v for v in by_slot.values() if len(v) == 2]
    separable = (sum(1 for v in pairs if v["L"] != v["R"]) / len(pairs)) if pairs else 0.0

    return dict(centers=centers.tolist(), labels=labels.tolist(), separation=sep,
                separable=round(float(separable), 3),
                reliable=bool(separable >= MIN_SEPARABLE))


def side_timeline(obs, split, n_slots, fps=10):
    """Для каждого момента: 0 — игрок 1 слева, 1 — игрок 2 слева, -1 — неизвестно."""
    centers = np.array(split["centers"], np.float32)
    who_left = np.full(n_slots, -1, np.int8)
    votes = {}
    for (slot, side, col) in obs:
        d = np.linalg.norm(centers - col, axis=1)
        who = int(np.argmin(d))
        left_player = who if side == "L" else 1 - who
        votes.setdefault(slot, []).append(left_player)
    for slot, vs in votes.items():
        if slot < n_slots:
            who_left[slot] = 1 if sum(vs) * 2 > len(vs) else 0
    # сглаживаем: стороны меняются редко, одиночные выбросы — ошибки
    known = np.flatnonzero(who_left >= 0)
    if len(known):
        filled = who_left.copy()
        last = who_left[known[0]]
        for i in range(n_slots):
            if who_left[i] >= 0:
                last = who_left[i]
            filled[i] = last
        w = 201
        pad = np.pad(filled.astype(np.float32), w // 2, mode="edge")
        sm = np.convolve(pad, np.ones(w) / w, mode="valid")[:n_slots]
        who_left = (sm > 0.5).astype(np.int8)
        who_left = _drop_short(who_left, int(MIN_HOLD * fps))
    return who_left


def _drop_short(seq, min_len):
    """Схлопывает слишком короткие участки — игроки так часто не меняются."""
    if len(seq) == 0:
        return seq
    runs, start = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            runs.append([start, i, int(seq[start])]); start = i
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, (a, b, v) in enumerate(runs):
            if b - a >= min_len:
                continue
            # короткий участок присоединяем к более длинному соседу
            left = runs[k - 1] if k > 0 else None
            right = runs[k + 1] if k + 1 < len(runs) else None
            take = left if (right is None or (left and (left[1] - left[0]) >= (right[1] - right[0]))) else right
            runs[k][2] = take[2]
            merged = []
            for r in runs:
                if merged and merged[-1][2] == r[2]:
                    merged[-1][1] = r[1]
                else:
                    merged.append(list(r))
            runs = merged; changed = True
            break
    out = np.empty(len(seq), np.int8)
    for a, b, v in runs:
        out[a:b] = v
    return out


def align_switches(who_left, rallies, fps):
    """Смена сторон бывает только в паузе — двигаем границы туда.

    Сглаживание смещает момент на секунду-другую, и тогда подпись перескакивает
    на розыгрыше, который шёл ещё до смены.
    """
    if not rallies or len(who_left) == 0:
        return who_left
    out = who_left.copy()
    n = len(who_left)
    gaps = []                       # паузы между розыгрышами
    for a, b in zip(rallies, rallies[1:]):
        gaps.append((a["end"], b["start"]))
    if not gaps:
        return out
    breaks = [g for g in gaps if g[1] - g[0] >= BREAK_GAP]
    for i in range(1, n):
        if out[i] == out[i - 1]:
            continue
        t = i / fps
        # стороны меняют в перерыве между геймами — это длинная пауза
        near = [g for g in breaks if abs((g[0] + g[1]) / 2 - t) <= BREAK_SEARCH]
        pool = near or gaps
        gs, ge = min(pool, key=lambda g: abs((g[0] + g[1]) / 2 - t))
        target = int((gs + ge) / 2 * fps)
        lo, hi = min(i, target), max(i, target)
        out[lo:hi] = out[i - 1] if target > i else out[i]
    return out


def switch_points(who_left, fps):
    """Моменты, когда игроки поменялись сторонами (в секундах)."""
    out = []
    for i in range(1, len(who_left)):
        if who_left[i] != who_left[i - 1]:
            out.append(round(i / fps, 1))
    return out


def _snapshot_score(crop):
    """Насколько кадр годится как портрет: фигура целиком, крупно, не размыта."""
    h, w = crop.shape[:2]
    if h < 80 or w < 30:
        return -1.0
    ratio = h / w
    if not (1.4 <= ratio <= 3.4):          # обрезанная или скособоченная фигура
        return -1.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return h * min(sharp, 400.0)


def save_snapshots(shots, split, out_dir, max_side=260):
    """По одному снимку на игрока: фигура целиком, крупная и чёткая."""
    centers = np.array(split["centers"], np.float32)
    best = {}
    for area, slot, side, col, crop in shots:
        if crop is None or crop.size == 0:
            continue
        q = _snapshot_score(crop)
        if q <= 0:
            continue
        who = int(np.argmin(np.linalg.norm(centers - col, axis=1)))
        if who not in best or q > best[who][0]:
            best[who] = (q, crop)
    paths = {}
    os.makedirs(out_dir, exist_ok=True)
    for who, (area, crop) in best.items():
        h, w = crop.shape[:2]
        k = min(max_side / max(h, 1), max_side / max(w, 1), 1.0)
        img = cv2.resize(crop, (max(1, int(w * k)), max(1, int(h * k))))
        p = os.path.join(out_dir, f"player{who + 1}.jpg")
        cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        paths[who] = p
    return paths
