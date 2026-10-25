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


def _block(m: TacMap, x0: int, y0: int, x1: int, y1: int, windows: dict | None = None, door=None):
    """在 (x0,y0)-(x1,y1) 圍出一棟單層建築的外牆；windows: {(x,y): side}；door: (x,y) 或 [(x,y), ...] 留空。"""
    windows = windows or {}
    doors = [] if not door else ([door] if isinstance(door[0], int) else list(door))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if x not in (x0, x1) and y not in (y0, y1):
                continue
            if (x, y) in doors:
                continue
            if (x, y) in windows:
                m.window_wall(x, y)
                continue
            m.wall(x, y)
    m.add_room(x0, y0, x1, y1, doors)


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


def house_clear(prep=None, tempo=None, entry=None):
    """4 打 2：紅方 2 人守在一棟兩門平房內（南門、東門），藍方從南邊 12 m 外破門。"""
    m = TacMap(w=16, h=18)
    _block(m, 4, 8, 11, 14, windows={(6, 8): "S", (11, 12): "E"}, door=[(8, 8), (11, 10)])
    m.low_wall(7, 3, "N")
    m.rubble(10, 4)
    blue = _squad("blue", "b", [(6, 1, 0), (7, 1, 0), (8, 1, 0), (9, 1, 0)], ["rifle", "rifle", "shotgun", "lmg"])
    red = _squad("red", "r", [(6, 12, 0), (9, 11, 0)], ["rifle", "rifle"], facing=(0, -1))
    from engine.tactical.units import loadout
    for u in blue + red:
        for k, v in loadout(u.weapon).items():
            setattr(u, k, v)
    return m, blue + red, Intent("breach", prep=prep, tempo=tempo, entry=entry), Intent("hold")


def shotgun_room_breach():
    """室內近距：用破門（閃光）取代直衝。"""
    m, units, _, red = shotgun_room()
    m.add_room(-1, 5, 10, 11, [(4, 5), (5, 5)])
    for u in units:
        if u.side == "blue":
            u.flashes = 1
    return m, units, Intent("breach", prep="flash"), red


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

BREACH = {
    "house_assault": lambda: (lambda m, u, _, r: (m, u, Intent("assault", goal=(7, 11, 0)), r))(*house_clear()),
    "house_supp": lambda: (lambda m, u, _, r: (m, u, Intent("supp_advance", goal=(7, 11, 0)), r))(*house_clear()),
    "house_breach_flash_stack": lambda: house_clear("flash", "stack"),
    "house_breach_flash_quick": lambda: house_clear("flash", "quick"),
    "house_breach_frag_stack": lambda: house_clear("frag", "stack"),
    "house_breach_none_quick": lambda: house_clear("none", "quick"),
    "house_breach_flash_multi": lambda: house_clear("flash", "stack", "multi"),
    "house_breach_auto": lambda: house_clear(),
    "shotgun_room": shotgun_room,
    "shotgun_room_breach": shotgun_room_breach,
}

CAPTAIN = {"captain_retreat": captain_retreat}
LARGE = {"district": district, "district_hold": district_hold}


# ---------------------------------------------------------------------- 地形（docs/new/terrain.md §11）
# 每個地形情境都有全平的對照組（同樣的人、同樣的位置，只差在地形），用 scripts/terrain_batch.py 比較。
# 地圖 20×28（40×56 m），藍方從南邊來、紅方在北邊。


def _ridge(m: TacMap, crest: int = 14, height_u: int = 8, back_u: int = 0):
    """東西向的稜線：從 crest−8 起往北每格升 0.2 m，到 crest、crest+1 是頂（height_u×0.2 m），之後往北每格降 0.2 m 到 back_u×0.2 m。"""
    for x in range(m.w):
        for y in range(m.h):
            if y < crest - height_u:
                u = 0
            elif y <= crest + 1:
                u = min(height_u, y - (crest - height_u))
            else:
                u = max(back_u, height_u - (y - crest - 1))
            m.set_ground(x, y, u)


