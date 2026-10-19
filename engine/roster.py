"""個體名冊：作戰層的小隊成員與敵兵都是持久個體，並在所在格的戰術地圖上有位置。

- 敵軍 `strength` 仍保留給作戰層規則使用，但永遠與 `soldiers` 人數同步（以 soldiers 為準）。
- 單位換格後，於下一次同步時依進場邊重新放到新格的戰術地圖上。
"""
from __future__ import annotations

from engine.models import Enemy, Member, Squad, World, new_soldier, refill_loadout
from engine.tactical import worldmap

BALANCED = ("lmg", "dmr")


def balanced_weapons(n: int) -> list[str]:
    """均衡班：機槍 ≈ 1/4、射手 ≈ 1/4（3 人以上各至少 1），其餘步槍。"""
    if n <= 2:
        return ["rifle"] * n
    lmg = max(1, round(n / 4))
    dmr = max(1, round(n / 4)) if n >= 4 else 0
    out = ["lmg"] * lmg + ["dmr"] * dmr
    return (out + ["rifle"] * n)[:n]


def assign_weapons(squad: Squad, fixed: set[str] | None = None):
    """依均衡班模板分配武器；fixed＝角色卡已指定武器的成員名稱，不更動。"""
    fixed = fixed or set()
    want = balanced_weapons(len(squad.members))
    have = [m.weapon for m in squad.members if m.name in fixed]
    for w in have:
        if w in want:
            want.remove(w)
    free = [m for m in squad.members if m.name not in fixed]
    for m, w in zip(free, want):
        m.weapon = w
    for m in squad.members:
        refill_loadout(m)


def missing_weapon(squad: Squad) -> str:
    alive = [m.weapon for m in squad.alive_members()]
    want = balanced_weapons(len(alive) + 1)
    for w in alive:
        if w in want:
            want.remove(w)
    return want[0] if want else "rifle"


def new_enemy_soldiers(world: World, enemy: Enemy, n: int) -> list[dict]:
    out = []
    weapons = balanced_weapons(n + len(enemy.soldiers))[len(enemy.soldiers):]
    for i in range(n):
        out.append(new_soldier(world.next_id(f"{enemy.id}s"), weapons[i] if i < len(weapons) else "rifle"))
    return out


def transfer(src: Enemy, dst: Enemy, n: int):
    """把 src 的 n 名敵兵移交給 dst（分兵／合併時保留個體身分與傷勢）。"""
    moved = src.soldiers[-n:] if n > 0 else []
    src.soldiers = src.soldiers[:len(src.soldiers) - len(moved)]
    for sd in moved:
        sd["tpos"] = None
    dst.soldiers.extend(moved)


def sync_enemy(world: World, e: Enemy):
    if not e.alive:
        e.soldiers = []
        return
    n = max(0, e.strength)
    fighting = [sd for sd in e.soldiers if not sd.get("down")]
    if len(fighting) < n:
        e.soldiers.extend(new_enemy_soldiers(world, e, n - len(fighting)))
    elif len(fighting) > n:
        drop = {id(sd) for sd in fighting[n:]}
        e.soldiers = [sd for sd in e.soldiers if id(sd) not in drop]
    moved = e.tcell != e.pos
    unplaced = [sd for sd in e.soldiers if not sd.get("tpos")]
    if moved or unplaced:
        if moved:
            frm = e.tcell
            for sd in e.soldiers:
                sd["tpos"] = None
            worldmap.place_enemy(world, e, frm=frm, defend=(e.tag == "garrison" and frm is None))
        else:
            worldmap.place(world, e.pos, unplaced, None, facing=[0, -1])
        e.tcell = e.pos


def sync_squad(world: World, s: Squad):
    if s.status != "active":
        return
    alive = [m for m in s.members if not m.lost]
    moved = s.tcell != s.pos
    unplaced = [m for m in alive if not m.tpos]
    if moved:
        frm = s.tcell
        for m in alive:
            m.tpos = None
        worldmap.place(world, s.pos, alive, frm)
        s.tcell = s.pos
    elif unplaced:
        worldmap.place(world, s.pos, unplaced, None)
    for m in s.members:
        if m.lost:
            m.tpos = None


def sync_remnant(world: World, r):
    if r.status != "holding":
        return
    unplaced = [m for m in r.members if not m.lost and not m.tpos]
    if r.tcell != r.pos or unplaced:
        worldmap.place_defenders(world, r.pos, unplaced or [m for m in r.members if not m.lost])
        r.tcell = r.pos


def sync_world(world: World):
    for e in sorted(world.enemies.values(), key=lambda x: x.id):
        sync_enemy(world, e)
    for r in sorted(getattr(world, "remnants", {}).values(), key=lambda x: x.id):
        sync_remnant(world, r)
    for s in sorted(world.squads.values(), key=lambda x: x.id):
        sync_squad(world, s)


def soldiers_in(world: World, cell: str) -> dict:
    """GM／測試：某格內的所有個體。"""
    return {
        "squads": {s.id: [m.tpos for m in s.alive_members()] for s in world.squads.values()
                   if s.pos == cell and s.status == "active"},
        "enemies": {e.id: [sd.get("tpos") for sd in e.soldiers] for e in world.enemies.values()
                    if e.pos == cell and e.alive},
    }
