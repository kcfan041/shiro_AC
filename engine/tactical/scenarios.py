"""批次模擬與測試用的戰術情境。每個情境回傳 (地圖, 單位, 藍方意圖, 紅方意圖)。"""
from __future__ import annotations

from engine.tactical.ai import Intent
from engine.tactical.tmap import TacMap
from engine.tactical.units import Unit


def _squad(side: str, prefix: str, nodes, weapons=None, posture="crouch", facing=(0, 1)):
    weapons = weapons or ["rifle"] * len(nodes)
    return [Unit(id=f"{prefix}{i}", side=side, pos=n, weapon=w, posture=posture, facing=facing)
            for i, (n, w) in enumerate(zip(nodes, weapons))]


def open_field():
    m = TacMap()
    blue = _squad("blue", "b", [(2, 1, 0), (4, 1, 0), (6, 1, 0), (8, 1, 0)], facing=(0, 1))
    red = _squad("red", "r", [(3, 8, 0), (5, 8, 0), (7, 8, 0)], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


def cover_duel():
    m = TacMap()
    for x in (2, 4, 6):
        m.low_wall(x, 2, "N")
        m.low_wall(x, 7, "S")
    blue = _squad("blue", "b", [(2, 2, 0), (4, 2, 0), (6, 2, 0)])
    red = _squad("red", "r", [(2, 7, 0), (4, 7, 0), (6, 7, 0)], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


def _fortified():
    m = TacMap()
    for x in range(1, 9):
        if x in (3, 6):
            m.window_side(x, 8, "S")
        else:
            m.wall_side(x, 8, "S")
    for x in (2, 7):
        m.rubble(x, 4)
    m.veg(0, 5)
    m.veg(0, 6)
    m.veg(9, 5)
    m.veg(9, 6)
    return m


def assault_direct():
    m = _fortified()
    blue = _squad("blue", "b", [(3, 0, 0), (4, 0, 0), (5, 0, 0), (6, 0, 0)], ["lmg", "rifle", "rifle", "dmr"])
    red = _squad("red", "r", [(3, 8, 0), (6, 8, 0)], facing=(0, -1))
    return m, blue + red, Intent("advance", goal=(4, 8, 0)), Intent("hold")


def assault_suppress():
    m, units, _, red = assault_direct()
    return m, units, Intent("supp_advance", goal=(4, 8, 0)), red


def assault_flank():
    m, units, _, red = assault_direct()
    return m, units, Intent("flank"), red


def ambush():
    m = TacMap()
    for y in (4, 5, 6):
        m.veg(1, y)
        m.veg(8, y)
    blue = _squad("blue", "b", [(4, 0, 0), (5, 0, 0), (4, 1, 0), (5, 1, 0)])
    red = _squad("red", "r", [(1, 5, 0), (8, 5, 0)], facing=(0, -1), posture="prone")
    return m, blue + red, Intent("advance", goal=(4, 9, 0)), Intent("ambush", ambush_range=9)


def high_ground():
    m = TacMap()
    for x in (5, 6, 7):
        for y in (6, 7, 8):
            m.platform(x, y)
    m.stair(7, 8)
    for x in (5, 6, 7):
        m.side_panel(x, 6, "S", 0.0, 0.6, thick=0.2, l=1, tag="lowwall")
    for x in (1, 3):
        m.low_wall(x, 2, "N")
    blue = _squad("blue", "b", [(1, 2, 0), (3, 2, 0)])
    red = _squad("red", "r", [(5, 6, 1), (6, 6, 1)], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


def low_ground():
    m = TacMap()
    for x in (5, 6, 7):
        m.low_wall(x, 6, "S")
    for x in (1, 3):
        m.low_wall(x, 2, "N")
    blue = _squad("blue", "b", [(1, 2, 0), (3, 2, 0)])
    red = _squad("red", "r", [(5, 6, 0), (6, 6, 0)], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


def _no_grenades(builder):
    def wrapped():
        m, units, bi, ri = builder()
        for u in units:
            u.grenades = 0
        return m, units, bi, ri
    return wrapped


def assault_storm():
    m, units, _, red = assault_direct()
    return m, units, Intent("assault", goal=(4, 8, 0)), red


def shotgun_room():
    """室內近距：兩邊各 3 人、牆與門，霰彈槍 vs 步槍。"""
    m = TacMap()
    for x in range(10):
        if x not in (4, 5):
            m.wall(x, 5)
    for x in (1, 8):
        m.low_wall(x, 3, "N")
        m.low_wall(x, 7, "S")
    blue = _squad("blue", "b", [(3, 3, 0), (4, 3, 0), (5, 3, 0)], ["shotgun"] * 3)
    red = _squad("red", "r", [(3, 7, 0), (4, 7, 0), (5, 7, 0)], ["rifle"] * 3, facing=(0, -1))
    return m, blue + red, Intent("assault", goal=(4, 7, 0)), Intent("hold")


def rout_test():
    """紅方 2 人在開闊地被 4 人壓著打，觀察士氣潰退與撤出。"""
    m = TacMap()
    blue = _squad("blue", "b", [(2, 1, 0), (4, 1, 0), (6, 1, 0), (8, 1, 0)], ["lmg", "rifle", "rifle", "lmg"])
    red = _squad("red", "r", [(4, 8, 0), (5, 8, 0)], facing=(0, -1))
    return m, blue + red, Intent("supp_advance", goal=(4, 8, 0)), Intent("hold")


SCENARIOS = {
    "open_field": open_field,
    "cover_duel": cover_duel,
    "assault_direct": assault_direct,
    "assault_suppress": assault_suppress,
    "assault_flank": assault_flank,
    "ambush": ambush,
    "high_ground": high_ground,
    "low_ground": low_ground,
    "assault_storm": assault_storm,
    "shotgun_room": shotgun_room,
    "rout_test": rout_test,
}
SCENARIOS["assault_direct_nogren"] = _no_grenades(assault_direct)
SCENARIOS["assault_storm_nogren"] = _no_grenades(assault_storm)
