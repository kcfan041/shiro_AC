"""戰術地圖：方塊幾何、可站立節點、通行、視線與射線。"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.tactical.geometry import Box, Vec, length, ray_box, sub

CELL = 2.0
FLOOR_H = 3.0
BODY_LO = 0.7
BODY_HI = 1.7
KNEE = 0.5
CHEST = 1.3
VEG_FULL = 1.5

Node = tuple[int, int, int]


@dataclass
class TacMap:
    w: int = 10
    h: int = 10
    cell: float = CELL
    boxes: list[Box] = field(default_factory=list)
    stairs: set[tuple[int, int]] = field(default_factory=set)
    _cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ build

    def add(self, box: Box):
        self.boxes.append(box)
        self._cache.clear()

    def cell_lo(self, x: int, y: int) -> tuple[float, float]:
        return x * self.cell, y * self.cell

    def wall(self, x: int, y: int, height: float = 3.0, l: int = 0, tag: str = "wall"):
        x0, y0 = self.cell_lo(x, y)
        z0 = l * FLOOR_H
        self.add(Box((x0, y0, z0), (x0 + self.cell, y0 + self.cell, z0 + height), "solid", tag))

    def low_wall(self, x: int, y: int, side: str, height: float = 1.0, thick: float = 0.3, l: int = 0):
        x0, y0 = self.cell_lo(x, y)
        x1, y1 = x0 + self.cell, y0 + self.cell
        z0 = l * FLOOR_H
        if side == "N":
            lo, hi = (x0, y1 - thick, z0), (x1, y1, z0 + height)
        elif side == "S":
            lo, hi = (x0, y0, z0), (x1, y0 + thick, z0 + height)
        elif side == "E":
            lo, hi = (x1 - thick, y0, z0), (x1, y1, z0 + height)
        else:
            lo, hi = (x0, y0, z0), (x0 + thick, y1, z0 + height)
        self.add(Box(lo, hi, "solid", "lowwall"))

    def side_panel(self, x: int, y: int, side: str, z_lo: float, z_hi: float, thick: float = 0.3,
                   l: int = 0, tag: str = "wall"):
        x0, y0 = self.cell_lo(x, y)
        x1, y1 = x0 + self.cell, y0 + self.cell
        z0 = l * FLOOR_H
        if side == "N":
            lo, hi = (x0, y1 - thick, z0 + z_lo), (x1, y1, z0 + z_hi)
        elif side == "S":
            lo, hi = (x0, y0, z0 + z_lo), (x1, y0 + thick, z0 + z_hi)
        elif side == "E":
            lo, hi = (x1 - thick, y0, z0 + z_lo), (x1, y1, z0 + z_hi)
        else:
            lo, hi = (x0, y0, z0 + z_lo), (x0 + thick, y1, z0 + z_hi)
        self.add(Box(lo, hi, "solid", tag))

    def wall_side(self, x: int, y: int, side: str, l: int = 0):
        self.side_panel(x, y, side, 0.0, FLOOR_H, l=l, tag="wallside")

    def window_side(self, x: int, y: int, side: str, sill: float = 1.0, top: float = 1.8, l: int = 0):
        self.side_panel(x, y, side, 0.0, sill, l=l, tag="window")
        self.side_panel(x, y, side, top, FLOOR_H, l=l, tag="window")

    def window_wall(self, x: int, y: int, sill: float = 1.0, top: float = 1.8, l: int = 0):
        x0, y0 = self.cell_lo(x, y)
        z0 = l * FLOOR_H
        self.add(Box((x0, y0, z0), (x0 + self.cell, y0 + self.cell, z0 + sill), "solid", "window"))
        self.add(Box((x0, y0, z0 + top), (x0 + self.cell, y0 + self.cell, z0 + FLOOR_H), "solid", "window"))

    def rubble(self, x: int, y: int, height: float = 0.6):
        x0, y0 = self.cell_lo(x, y)
        self.add(Box((x0 + 0.1, y0 + 0.1, 0.0), (x0 + self.cell - 0.1, y0 + self.cell - 0.1, height), "solid", "rubble"))

    def veg(self, x: int, y: int, height: float = 1.2):
        x0, y0 = self.cell_lo(x, y)
        self.add(Box((x0, y0, 0.0), (x0 + self.cell, y0 + self.cell, height), "veg", "veg"))

    def platform(self, x: int, y: int, l: int = 1):
        x0, y0 = self.cell_lo(x, y)
        z = l * FLOOR_H
        self.add(Box((x0, y0, z - 0.2), (x0 + self.cell, y0 + self.cell, z), "solid", "floor"))

    def stair(self, x: int, y: int):
        self.stairs.add((x, y))
        self._cache.clear()

    # ------------------------------------------------------------------ queries

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def center(self, node: Node) -> Vec:
        x, y, l = node
        return ((x + 0.5) * self.cell, (y + 0.5) * self.cell, l * FLOOR_H)

    def hug_base(self, node: Node, facing: tuple[int, int]) -> Vec:
        """單位會貼近格內面向敵方的掩體（矮牆、窗、胸牆）站立。"""
        key = ("hug", node, facing)
        if key in self._cache:
            return self._cache[key]
        c = self.center(node)
        x0, y0 = self.cell_lo(node[0], node[1])
        x1, y1 = x0 + self.cell, y0 + self.cell
        z = node[2] * FLOOR_H
        best = c
        fx, fy = facing
        for b in self._boxes_in_cell(node[0], node[1]):
            if b.tag not in ("lowwall", "window") or b.hi[2] <= z or b.lo[2] >= z + FLOOR_H:
                continue
            gap = 0.35
            if fy > 0 and b.hi[1] >= y1 - 0.01 and b.lo[1] > c[1]:
                best = (c[0], b.lo[1] - gap, c[2])
            elif fy < 0 and b.lo[1] <= y0 + 0.01 and b.hi[1] < c[1]:
                best = (c[0], b.hi[1] + gap, c[2])
            elif fx > 0 and b.hi[0] >= x1 - 0.01 and b.lo[0] > c[0]:
                best = (b.lo[0] - gap, c[1], c[2])
            elif fx < 0 and b.lo[0] <= x0 + 0.01 and b.hi[0] < c[0]:
                best = (b.hi[0] + gap, c[1], c[2])
        self._cache[key] = best
        return best

    @staticmethod
    def facing_to(a: Node, b: Node) -> tuple[int, int]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx) > abs(dy):
            return (1 if dx > 0 else -1, 0)
        if dy:
            return (0, 1 if dy > 0 else -1)
        return (0, 1)

    def _boxes_in_cell(self, x: int, y: int) -> list[Box]:
        key = ("in", x, y)
        if key not in self._cache:
            x0, y0 = self.cell_lo(x, y)
            x1, y1 = x0 + self.cell, y0 + self.cell
            self._cache[key] = [b for b in self.boxes
                                if b.lo[0] < x1 - 1e-6 and b.hi[0] > x0 + 1e-6
                                and b.lo[1] < y1 - 1e-6 and b.hi[1] > y0 + 1e-6]
        return self._cache[key]

    def floor_z(self, node: Node) -> float:
        return node[2] * FLOOR_H

    def _boxes_at(self, x: int, y: int) -> list[Box]:
        key = ("at", x, y)
        if key not in self._cache:
            cx, cy = (x + 0.5) * self.cell, (y + 0.5) * self.cell
            self._cache[key] = [b for b in self.boxes if b.contains_xy(cx, cy)]
        return self._cache[key]

    def has_floor(self, node: Node) -> bool:
        x, y, l = node
        if l == 0:
            return True
        z = l * FLOOR_H
        return any(b.tag == "floor" and abs(b.hi[2] - z) < 0.05 for b in self._boxes_at(x, y))

    def standable(self, node: Node) -> bool:
        key = ("st", node)
        if key in self._cache:
            return self._cache[key]
        x, y, l = node
        ok = self.in_bounds(x, y) and self.has_floor(node)
        if ok:
            z = l * FLOOR_H
            for b in self._boxes_at(x, y):
                if b.material != "solid" or b.tag == "floor":
                    continue
                if b.lo[2] < z + BODY_HI and b.hi[2] > z + BODY_LO:
                    ok = False
                    break
        self._cache[key] = ok
        return ok

    def blocked(self, a: Vec, b: Vec, materials=("solid",)) -> bool:
        d = sub(b, a)
        for box in self.boxes:
            if box.material in materials and box.tag != "floor_skip":
                hit = ray_box(a, d, box, 1.0)
                if hit and hit[1] > 1e-6 and hit[0] < 1.0 - 1e-6:
                    return True
        return False

    def step_cost(self, a: Node, b: Node) -> int | None:
        """相鄰（含斜向）節點移動成本：1 正常、2 需翻越；None 不可通行。"""
        key = ("step", a, b)
        if key in self._cache:
            return self._cache[key]
        cost = self._step_cost(a, b)
        self._cache[key] = cost
        return cost

    def _step_cost(self, a: Node, b: Node) -> int | None:
        if not self.standable(b):
            return None
        if a[2] != b[2]:
            if a[:2] == b[:2] and a[:2] in self.stairs:
                return 2
            return None
        dx, dy = b[0] - a[0], b[1] - a[1]
        if max(abs(dx), abs(dy)) != 1:
            return None
        if dx and dy:
            for side in ((a[0] + dx, a[1], a[2]), (a[0], a[1] + dy, a[2])):
                if self.step_cost(a, side) != 1:
                    return None
        ca, cb = self.center(a), self.center(b)
        z = a[2] * FLOOR_H
        knee_a, knee_b = (ca[0], ca[1], z + KNEE), (cb[0], cb[1], z + KNEE)
        if not self.blocked(knee_a, knee_b):
            return 1
        chest_a, chest_b = (ca[0], ca[1], z + CHEST), (cb[0], cb[1], z + CHEST)
        if not self.blocked(chest_a, chest_b):
            return 2
        return None

    def neighbors(self, node: Node) -> list[tuple[Node, int]]:
        x, y, l = node
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                n = (x + dx, y + dy, l)
                c = self.step_cost(node, n)
                if c is not None:
                    out.append((n, c))
        if (x, y) in self.stairs:
            for nl in (l - 1, l + 1):
                n = (x, y, nl)
                if nl >= 0 and self.standable(n):
                    out.append((n, 2))
        return out

    def nodes(self) -> list[Node]:
        levels = {0} | {int(round(b.hi[2] / FLOOR_H)) for b in self.boxes if b.tag == "floor"}
        return [(x, y, l) for l in sorted(levels) for y in range(self.h) for x in range(self.w)
                if self.standable((x, y, l))]

    def veg_visibility(self, a: Vec, b: Vec) -> float:
        """穿越植被的可見權重。"""
        d = sub(b, a)
        seg = length(d)
        if seg < 1e-6:
            return 1.0
        thick = 0.0
        for box in self.boxes:
            if box.material != "veg":
                continue
            hit = ray_box(a, d, box, 1.0)
            if hit:
                thick += max(0.0, hit[1] - hit[0]) * seg
        return max(0.0, 1.0 - thick / VEG_FULL)

    def sight(self, a: Vec, b: Vec) -> float:
        key = ("sight", tuple(round(v, 3) for v in a), tuple(round(v, 3) for v in b))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = 0.0 if self.blocked(a, b) else self.veg_visibility(a, b)
        self._cache[key] = value
        return value

    def raycast(self, origin: Vec, direction: Vec, max_len: float) -> tuple[float, Box] | None:
        """回傳最近的實心方塊命中距離（direction 為單位向量）。"""
        best = None
        for box in self.boxes:
            if box.material != "solid":
                continue
            hit = ray_box(origin, direction, box, max_len)
            if hit and hit[1] > 1e-6:
                t = max(hit[0], 0.0)
                if best is None or t < best[0]:
                    best = (t, box)
        return best

    def render(self, units=None, level: int = 0, known=None) -> str:
        marks = {}
        for u in units or []:
            if u.pos[2] == level and u.alive:
                marks[(u.pos[0], u.pos[1])] = (u.side[0].upper() if u.side == "blue" else u.side[0].lower())
        rows = []
        for y in reversed(range(self.h)):
            row = []
            for x in range(self.w):
                if (x, y) in marks:
                    row.append(marks[(x, y)])
                    continue
                tags = {b.tag for b in self._boxes_in_cell(x, y)
                        if b.lo[2] < (level + 1) * FLOOR_H and b.hi[2] > level * FLOOR_H}
                if level > 0 and not self.has_floor((x, y, level)):
                    row.append(" ")
                elif (x, y) in self.stairs:
                    row.append("H")
                elif "wall" in tags:
                    row.append("#")
                elif "window" in tags:
                    row.append("W")
                elif "wallside" in tags:
                    row.append("|")
                elif "lowwall" in tags:
                    row.append("=")
                elif "rubble" in tags:
                    row.append("%")
                elif "veg" in tags:
                    row.append('"')
                else:
                    row.append(".")
            rows.append(f"{y} " + " ".join(row))
        rows.append("  " + " ".join(str(x) for x in range(self.w)))
        return "\n".join(rows)
