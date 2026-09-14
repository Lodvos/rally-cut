#!/usr/bin/env python
"""Добавить обучающие данные из пары «полный матч + ваша ручная нарезка».

  python scripts/add_truth.py video/матч.mov video/матч_нарезка.mov

Извлекает границы розыгрышей из монтажа, считает признаки полного видео
и складывает набор в data/. Потом: python scripts/train.py
"""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import align, features, signals, store

if len(sys.argv) != 3:
    sys.exit(__doc__)
full, cut = sys.argv[1], sys.argv[2]
for p in (full, cut):
    if not os.path.exists(p):
        sys.exit(f"нет файла: {p}")

print("сопоставляю нарезку с исходником…")
segs, quality = align.align(full, cut)
meta = signals.probe(full)
total = sum(b - a for a, b in segs)
print(f"  найдено {len(segs)} розыгрышей, {total:.0f} с из {meta['duration']:.0f} с"
      f"  (качество совпадения {quality:.3f})")
if quality < 0.9:
    sys.exit("нарезка не похожа на этот исходник — проверьте пару файлов")

print("считаю признаки исходника (это самая долгая часть)…")
X, flights = features.extract(full, meta,
                              progress=lambda s, p: print(f"  {s} {p}%", flush=True))
y = np.zeros(len(X), bool)
for a, b in segs:
    y[int(a * features.FPS):int(b * features.FPS)] = True

name = os.path.splitext(os.path.basename(full))[0]
os.makedirs(store.DATA_DIR, exist_ok=True)
np.savez(os.path.join(store.DATA_DIR, f"trainset_{name}.npz"), X=X, y=y)
json.dump(segs, open(os.path.join(store.DATA_DIR, f"truth_{name}.json"), "w"))
print(f"готово: data/trainset_{name}.npz — теперь запустите python scripts/train.py")
