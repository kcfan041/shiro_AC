from __future__ import annotations

from dataclasses import dataclass, field

from engine.tactical import body
from engine.tactical.tmap import Node, TacMap

WEAPONS = {
    "rifle": {"sigma": 0.020, "range": 40.0, "burst": 3, "supp_rounds": 5, "supp_k": 1.0, "ammo": 210,
              "caliber": "5.56", "move_mult": 1.6, "melee": 1.0},
    "lmg": {"sigma": 0.026, "range": 50.0, "burst": 4, "supp_rounds": 8, "supp_k": 1.3, "ammo": 400,
            "caliber": "5.56", "move_mult": 2.0, "melee": 0.8},
    "dmr": {"sigma": 0.011, "range": 60.0, "burst": 0, "supp_rounds": 3, "supp_k": 0.7, "ammo": 60,
            "caliber": "7.62", "move_mult": 1.6, "melee": 0.7},
    "shotgun": {"sigma": 0.045, "range": 15.0, "burst": 2, "supp_rounds": 3, "supp_k": 1.0, "ammo": 40,
                "caliber": "12g", "move_mult": 1.4, "melee": 1.3, "pellets": 8},
}

SUPPRESSED = 50
PINNED = 80
DEFAULT_PROF = 100.0
GRENADES = 2


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
        return self.suppression >= PINNED

    @property
    def suppressed(self) -> bool:
        return self.suppression >= SUPPRESSED

    def proficiency(self, weapon: str | None = None) -> float:
        return float(self.prof.get(weapon or self.weapon, DEFAULT_PROF))

    def can_shoot(self) -> bool:
        return self.alive and not (self.damage.get("larm") and self.damage.get("rarm")) and self.ammo > 0

    def can_throw(self) -> bool:
        return self.alive and self.grenades > 0 and not (self.damage.get("larm") and self.damage.get("rarm"))

    def base(self, tmap: TacMap):
        return tmap.hug_base(self.pos, self.facing)

    def eye(self, tmap: TacMap):
        return body.eye(self.base(tmap), self.posture)

    def parts(self, tmap: TacMap):
        return body.parts(self.base(tmap), self.posture, self.facing)

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
        return cls(**d)
