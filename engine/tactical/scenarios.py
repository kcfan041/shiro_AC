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


def _no_smoke(builder):
    def wrapped():
        m, units, bi, ri = builder()
        for u in units:
            u.smokes = 0
        return m, units, bi, ri
    return wrapped


def retreat_open():
    """藍方 3 人在開闊地中段被紅方 4 人（窗口）發現，下令撤退往南邊緣。"""
    m = TacMap()
    for x in range(1, 9):
        if x in (2, 3, 6, 7):
            m.window_side(x, 9, "S", sill=0.85)
        else:
            m.wall_side(x, 9, "S")
    blue = _squad("blue", "b", [(3, 5, 0), (5, 5, 0), (7, 5, 0)])
    red = _squad("red", "r", [(2, 9, 0), (3, 9, 0), (6, 9, 0), (7, 9, 0)], facing=(0, -1))
    return m, blue + red, Intent("retreat", toward=(0, -1)), Intent("hold")


def retreat_cover():
    """藍方 3 人在矮牆後，撤退路線（往南）有幾段開闊地。"""
    m, units, bi, ri = retreat_open()
    for x in (3, 5, 7):
        m.low_wall(x, 5, "N")
    m.rubble(4, 3)
    m.rubble(6, 2)
    return m, units, bi, ri


def rescue():
    """藍方 3 人 + 1 名倒地隊友在開闊地，紅方 2 人在 12 m 外矮牆後；藍方撤退並拖回倒地者。"""
    m = TacMap()
    for x in (3, 6):
        m.low_wall(x, 8, "S")
    blue = _squad("blue", "b", [(3, 3, 0), (5, 3, 0), (6, 3, 0), (4, 4, 0)])
    blue[3].down = True
    blue[3].down_tick = 0
    red = _squad("red", "r", [(3, 8, 0), (6, 8, 0)], facing=(0, -1))
    return m, blue + red, Intent("retreat", toward=(0, -1)), Intent("hold")


def captain_retreat():
    """壓制推進直衝窗口；藍方用簡易「隊長」：出現重大事件時改下撤退。"""
    m, units, bi, ri = assault_suppress()
    return m, units, bi, ri


def _block(m: TacMap, x0: int, y0: int, x1: int, y1: int, windows: dict | None = None, door: tuple | None = None):
    """在 (x0,y0)-(x1,y1) 圍出一棟單層建築的外牆；windows: {(x,y): side}；door: (x,y) 留空。"""
    windows = windows or {}
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if x not in (x0, x1) and y not in (y0, y1):
                continue
            if door and (x, y) == door:
                continue
            if (x, y) in windows:
                m.window_wall(x, y)
                continue
            m.wall(x, y)


def district():
    """25×25（50 m）街區：中央兩棟建築與瓦礫、植被；雙方從南北兩端進場，開局互相看不見。"""
    m = TacMap(w=25, h=25)
    _block(m, 4, 9, 10, 14, windows={(6, 9): "S", (8, 14): "N", (10, 11): "E"}, door=(7, 9))
    _block(m, 14, 10, 20, 15, windows={(16, 10): "S", (18, 15): "N", (14, 12): "W"}, door=(17, 15))
    _block(m, 10, 16, 13, 18, windows={(11, 16): "S", (12, 18): "N"}, door=(13, 17))
    _block(m, 11, 6, 14, 8, windows={(12, 8): "N", (13, 6): "S"}, door=(11, 7))
    _block(m, 0, 10, 2, 13, windows={(2, 11): "E"}, door=(1, 13))
    _block(m, 22, 11, 24, 14, windows={(22, 12): "W"}, door=(23, 11))
    for x, y in ((2, 5), (12, 6), (22, 6), (3, 19), (12, 18), (21, 19), (12, 12)):
        m.rubble(x, y)
    for y in range(4, 8):
        m.veg(0, y)
        m.veg(24, y + 12)
    for x in range(11, 14):
        m.low_wall(x, 4, "N")
        m.low_wall(x, 20, "S")
    blue = _squad("blue", "b", [(10, 1, 0), (12, 1, 0), (14, 1, 0), (16, 1, 0)], ["lmg", "rifle", "rifle", "dmr"])
    red = _squad("red", "r", [(9, 23, 0), (12, 23, 0), (15, 23, 0), (18, 23, 0)], ["lmg", "rifle", "rifle", "dmr"],
                 facing=(0, -1))
    return m, blue + red, Intent("advance", goal=(12, 22, 0)), Intent("advance", goal=(12, 2, 0))


def district_hold():
    """25×25：紅方守在北側建築內，藍方從南端推進。"""
    m, units, _, _ = district()
    reds = [u for u in units if u.side == "red"]
    for u, pos in zip(reds, [(16, 10, 0), (14, 12, 0), (15, 11, 0), (19, 11, 0)]):
        u.pos = pos
    return m, units, Intent("supp_advance", goal=(16, 9, 0)), Intent("hold")


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
SCENARIOS["retreat_open"] = retreat_open
SCENARIOS["retreat_open_nosmoke"] = _no_smoke(retreat_open)
SCENARIOS["retreat_cover"] = retreat_cover
SCENARIOS["retreat_cover_nosmoke"] = _no_smoke(retreat_cover)
SCENARIOS["rescue"] = rescue
SCENARIOS["rescue_nosmoke"] = _no_smoke(rescue)
SCENARIOS["rout_test_nosmoke"] = _no_smoke(rout_test)
SCENARIOS["assault_storm_nosmoke"] = _no_smoke(assault_storm)
SCENARIOS["shotgun_room_nosmoke"] = _no_smoke(shotgun_room)

CAPTAIN = {"captain_retreat": captain_retreat}
LARGE = {"district": district, "district_hold": district_hold}
