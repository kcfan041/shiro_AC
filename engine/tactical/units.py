from __future__ import annotations

from dataclasses import dataclass, field

from engine.tactical import body
from engine.tactical.tmap import Node, TacMap

WEAPONS = {
    "rifle": {"sigma": 0.020, "range": 40.0, "burst": 3, "supp_rounds": 5, "supp_k": 1.0, "ammo": 210,
              "caliber": "5.56", "move_mult": 1.6},
    "lmg": {"sigma": 0.026, "range": 50.0, "burst": 4, "supp_rounds": 8, "supp_k": 1.3, "ammo": 400,
            "caliber": "5.56", "move_mult": 2.0},
    "dmr": {"sigma": 0.011, "range": 60.0, "burst": 0, "supp_rounds": 3, "supp_k": 0.7, "ammo": 60,
            "caliber": "7.62", "move_mult": 1.6},
}

SUPPRESSED = 50
PINNED = 80


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
    stats: dict = field(default_factory=lambda: {"shots": 0, "hits": 0, "kills": 0})

    def __post_init__(self):
        if self.ammo < 0:
            self.ammo = WEAPONS[self.weapon]["ammo"]

    @property
    def alive(self) -> bool:
        return not self.down

    @property
    def pinned(self) -> bool:
        return self.suppression >= PINNED

    @property
    def suppressed(self) -> bool:
        return self.suppression >= SUPPRESSED

    def can_shoot(self) -> bool:
        return self.alive and not (self.damage.get("larm") and self.damage.get("rarm")) and self.ammo > 0

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
        return cls(**d)
