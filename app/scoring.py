"""Счёт по правилам настольного тенниса.

Из последовательности победителей очков разворачивается всё состояние матча:
счёт в гейме, счёт по геймам, кто подаёт, кто с какой стороны стола.
"""
from dataclasses import dataclass, asdict


@dataclass
class Setup:
    """Что игрок задаёт один раз перед обработкой."""
    name_a: str = "Игрок A"
    name_b: str = "Игрок B"
    a_starts_left: bool = True       # A в начале матча слева в кадре
    server_a_first: bool = True      # A подаёт первым в матче
    games_to_win: int = 3            # 3 — матч до трёх побед, 4 — до четырёх
    points_to_win: int = 11
    switch_ends: bool = True         # менять стороны между геймами


def _game_over(a, b, need):
    return (a >= need or b >= need) and abs(a - b) >= 2


def _serve_owner(a, b, first_server, need):
    """Кто подаёт при счёте a:b. При 10:10 и дальше подача меняется каждое очко."""
    total = a + b
    if a >= need - 1 and b >= need - 1:
        blocks = (need - 1) * 2 // 2 + (total - (need - 1) * 2)
    else:
        blocks = total // 2
    return first_server if blocks % 2 == 0 else (not first_server)


def timeline(winners, setup: Setup):
    """winners: список 'a'/'b'/None по розыгрышам.

    Возвращает по одному состоянию на розыгрыш: счёт ДО него (он и виден на
    табло во время игры) и счёт ПОСЛЕ, плюс кто подавал и где кто стоял.
    """
    need = setup.points_to_win
    games_a = games_b = 0
    pa = pb = 0
    game_idx = 0
    first_server = setup.server_a_first        # кто подаёт первым в текущем гейме
    a_left = setup.a_starts_left
    out = []

    for i, w in enumerate(winners):
        server_a = _serve_owner(pa, pb, first_server, need)
        state = dict(
            rally=i,
            game=game_idx + 1,
            points_a=pa, points_b=pb,
            games_a=games_a, games_b=games_b,
            server_a=server_a,
            a_left=a_left,
            winner=w,
            match_over=False,
        )

        if w == "a":
            pa += 1
        elif w == "b":
            pb += 1
        # None — переигровка или неразмеченное очко: счёт не меняется

        state["after_a"], state["after_b"] = pa, pb

        if _game_over(pa, pb, need):
            if pa > pb:
                games_a += 1
            else:
                games_b += 1
            state["game_won_by"] = "a" if pa > pb else "b"
            pa = pb = 0
            game_idx += 1
            first_server = not first_server     # в новом гейме подаёт принимавший
            if setup.switch_ends:
                a_left = not a_left
        state["games_after_a"], state["games_after_b"] = games_a, games_b
        if games_a >= setup.games_to_win or games_b >= setup.games_to_win:
            state["match_over"] = True
            out.append(state)
            break
        out.append(state)

    return out


def summary(states, setup: Setup):
    if not states:
        return dict(games_a=0, games_b=0, finished=False, score_line="")
    last = states[-1]
    ga, gb = last["games_after_a"], last["games_after_b"]
    return dict(
        games_a=ga, games_b=gb,
        finished=bool(last.get("match_over")),
        winner=(setup.name_a if ga > gb else setup.name_b) if last.get("match_over") else None,
        score_line=f"{ga}:{gb}",
        rallies=len(states),
    )
