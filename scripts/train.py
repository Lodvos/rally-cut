#!/usr/bin/env python
"""Обучение модели на размеченных видео.

Разметка берётся из data/trainset_*.npz — наборов признаков с метками.
Пополнить набор можно scripts/add_truth.py (по паре «полное видео + нарезка»)
или из ручных правок в приложении.
"""
import sys, os, glob, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import model, store

sets = sorted(glob.glob(os.path.join(store.DATA_DIR, "trainset_*.npz")))
if not sets:
    sys.exit("нет обучающих наборов в data/")
data = []
for p in sets:
    d = np.load(p)
    data.append((d["X"], d["y"]))
    print(f"  {os.path.basename(p)}: {len(d['X'])} точек, розыгрышей {d['y'].mean():.0%} времени")
clf = model.train(data)
model.save(clf)
print(f"модель обучена на {len(data)} видео → {model.MODEL_PATH}")
print(f"признаков в наборе: {data[0][0].shape[1]}")