def reverse_slope(flat: bool = False, forward: bool = False):
    """反斜面：1.6 m 稜線（頂在 y＝14～15），紅方 3 人守在稜線後（y＝21，地面 0.4 m，連站著的藍方都要爬上稜線才看得到）；
    forward＝守在前坡（y＝11，藍方一路都看得到）；flat＝沒有稜線。藍方 4 人從 y＝2 推進。"""
    m = TacMap(w=20, h=28)
    if not flat:
        _ridge(m)
    ry = 11 if forward else 21
    blue = _squad("blue", "b", [(7, 2, 0), (9, 2, 0), (11, 2, 0), (13, 2, 0)], ["lmg", "rifle", "rifle", "dmr"])
    red = _squad("red", "r", [(7, ry, 0), (10, ry, 0), (13, ry, 0)], facing=(0, -1))
    return m, blue + red, Intent("supp_advance", goal=(10, ry, 0)), Intent("hold")


def foxhole_defense(flat: bool = False, dug: str | None = None):
    """散兵坑防守：紅方 3 人各在一個散兵坑（−1.0 m、南側 0.4 m 胸牆），藍方 4 人壓制推進；flat＝同位置、平地。
    dug：藍方知道對方挖了陣地（Intent.dug_in，敵方戰術指揮的判斷）——"supp" 照樣壓制推進、"flank" 改包抄。"""
    m = TacMap(w=20, h=28)
    holes = [(6, 18), (10, 18), (14, 18)]
    if not flat:
        for x, y in holes:
            m.set_ground(x, y, -5)
            m.set_berm(x, y, "S", 2)
    blue = _squad("blue", "b", [(7, 2, 0), (9, 2, 0), (11, 2, 0), (13, 2, 0)], ["lmg", "rifle", "rifle", "dmr"])
    red = _squad("red", "r", [(x, y, 0) for x, y in holes], facing=(0, -1))
    if dug == "flank":
        return m, blue + red, Intent("flank", goal=(10, 18, 0), side="left", dug_in=True), Intent("hold")
    return m, blue + red, Intent("supp_advance", goal=(10, 18, 0), dug_in=dug == "supp"), Intent("hold")


def high_vs_low(flat: bool = False, cover: bool = False):
    """高處對低處：紅方 2 人在 2 m 高台上（y≥15，y＝11～15 是每格 0.4 m 的坡、沒有矮牆），藍方 2 人在低處的 1 m 矮牆後，對射約 24 m。
    flat＝同位置、平地（紅方在開闊地）。cover＝紅方也躲在高台邊的 1 m 矮牆後：蹲著往下看會被自己躲的牆擋住，
    要站起來才打得到（AI 的「站起來越過掩體射擊」）；雙方躲著互相看不到，所以藍方改成往高台壓制推進。"""
    m = TacMap(w=20, h=28)
    if not flat:
        for x in range(m.w):
            for y in range(11, m.h):
                m.set_ground(x, y, min(10, (y - 10) * 2))
    for x in (8, 11):
        m.low_wall(x, 5, "N")
        if cover:
            m.low_wall(x, 17, "S")
    blue = _squad("blue", "b", [(8, 5, 0), (11, 5, 0)])
    red = _squad("red", "r", [(8, 17, 0), (11, 17, 0)], facing=(0, -1))
    if cover:                                     # 雙方都躲著互相看不到：藍方往高台推進
        return m, blue + red, Intent("supp_advance", goal=(10, 17, 0)), Intent("hold")
    return m, blue + red, Intent("hold"), Intent("hold")


def grenade_foxhole(flat: bool = False):
    """坑內躲手榴彈：藍方 2 人在散兵坑（−1.0 m、北側 0.4 m 胸牆），紅方 3 人帶手榴彈（每人 3 顆）從 10 m 外突擊；
    flat＝藍方在平地的 1 m 矮牆後（掩護高度相近）。比較手榴彈造成的傷亡。"""
    m = TacMap(w=20, h=28)
    holes = [(8, 8), (11, 8)]
    for x, y in holes:
        if flat:
            m.low_wall(x, y, "N")
        else:
            m.set_ground(x, y, -5)
            m.set_berm(x, y, "N", 2)
    blue = _squad("blue", "b", [(x, y, 0) for x, y in holes])
    red = _squad("red", "r", [(7, 13, 0), (10, 13, 0), (13, 13, 0)], facing=(0, -1))
    for u in red:
        u.grenades = 3
    return m, blue + red, Intent("hold"), Intent("assault", goal=(10, 8, 0))


