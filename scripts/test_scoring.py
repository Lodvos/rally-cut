#!/usr/bin/env python
"""Проверка правил счёта на разобранных вручную случаях."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.scoring import Setup, timeline, summary

ok = True
def check(name, got, want):
    global ok
    if got != want:
        ok = False
        print(f"  ПРОВАЛ {name}: получено {got}, ожидалось {want}")
    else:
        print(f"  ок  {name}")

s = Setup(games_to_win=3)

# подача переходит каждые два очка
st = timeline(["a"] * 6, s)
check("подача: очки 1-2 у A", [x["server_a"] for x in st[:2]], [True, True])
check("подача: очки 3-4 у B", [x["server_a"] for x in st[2:4]], [False, False])
check("подача: очки 5-6 у A", [x["server_a"] for x in st[4:6]], [True, True])

# гейм до 11 в два очка
st = timeline(["a"] * 11, s)
check("гейм выигран при 11:0", st[-1]["games_after_a"], 1)
check("счёт обнулился", (st[-1]["after_a"], st[-1]["after_b"]), (11, 0))

# при 10:10 подача меняется каждое очко
w = ["a", "b"] * 10          # 10:10 после 20 очков
st = timeline(w + ["a", "a"], s)
srv = [x["server_a"] for x in st[20:22]]
check("при 10:10 подача чередуется", srv[0] != srv[1], True)
check("гейм не окончен при 11:10", st[20]["games_after_a"], 0)
check("гейм окончен при 12:10", st[21]["games_after_a"], 1)

# смена сторон между геймами
st = timeline(["a"] * 11 + ["b"] * 11, s)
check("стороны поменялись", st[0]["a_left"] != st[11]["a_left"], True)
check("в новом гейме подаёт принимавший", st[11]["server_a"], False)

# матч заканчивается на третьей победе
st = timeline(["a"] * 33, s)
check("матч окончен после 3 геймов", st[-1]["match_over"], True)
check("лишние очки не считаются", len(st), 33)
check("итог 3:0", summary(st, s)["score_line"], "3:0")

# неразмеченное очко не ломает счёт
st = timeline(["a", None, "a"], s)
check("None не меняет счёт", (st[-1]["after_a"], st[-1]["after_b"]), (2, 0))

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
