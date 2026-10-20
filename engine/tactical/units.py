from __future__ import annotations

from dataclasses import dataclass, field

from engine.tactical import body
from engine.tactical.tmap import Node, TacMap

WEAPONS = {
    "rifle": {"sigma": 0.020, "range": 40.0, "burst": 3, "supp_rounds": 5, "supp_k": 1.0, "ammo": 210,
              "caliber": "5.56", "move_mult": 1.6, "melee": 1.0},
    "lmg": {"sigma": 0.026, "range": 50.0, "burst": 4, "supp_rounds": 8, "supp_k": 1.3, "ammo": 400,
            "caliber": "5.56", "move_mult": 2.0, "melee": 0.8},
    "dmr": {"sigma": 0.011, "range": 60.0, "burst": 0, "supp_rounds": 4, "supp_k": 0.7, "ammo": 60,
            "caliber": "7.62", "move_mult": 1.6, "melee": 0.7, "supp_far": 1.5},
    "shotgun": {"sigma": 0.045, "range": 15.0, "burst": 2, "supp_rounds": 3, "supp_k": 1.0, "ammo": 40,
                "caliber": "12g", "move_mult": 1.4, "melee": 1.3, "pellets": 8},
}

SUPPRESSED = 50
PINNED = 80
DEFAULT_PROF = 100.0
GRENADES = 2
SMOKES = 1
THROW_CAP = 3                 # 每人投擲物（破片＋煙霧＋閃光）合計上限
LOADOUT = {                   # 整補時依武器補到這個配置：(破片, 煙霧, 閃光)
    "rifle": (1, 1, 1),
    "lmg": (2, 1, 0),
    "dmr": (2, 1, 0),
    "shotgun": (1, 0, 2),
}
ITEMS = ("grenades", "smokes", "flashes")
ITEM_LABELS = {"grenades": "破片", "smokes": "煙霧", "flashes": "閃光"}


def loadout(weapon: str) -> dict:
    g, s, f = LOADOUT.get(weapon, LOADOUT["rifle"])
    return {"grenades": g, "smokes": s, "flashes": f}


def _lerp3(p: float, a: float, b: float, c: float) -> float:
    p = max(0.0, min(200.0, p))
    if p <= 100:
        return a + (b - a) * p / 100
    return b + (c - b) * (p - 100) / 100


def prof_sigma(p: float) -> float:
    return _lerp3(p, 1.25, 1.0, 0.85)


def prof_supp(p: float) -> float:
    return _lerp3(p, 0.8, 1.0, 1.15)


def prof_ammo(p: float) -> float:
    return _lerp3(p, 1.2, 1.0, 0.9)


def prof_melee(p: float) -> float:
    return _lerp3(p, 0.8, 1.0, 1.2)


@dataclass
class Unit:
    id: str
    side: str
    pos: Node
    weapon: str = "rifle"
    posture: str = "crouch"
    facing: tuple[int, int] = (0, 1)
    ammo: int = -1
    damage: dict = field(default_factory=dict)
    torso_hits: int = 0
    suppression: float = 0.0
    down: bool = False
    moved: bool = False
    fired: bool = False
    group: str = "a"
    watch: tuple[float, float] | None = None
    leader: bool = False
    grenades: int = GRENADES
    smokes: int = SMOKES
    flashes: int = 0
    braced: tuple[float, float] | None = None   # 架槍方向（單位向量）；None＝未架槍
    brace_rest: bool = False                    # 架槍時有依託（矮牆、窗台、瓦礫或臥姿）
    bracing: tuple[float, float] | None = None  # 架槍中（第一步）：下一步才生效
    dazzled: int = 0                            # 光學過載剩餘 tick
    traits: dict = field(default_factory=dict)  # 性格（engine/persona.py）
    persona: float = 0.0                        # 人格值：性格影響戰術的程度
    prof: dict = field(default_factory=dict)
    dead: bool = False
    down_tick: int | None = None
    stabilized: bool = False
    exited: bool = False
    surrendered: bool = False
    stats: dict = field(default_factory=lambda: {"shots": 0, "hits": 0, "kills": 0})

    def __post_init__(self):
        if self.ammo < 0:
            self.ammo = WEAPONS[self.weapon]["ammo"]

    @property
    def alive(self) -> bool:
        """仍在戰鬥中（未倒地、未離場、未投降）。"""
        return not (self.down or self.exited or self.surrendered)

    @property
    def pinned(self) -> bool:
        """沉著（×人格值）讓門檻上下移動最多 ±15。"""
        return self.suppression >= PINNED + 15 * self.trait("comp")

    @property
    def suppressed(self) -> bool:
        return self.suppression >= SUPPRESSED + 15 * self.trait("comp")

    def trait(self, key: str) -> float:
        """實際影響＝性格數值 × 人格值（-1～1）。"""
        return float(self.traits.get(key, 0.0)) * float(self.persona or 0.0)

    def proficiency(self, weapon: str | None = None) -> float:
        return float(self.prof.get(weapon or self.weapon, DEFAULT_PROF))

    def can_shoot(self) -> bool:
        return self.alive and not (self.damage.get("larm") and self.damage.get("rarm")) and self.ammo > 0

    def can_throw(self, item: str = "grenades") -> bool:
        return self.alive and getattr(self, item) > 0 and not (self.damage.get("larm") and self.damage.get("rarm"))

    def throwables(self) -> int:
        return self.grenades + self.smokes + self.flashes

    def room(self) -> int:
        return max(0, THROW_CAP - self.throwables())

    def base(self, tmap: TacMap):
        return tmap.hug_base(self.pos, self.facing)

    def eye(self, tmap: TacMap):
        return body.eye(self.base(tmap), self.posture)

    def parts(self, tmap: TacMap):
        return body.parts_cached(self.base(tmap), self.posture, self.facing)

    def samples(self, tmap: TacMap):
        return body.sample_points(self.base(tmap), self.posture, self.facing)

    def torso(self, tmap: TacMap):
        return body.torso_center(self.base(tmap), self.posture, self.facing)

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Unit":
        d = dict(d)
        d["pos"] = tuple(d["pos"])
        d["facing"] = tuple(d["facing"])
        if d.get("watch") is not None:
            d["watch"] = tuple(d["watch"])
        if d.get("braced") is not None:
            d["braced"] = tuple(d["braced"])
        if d.get("bracing") is not None:
            d["bracing"] = tuple(d["bracing"])
        return cls(**d)