TERRAIN = {
    "reverse_slope": reverse_slope,
    "reverse_slope_forward": lambda: reverse_slope(forward=True),
    "reverse_slope_flat": lambda: reverse_slope(flat=True),
    "foxhole_defense": foxhole_defense,
    "foxhole_defense_flat": lambda: foxhole_defense(flat=True),
    "foxhole_defense_dug_supp": lambda: foxhole_defense(dug="supp"),
    "foxhole_defense_dug_flank": lambda: foxhole_defense(dug="flank"),
    "high_vs_low": high_vs_low,
    "high_vs_low_flat": lambda: high_vs_low(flat=True),
    "high_vs_low_cover": lambda: high_vs_low(cover=True),
    "high_vs_low_cover_flat": lambda: high_vs_low(flat=True, cover=True),
    "grenade_foxhole": grenade_foxhole,
    "grenade_foxhole_flat": lambda: grenade_foxhole(flat=True),
}


# ---------------------------------------------------------------------- 原本固定遭遇缺的行為（TODO P1）


def aid_drag():
    """急救／拖人：藍方 3 人在矮牆後、1 人倒在牆前開闊地（4 m 外），紅方 2 人在 40 m 外；藍方固守，應該有人去止血並拖回掩護。"""
    m = TacMap(w=20, h=28)
    for x in (7, 9, 11, 13):
        m.low_wall(x, 4, "N")
    blue = _squad("blue", "b", [(7, 4, 0), (9, 4, 0), (11, 4, 0), (10, 6, 0)])
    blue[3].down = True
    blue[3].down_tick = 0
    red = _squad("red", "r", [(8, 24, 0), (12, 24, 0)], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


def overwatch_crossing():
    """警戒射擊：藍方 2 人蹲在開闊地固守，紅方 3 人從 30 m 外一道牆後走出來、穿越開闊地往東走；
    藍方等待中看到移動的人就警戒射擊（一開始就看得到的話會直接規劃一般射擊，所以紅方要從遮蔽物後出現）。
    藍方不放矮牆：蹲在 1 m 矮牆後眼高剛好和牆頂一樣，只看得到比眼睛高的部分，蹲著走的人可見程度不到警戒射擊的門檻。"""
    m = TacMap(w=28, h=28)
    for x in range(0, 7):                         # L 形牆把紅方起點圍住：要繞過北邊的牆角才出現
        m.wall(x, 17)
    for y in range(17, 25):
        m.wall(7, y)
    blue = _squad("blue", "b", [(12, 4, 0), (15, 4, 0)])
    red = _squad("red", "r", [(2, 19, 0), (3, 20, 0), (2, 21, 0)], facing=(1, 0))
    return m, blue + red, Intent("hold"), Intent("advance", goal=(26, 20, 0))


def unseen_shooter():
    """看不到的射手：紅方 1 名射手步槍蹲在 50 m 外一排房子的窗戶後（只露出頭，遠距離很難發現），藍方 3 人在開闊地固守；
    藍方應該被打、聽到槍聲（只得到大概位置），但一開始看不到他。
    （射手趴在草叢裡時，草叢遮不完全會被看到，而且 AI 會在「暴露→換掩體→看不到→換位置」之間來回、一直不開槍，所以用窗戶。）"""
    m = TacMap(w=20, h=40)
    for x in range(6, 15):
        if x == 10:
            m.window_side(x, 29, "S")
        else:
            m.wall_side(x, 29, "S")
    blue = _squad("blue", "b", [(8, 4, 0), (10, 4, 0), (12, 4, 0)])
    red = _squad("red", "r", [(10, 29, 0)], ["dmr"], facing=(0, -1))
    return m, blue + red, Intent("hold"), Intent("hold")


BEHAVIORS = {"aid_drag": aid_drag, "overwatch_crossing": overwatch_crossing, "unseen_shooter": unseen_shooter}
