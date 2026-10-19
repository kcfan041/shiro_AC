from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


def _known(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def default_tendencies() -> dict:
    return {"consult": "normal", "verify": False, "initiative": "normal", "execution": "literal"}


@dataclass
class Member:
    name: str
    hp: int = 100
    xp: int = 0
    trait: str = ""
    joined: int = 0
    weapon: str = "rifle"
    tpos: list | None = None
    posture: str = "crouch"
    facing: list = field(default_factory=lambda: [0, 1])
    damage: dict = field(default_factory=dict)
    ammo: int | None = None
    grenades: int = 1
    smokes: int = 1
    flashes: int = 1
    prof: dict = field(default_factory=dict)
    torso_hits: int = 0
    suppression: float = 0.0
    down: bool = False
    down_tick: int | None = None
    stabilized: bool = False
    # 人格（engine/persona.py）
    traits: dict = field(default_factory=lambda: {"aggr": 0.0, "caut": 0.0, "comp": 0.0, "bond": 0.0})
    innate: dict = field(default_factory=dict)
    persona: float = 0.1
    personality: str = ""
    speech: str = ""
    background: str = ""
    sample_lines: list = field(default_factory=list)
    voice_level: float = 0.0          # 個性文字是在哪個人格值寫的（跨 0.4／0.7 重寫）
    led_before: bool = False          # 曾擔任隊長（接手優先）
    beliefs: list = field(default_factory=list)
    tendencies: dict = field(default_factory=dict)
    battles: int = 0

    @property
    def alive(self) -> bool:
        """仍可作戰（未陣亡、未倒地）。"""
        return self.hp > 0 and not self.down

    @property
    def lost(self) -> bool:
        return self.hp <= 0


@dataclass
class Action:
    type: str = "hold"
    target: str | None = None
    progress: int = 0
    duration: int = 0
    ordered: bool = False
    path: list[str] = field(default_factory=list)
    direct: bool = False
    flank: str | None = None
    breach: dict | None = None    # 破門參數：{"room": id|None, "prep": ..., "entry": ..., "tempo": ...}


@dataclass
class Squad:
    id: str
    callsign: str
    leader: dict
    members: list[Member]
    pos: str
    morale: int = 80
    ammo: int = 100
    linked: bool = True
    status: str = "active"
    action: Action = field(default_factory=Action)
    memory: list[str] = field(default_factory=list)
    known_enemies: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    offline_events: list[str] = field(default_factory=list)
    unlinked_since: int | None = None
    idle_ticks: int = 0
    contact: str | None = None
    last_order: str | None = None
    kills: int = 0
    commander: str | None = None
    experiences: list[dict] = field(default_factory=list)
    dialogue: list[dict] = field(default_factory=list)
    last_combat_report: int = -99
    ammo_flags: list[str] = field(default_factory=list)
    max_size: int = 4
    rest_ticks: int = 0
    last_recruit: int = -99
    next_chatter: int = 0
    beliefs: list[dict] = field(default_factory=list)
    tendencies: dict = field(default_factory=default_tendencies)
    long_memory: str = ""
    reflect_reasons: list[str] = field(default_factory=list)
    last_reflect: int = -99
    pending_confirm: str | None = None
    tcell: str | None = None
    speaker: str | None = None          # 目前帶隊／發言的人（名字）；None＝第一位隊員
    commander_name: str | None = None   # 原隊長（整補後恢復指揮）

    def alive_members(self) -> list[Member]:
        return [m for m in self.members if m.alive]

    def member(self, name: str | None) -> Member | None:
        return next((m for m in self.members if m.name == name), None)

    def speaker_member(self) -> Member | None:
        m = self.member(self.speaker) if self.speaker else (self.members[0] if self.members else None)
        if m is not None and m.alive:
            return m
        alive = self.alive_members()
        return alive[0] if alive else m

    def acting_leader(self) -> str:
        m = self.speaker_member()
        return m.name if m is not None and m.alive else "—"

    @classmethod
    def from_dict(cls, d: dict) -> Squad:
        d = _known(cls, d)
        d["members"] = [Member(**_known(Member, m)) for m in d["members"]]
        d["action"] = Action(**_known(Action, d.get("action", {})))
        d["tendencies"] = {**default_tendencies(), **d.get("tendencies", {})}
        return cls(**d)


@dataclass
class Enemy:
    id: str
    pos: str
    strength: int
    route: list[str]
    route_idx: int = 0
    move_every: int = 6
    cooldown: int = 6
    engaged_with: str | None = None
    alive: bool = True
    tag: str | None = None
    home: str | None = None
    order: str | None = None
    goal: str | None = None
    path: list[str] = field(default_factory=list)
    step_cd: int = 0
    max_strength: int = 0
    soldiers: list[dict] = field(default_factory=list)
    tcell: str | None = None


@dataclass
class Remnant:
    """失聯友軍殘部（綠方）：被友方 AI 帶出、斷線後殘存的白紙兵，隨機一人當臨時隊長，固守直到被救／投降／被殲滅。"""
    id: str
    pos: str
    commander: str | None = None
    mission: str | None = None
    members: list[Member] = field(default_factory=list)
    leader: str | None = None          # 臨時隊長名字
    status: str = "holding"            # holding / rescued / surrendered / destroyed
    morale: int = 55
    tcell: str | None = None
    voiced: bool = False
    last_radio: int = -99
    radio_flags: list[str] = field(default_factory=list)
    last_line: str = ""

    def alive_members(self) -> list[Member]:
        return [m for m in self.members if m.alive]

    @classmethod
    def from_dict(cls, d: dict) -> "Remnant":
        d = _known(cls, d)
        d["members"] = [Member(**_known(Member, m)) for m in d.get("members", [])]
        return cls(**d)


def new_soldier(sid: str, weapon: str = "rifle") -> dict:
    """持久個體（敵我共用格式）：戰術層位置 tpos=[x,y,l]、傷勢、彈藥、投擲物、熟練度。"""
    from engine.tactical.units import loadout
    return {"id": sid, "weapon": weapon, "tpos": None, "posture": "crouch", "facing": [0, -1],
            "damage": {}, "torso_hits": 0, "ammo": None, **loadout(weapon), "prof": {},
            "traits": {}, "persona": 0.0}


def refill_loadout(m: "Member"):
    """整補：依目前武器補到預設投擲物配置（換武器後配置跟著換）。"""
    from engine.tactical.units import loadout
    for k, v in loadout(m.weapon).items():
        setattr(m, k, v)


def _default_intel() -> dict:
    return {"squads": {}, "enemies": {}, "log": []}


def _default_adjutant() -> dict:
    return {"summary": "", "suggestions": [], "tick": 0}


def _default_discord() -> dict:
    return {"guild_id": None, "lobby_channel": None, "lobby_message": None, "spectator_role": None, "squads": {}}


@dataclass
class Commander:
    id: str
    name: str
    category: int | None = None
    hq_channel: int | None = None
    panel_message: int | None = None
    board_message: int | None = None
    pending_notices: list[dict] = field(default_factory=list)
    viewers: list[int] = field(default_factory=list)
    intel: dict = field(default_factory=_default_intel)
    adjutant: dict = field(default_factory=_default_adjutant)
    main_idx: int = 0
    missions: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    next_side: int = 0
    supply_ready: int = 0
    reinforce_ready: int = 0
    events: list[str] = field(default_factory=list)

    def reset_knowledge(self):
        self.intel = _default_intel()
        self.adjutant = _default_adjutant()
        self.main_idx = 0
        self.missions = []
        self.history = []
        self.next_side = 0
        self.supply_ready = 0
        self.reinforce_ready = 0
        self.events = []


@dataclass
class World:
    scenario: str
    tick: int = 0
    running: bool = False
    squads: dict[str, Squad] = field(default_factory=dict)
    enemies: dict[str, Enemy] = field(default_factory=dict)
    commanders: dict[str, Commander] = field(default_factory=dict)
    discord: dict = field(default_factory=_default_discord)
    objects: dict[str, dict] = field(default_factory=dict)
    seq: int = 0
    tmaps: dict = field(default_factory=dict)   # cell -> {"seed": int, "template": str}
    enemy_intel: dict = field(default_factory=dict)
    enemy_next_think: int = 0
    enemy_next_harass: int = 0
    reserve: dict = field(default_factory=lambda: {"ally": []})   # 陣營 -> 預備兵（白紙個體 dict）
    reserve_announced: bool = False
    next_wanderer: int = 0
    remnants: dict = field(default_factory=dict)   # id -> Remnant（失聯友軍殘部，綠方）

    def next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"

    def to_dict(self) -> dict:
        return asdict(self)

    def squads_of(self, commander_id: str) -> list[Squad]:
        return [s for s in self.squads.values() if s.commander == commander_id]

    @classmethod
    def from_dict(cls, d: dict) -> World:
        d = _known(cls, d)
        d["squads"] = {k: Squad.from_dict(v) for k, v in d.get("squads", {}).items()}
        d["enemies"] = {k: Enemy(**_known(Enemy, v)) for k, v in d.get("enemies", {}).items()}
        d["remnants"] = {k: Remnant.from_dict(v) for k, v in d.get("remnants", {}).items()}
        d["commanders"] = {k: Commander(**_known(Commander, v)) for k, v in d.get("commanders", {}).items()}
        d["discord"] = {**_default_discord(), **d.get("discord", {})}
        return cls(**d)
