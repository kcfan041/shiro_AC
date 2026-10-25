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
SIGHT_CACHE_MAX = 800000   # 地圖視線快取上限（靜態結果，跨步、跨戰鬥沿用；超過就清空）
H_STEP = 0.2               # 地面高度、邊緣土堤的級距（m）
MOVE_MODES = {"foot": (0.4, 1.2)}   # 移動方式 → (正常走的高差上限, 翻越的高差上限)，超過就過不去
HUG_BERM = 0.6             # 面向那一邊的土堤高出地面這麼多才貼過去站
REST_BERM = 0.4            # 面向那一邊的土堤高出地面這麼多就算依託

Node = tuple[int, int, int]


@dataclass
class TacMap:
    w: int = 10
    h: int = 10
    cell: float = CELL
    boxes: list[Box] = field(default_factory=list)
    stairs: set[tuple[int, int]] = field(default_factory=set)
    rooms: list[dict] = field(default_factory=list)   # {"id", "rect": (x0,y0,x1,y1) 外牆, "doors": [(x,y)], "cells": [...]}
    ground: dict = field(default_factory=dict)        # (x, y) → 地面高度（H_STEP 的整數倍；只存不是 0 的）
    berm: dict = field(default_factory=dict)          # (x, y, "E"|"N") → 那一格東邊／北邊的土堤高度（同上）
    berm_mat: dict = field(default_factory=dict)      # 同上的鍵 → 材質（soil 土…，先只給畫面用）
    _cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ terrain（地面高度、邊緣土堤）

    @property
    def has_relief(self) -> bool:
        """有沒有任何高低差。全平的地圖跳過所有地形檢查，結果與加入地形前完全相同。"""
        return bool(self.ground) or bool(self.berm)

    def set_ground(self, x: int, y: int, units: int):
        """設定一格的地面高度（單位 H_STEP）。"""
        if units:
            self.ground[(x, y)] = int(units)
        else:
            self.ground.pop((x, y), None)
        self._cache.clear()

    def ground_h(self, x: int, y: int) -> float:
        return self.ground.get((x, y), 0) * H_STEP

    @staticmethod
    def edge_key(x: int, y: int, side: str) -> tuple[int, int, str]:
        """格子某一邊的鍵：西邊、南邊換成鄰格的東邊、北邊（兩格共用一條邊）。"""
        if side == "E":
            return (x, y, "E")
        if side == "W":
            return (x - 1, y, "E")
        if side == "N":
            return (x, y, "N")
        return (x, y - 1, "N")

    def set_berm(self, x: int, y: int, side: str, units: int, mat: str = "soil"):
        """設定格子某一邊的土堤高度（單位 H_STEP，從兩側較高的地面算起）。"""
        k = self.edge_key(x, y, side)
        if units:
            self.berm[k] = int(units)
            self.berm_mat[k] = mat
        else:
            self.berm.pop(k, None)
            self.berm_mat.pop(k, None)
        self._cache.clear()

    def berm_h(self, a, b) -> float:
        """兩個上下左右相鄰的格子之間那條邊的土堤高度；不相鄰（含斜向）回傳 0。"""
        if not self.berm:
            return 0.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx == 1 and dy == 0:
            k = (a[0], a[1], "E")
        elif dx == -1 and dy == 0:
            k = (b[0], b[1], "E")
        elif dy == 1 and dx == 0:
            k = (a[0], a[1], "N")
        elif dy == -1 and dx == 0:
            k = (b[0], b[1], "N")
        else:
            return 0.0
        return self.berm.get(k, 0) * H_STEP

    def berm_toward(self, node: Node, direction) -> float:
        """面向 direction（取主要軸）那一邊的土堤頂端高出這格地面多少；樓上、沒有土堤回傳 0。"""
        if not self.berm or node[2] != 0:
            return 0.0
        dx, dy = direction[0], direction[1]
        if abs(dx) >= abs(dy):
            nb = (node[0] + (1 if dx > 0 else -1), node[1])
        else:
            nb = (node[0], node[1] + (1 if dy > 0 else -1))
        h = self.berm_h(node, nb)
        if h <= 0:
            return 0.0
        return max(self.ground_h(node[0], node[1]), self.ground_h(*nb)) + h - self.ground_h(node[0], node[1])

    def terrain_hit(self, a: Vec, b: Vec) -> float | None:
        """線段 a→b 第一次碰到地形（某一格的地面，或跨過格界時「兩側較高的地面＋土堤」）的參數 t（0～1）。
        端點上的接觸不算；沒碰到回傳 None。全平的地圖一律 None（地面 z＝0 由呼叫端照舊處理）。"""
        if not self.has_relief:
            return None
        c = self.cell
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        x, y = int(a[0] // c), int(a[1] // c)
        ex, ey = int(b[0] // c), int(b[1] // c)
        sx = 1 if dx > 0 else -1
        sy = 1 if dy > 0 else -1
        tdx = abs(c / dx) if dx else float("inf")
        tdy = abs(c / dy) if dy else float("inf")
        nx = ((x + (sx > 0)) * c - a[0]) / dx if dx else float("inf")
        ny = ((y + (sy > 0)) * c - a[1]) / dy if dy else float("inf")
        lo, hi = 1e-6, 1.0 - 1e-6
        t_in = 0.0
        g = self.ground_h(x, y)
        guard = 0
        limit = abs(ex - x) + abs(ey - y) + 4
        while guard <= limit:
            guard += 1
            t_out = min(nx, ny, 1.0)
            z_in, z_out = a[2] + dz * t_in, a[2] + dz * t_out
            if min(z_in, z_out) < g - 1e-6:
                th = t_in if z_in < g - 1e-6 else min(t_out, max(t_in, (g - a[2]) / dz))
                if lo < th < hi:
                    return th
            if t_out >= 1.0 or (x == ex and y == ey):
                return None
            if nx < ny:
                nxt = (x + sx, y)
                nx += tdx
            else:
                nxt = (x, y + sy)
                ny += tdy
            g2 = self.ground_h(*nxt)
            bar = max(g, g2) + self.berm_h((x, y), nxt)
            if a[2] + dz * t_out < bar - 1e-6 and lo < t_out < hi:
                return t_out
            x, y = nxt
            g = g2
            t_in = t_out
        return None

    def terrain_t(self, origin: Vec, direction: Vec, max_len: float) -> float | None:
        """從 origin 沿單位向量 direction 走多遠碰到地形（最遠 max_len）；全平或沒碰到回傳 None。"""
        if not self.has_relief or max_len <= 0:
            return None
        end = (origin[0] + direction[0] * max_len, origin[1] + direction[1] * max_len,
               origin[2] + direction[2] * max_len)
        t = self.terrain_hit(origin, end)
        return None if t is None else t * max_len

    def _terrain_cost(self, a: Node, b: Node, mode: str = "foot") -> int | None:
        """地面層上下左右相鄰兩格之間的地形通行成本：要爬過「兩側較高的地面＋土堤」。1 正常、2 翻越、None 過不去。"""
        walk, climb = MOVE_MODES[mode]
        ga, gb = self.ground_h(a[0], a[1]), self.ground_h(b[0], b[1])
        bar = max(ga, gb) + self.berm_h(a, b)
        up, down = bar - ga, bar - gb
        if up > climb + 1e-6 or down > climb + 1e-6:
            return None
        return 1 if up <= walk + 1e-6 else 2

    # ------------------------------------------------------------------ rooms / doors

    def add_room(self, x0: int, y0: int, x1: int, y1: int, doors) -> dict:
        """登記一棟單間建築：外牆 (x0,y0)-(x1,y1)，doors＝外牆上留空的格子。"""
        cells = [(x, y) for x in range(x0 + 1, x1) for y in range(y0 + 1, y1)]
        room = {"id": len(self.rooms), "rect": (x0, y0, x1, y1), "doors": sorted(tuple(d) for d in doors),
                "cells": cells}
        self.rooms.append(room)
        return room

    def room_at(self, node) -> dict | None:
        x, y = node[0], node[1]
        for r in self.rooms:
            x0, y0, x1, y1 = r["rect"]
            if x0 < x < x1 and y0 < y < y1:
                return r
            if (x, y) in r["doors"]:
                return r
        return None

    def door_outside(self, room: dict, door) -> tuple[int, int]:
        """門外的方向（單位向量 dx, dy）。"""
        x0, y0, x1, y1 = room["rect"]
        x, y = door
        if y == y0:
            return (0, -1)
        if y == y1:
            return (0, 1)
        if x == x0:
            return (-1, 0)
        return (1, 0)

    def door_stack_cells(self, room: dict, door) -> list[tuple[int, int, int]]:
        """門外待命格：門外 2 格內的可站格，依「房內有幾格看得到它」排序（越隱蔽越前），再依離門距離。"""
        key = ("stack", room["id"], tuple(door))
        if key in self._cache:
            return self._cache[key]
        x0, y0, x1, y1 = room["rect"]
        x, y = door
        cands = []
        for cx in range(x - 2, x + 3):
            for cy in range(y - 2, y + 3):
                if (cx, cy) == (x, y) or (x0 <= cx <= x1 and y0 <= cy <= y1):
                    continue
                c = (cx, cy, 0)
                if not self.in_bounds(cx, cy) or not self.standable(c):
                    continue
                cands.append(c)
        inner = room["cells"]
        scored = []
        for c in cands:
            cc = self.center(c)
            eye = (cc[0], cc[1], 1.0)
            seen = sum(1 for (ix, iy) in inner
                       if self.sight((ix * self.cell + self.cell / 2, iy * self.cell + self.cell / 2, 1.0), eye) > 0.3)
            dist = max(abs(cx - x) for cx in (c[0],)) + max(abs(cy - y) for cy in (c[1],))
            scored.append((seen, dist, c))
        out = [c for _, _, c in sorted(scored)]
        self._cache[key] = out
        return out

    def stack_exposure(self, room: dict, cell) -> int:
        cc = self.center(cell)
        eye = (cc[0], cc[1], 1.0)
        return sum(1 for (ix, iy) in room["cells"]
                   if self.sight((ix * self.cell + self.cell / 2, iy * self.cell + self.cell / 2, 1.0), eye) > 0.3)

    def door_entry(self, room: dict, door) -> tuple[int, int, int]:
        """門內第一格。"""
        dx, dy = self.door_outside(room, door)
        return (door[0] - dx, door[1] - dy, 0)

    # ------------------------------------------------------------------ build

    def add(self, box: Box):
        self.boxes.append(box)
        self._cache.clear()

    def cell_lo(self, x: int, y: int) -> tuple[float, float]:
        return x * self.cell, y * self.cell

    def wall(self, x: int, y: int, height: float = 3.0, l: int = 0, tag: str = "wall"):
        x0, y0 = self.cell_lo(x, y)
        z0 = self.floor_z((x, y, l))
        self.add(Box((x0, y0, z0), (x0 + self.cell, y0 + self.cell, z0 + height), "solid", tag))

    def low_wall(self, x: int, y: int, side: str, height: float = 1.0, thick: float = 0.3, l: int = 0):
        x0, y0 = self.cell_lo(x, y)
        x1, y1 = x0 + self.cell, y0 + self.cell
        z0 = self.floor_z((x, y, l))
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
        z0 = self.floor_z((x, y, l))
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
        z0 = self.floor_z((x, y, l))
        self.add(Box((x0, y0, z0), (x0 + self.cell, y0 + self.cell, z0 + sill), "solid", "window"))
        self.add(Box((x0, y0, z0 + top), (x0 + self.cell, y0 + self.cell, z0 + FLOOR_H), "solid", "window"))

    def rubble(self, x: int, y: int, height: float = 0.6):
        x0, y0 = self.cell_lo(x, y)
        z0 = self.ground_h(x, y)
        self.add(Box((x0 + 0.1, y0 + 0.1, z0), (x0 + self.cell - 0.1, y0 + self.cell - 0.1, z0 + height), "solid", "rubble"))

    def veg(self, x: int, y: int, height: float = 1.2):
        x0, y0 = self.cell_lo(x, y)
        z0 = self.ground_h(x, y)
        self.add(Box((x0, y0, z0), (x0 + self.cell, y0 + self.cell, z0 + height), "veg", "veg"))

    def platform(self, x: int, y: int, l: int = 1):
        x0, y0 = self.cell_lo(x, y)
        z = self.floor_z((x, y, l))
        self.add(Box((x0, y0, z - 0.2), (x0 + self.cell, y0 + self.cell, z), "solid", "floor"))

    def stair(self, x: int, y: int):
        self.stairs.add((x, y))
        self._cache.clear()

    # ------------------------------------------------------------------ queries

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def center(self, node: Node) -> Vec:
        x, y, l = node
        return ((x + 0.5) * self.cell, (y + 0.5) * self.cell, self.floor_z(node))

    def hug_base(self, node: Node, facing: tuple[int, int]) -> Vec:
        """單位會貼近格內面向敵方的掩體（矮牆、窗、胸牆）站立。"""
        key = ("hug", node, facing)
        if key in self._cache:
            return self._cache[key]
        c = self.center(node)
        x0, y0 = self.cell_lo(node[0], node[1])
        x1, y1 = x0 + self.cell, y0 + self.cell
        best = c
        fx, fy = facing
        z = self.floor_z(node)
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
        if best == c and (fx or fy) and self.berm_toward(node, facing) >= HUG_BERM:
            gap = 0.5                           # 土堤在格界上：貼著邊站
            if abs(fx) >= abs(fy):
                best = ((x1 - gap) if fx > 0 else (x0 + gap), c[1], c[2])
            else:
                best = (c[0], (y1 - gap) if fy > 0 else (y0 + gap), c[2])
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
            cand = [self.boxes[i] for i in self._grid().get((x, y), ())]
            self._cache[key] = [b for b in cand
                                if b.lo[0] < x1 - 1e-6 and b.hi[0] > x0 + 1e-6
                                and b.lo[1] < y1 - 1e-6 and b.hi[1] > y0 + 1e-6]
        return self._cache[key]

    def floor_z(self, node: Node) -> float:
        """站在這個節點時腳下的高度：地面高度＋樓層 × 3 m。"""
        if not self.ground:
            return node[2] * FLOOR_H
        return self.ground.get((node[0], node[1]), 0) * H_STEP + node[2] * FLOOR_H

    def _boxes_at(self, x: int, y: int) -> list[Box]:
        key = ("at", x, y)
        if key not in self._cache:
            cx, cy = (x + 0.5) * self.cell, (y + 0.5) * self.cell
            cand = [self.boxes[i] for i in self._grid().get((x, y), ())] if self.in_bounds(x, y) else self.boxes
            self._cache[key] = [b for b in cand if b.contains_xy(cx, cy)]
        return self._cache[key]

    def has_floor(self, node: Node) -> bool:
        x, y, l = node
        if l == 0:
            return True
        z = self.floor_z(node)
        return any(b.tag == "floor" and abs(b.hi[2] - z) < 0.05 for b in self._boxes_at(x, y))

    def standable(self, node: Node) -> bool:
        key = ("st", node)
        if key in self._cache:
            return self._cache[key]
        x, y, l = node
        ok = self.in_bounds(x, y) and self.has_floor(node)
        if ok:
            z = self.floor_z(node)
            for b in self._boxes_at(x, y):
                if b.material != "solid" or b.tag == "floor":
                    continue
                if b.lo[2] < z + BODY_HI and b.hi[2] > z + BODY_LO:
                    ok = False
                    break
        self._cache[key] = ok
        return ok

    # ------------------------------------------------------------------ spatial index

    def _grid(self) -> dict:
        g = self._cache.get("grid")
        if g is None:
            g = {}
            for i, b in enumerate(self.boxes):
                x0, x1 = int(b.lo[0] // self.cell), int((b.hi[0] - 1e-9) // self.cell)
                y0, y1 = int(b.lo[1] // self.cell), int((b.hi[1] - 1e-9) // self.cell)
                for x in range(max(0, x0), min(self.w - 1, x1) + 1):
                    for y in range(max(0, y0), min(self.h - 1, y1) + 1):
                        g.setdefault((x, y), []).append(i)
            self._cache["grid"] = g
        return g

    def _cells_on_segment(self, a: Vec, b: Vec) -> list[tuple[int, int]]:
        """2D 格線遍歷（Amanatides–Woo），回傳線段經過的格子（含端點）。"""
        return list(self._iter_cells(a, b))

    def _iter_cells(self, a: Vec, b: Vec):
        """同 _cells_on_segment，但一格一格產生（呼叫端可以提早停）。"""
        c = self.cell
        x, y = int(a[0] // c), int(a[1] // c)
        ex, ey = int(b[0] // c), int(b[1] // c)
        dx, dy = b[0] - a[0], b[1] - a[1]
        yield (x, y)
        sx = 1 if dx > 0 else -1
        sy = 1 if dy > 0 else -1
        tdx = abs(c / dx) if dx else float("inf")
        tdy = abs(c / dy) if dy else float("inf")
        nx = ((x + (sx > 0)) * c - a[0]) / dx if dx else float("inf")
        ny = ((y + (sy > 0)) * c - a[1]) / dy if dy else float("inf")
        guard = 0
        limit = self.w + self.h + 4
        while (x != ex or y != ey) and guard < limit:
            guard += 1
            if nx < ny:
                x += sx
                nx += tdx
            else:
                y += sy
                ny += tdy
            yield (x, y)

    def _boxes_along(self, a: Vec, b: Vec) -> list[Box]:
        g = self._grid()
        seen, out = set(), []
        for cell in self._cells_on_segment(a, b):
            for i in g.get(cell, ()):
                if i not in seen:
                    seen.add(i)
                    out.append(self.boxes[i])
        return out

    def blocked(self, a: Vec, b: Vec, materials=("solid",), terrain: bool = True) -> bool:
        """線段被實心方塊擋住；terrain＝True 時地形（地面、土堤）也算。"""
        if terrain and self.has_relief and self.terrain_hit(a, b) is not None:
            return True
        d = sub(b, a)
        for box in self._boxes_along(a, b):
            if box.material in materials and box.tag != "floor_skip":
                hit = ray_box(a, d, box, 1.0)
                if hit and hit[1] > 1e-6 and hit[0] < 1.0 - 1e-6:
                    return True
        return False

    def step_cost(self, a: Node, b: Node, mode: str = "foot") -> int | None:
        """相鄰（含斜向）節點移動成本：1 正常、2 需翻越；None 不可通行。mode＝移動方式（目前只有步行 foot）。"""
        key = ("step", a, b, mode)
        if key in self._cache:
            return self._cache[key]
        cost = self._step_cost(a, b, mode)
        self._cache[key] = cost
        return cost

    def _step_cost(self, a: Node, b: Node, mode: str = "foot") -> int | None:
        if not self.standable(b):
            return None
        if a[2] != b[2]:
            if a[:2] == b[:2] and a[:2] in self.stairs:
                return 2
            return None
        dx, dy = b[0] - a[0], b[1] - a[1]
        if max(abs(dx), abs(dy)) != 1:
            return None
        relief = self.has_relief and a[2] == 0
        terr = 1
        if dx and dy:
            for side in ((a[0] + dx, a[1], a[2]), (a[0], a[1] + dy, a[2])):
                if self.step_cost(a, side, mode) != 1:
                    return None
                if relief and self._terrain_cost(side, b, mode) != 1:    # 斜走也要跨過另外兩條邊
                    return None
        elif relief:
            terr = self._terrain_cost(a, b, mode)
            if terr is None:
                return None
        # 方塊（牆、矮牆、窗台…）：膝高射線被擋、胸高通暢 → 翻越；地形另外算（上面），這裡只看方塊
        ca, cb = self.center(a), self.center(b)
        za, zb = ca[2], cb[2]
        knee_a, knee_b = (ca[0], ca[1], za + KNEE), (cb[0], cb[1], zb + KNEE)
        if not self.blocked(knee_a, knee_b, terrain=False):
            return terr
        chest_a, chest_b = (ca[0], ca[1], za + CHEST), (cb[0], cb[1], zb + CHEST)
        if not self.blocked(chest_a, chest_b, terrain=False):
            return 2
        return None

    def neighbors(self, node: Node) -> list[tuple[Node, int]]:
        nb = self._cache.get("_nb")
        if nb is None:
            nb = self._cache["_nb"] = {}
        hit = nb.get(node)
        if hit is not None:
            return hit
        out = self._neighbors(node)
        nb[node] = out
        return out

    def warm(self):
        """預先算好地面層所有格的鄰接（全圖第一次尋路就不會卡）。"""
        for y in range(self.h):
            for x in range(self.w):
                if self.standable((x, y, 0)):
                    self.neighbors((x, y, 0))

    def _neighbors(self, node: Node) -> list[tuple[Node, int]]:
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
        """穿越植被的可見權重。a 為觀察者眼點：觀察者自己所在格的植被不遮擋（往外看不受影響）。"""
        d = sub(b, a)
        seg = length(d)
        if seg < 1e-6:
            return 1.0
        thick = 0.0
        own = (int(a[0] // self.cell), int(a[1] // self.cell))
        for box in self._boxes_along(a, b):
            if box.material != "veg":
                continue
            bx = (int(((box.lo[0] + box.hi[0]) / 2) // self.cell), int(((box.lo[1] + box.hi[1]) / 2) // self.cell))
            if bx == own and box.lo[2] <= a[2] <= box.hi[2] + 1e-6:
                continue
            hit = ray_box(a, d, box, 1.0)
            if hit:
                thick += max(0.0, hit[1] - hit[0]) * seg
        return max(0.0, 1.0 - thick / VEG_FULL)

    def sight(self, a: Vec, b: Vec) -> float:
        sc = self._cache.get("_sight")
        if sc is None or len(sc) > SIGHT_CACHE_MAX:
            sc = self._cache["_sight"] = {}
        key = (tuple(round(v, 3) for v in a), tuple(round(v, 3) for v in b))
        cached = sc.get(key)
        if cached is not None:
            return cached
        value = self._sight_raw(a, b)
        sc[key] = value
        return value

    def _sight_raw(self, a: Vec, b: Vec) -> float:
        """＝ 0 if blocked(a, b) else veg_visibility(a, b)，但只走一次格線、碰到擋住的實心物就停。
        方塊檢查順序與植被厚度的累加順序都和分開算時相同，結果一致。地形（地面、土堤）擋住也是 0。"""
        if self.has_relief and self.terrain_hit(a, b) is not None:
            return 0.0
        d = sub(b, a)
        seg = length(d)
        if seg < 1e-6:
            return 0.0 if self.blocked(a, b) else 1.0
        g = self._grid()
        boxes = self.boxes
        own = (int(a[0] // self.cell), int(a[1] // self.cell))
        seen = set()
        thick = 0.0
        for cell in self._iter_cells(a, b):
            for i in g.get(cell, ()):
                if i in seen:
                    continue
                seen.add(i)
                box = boxes[i]
                if box.material == "solid":
                    if box.tag != "floor_skip":
                        hit = ray_box(a, d, box, 1.0)
                        if hit and hit[1] > 1e-6 and hit[0] < 1.0 - 1e-6:
                            return 0.0
                elif box.material == "veg":
                    bx = (int(((box.lo[0] + box.hi[0]) / 2) // self.cell),
                          int(((box.lo[1] + box.hi[1]) / 2) // self.cell))
                    if bx == own and box.lo[2] <= a[2] <= box.hi[2] + 1e-6:
                        continue
                    hit = ray_box(a, d, box, 1.0)
                    if hit:
                        thick += max(0.0, hit[1] - hit[0]) * seg
        return max(0.0, 1.0 - thick / VEG_FULL)

    def raycast(self, origin: Vec, direction: Vec, max_len: float) -> tuple[float, Box] | None:
        """回傳最近的實心方塊命中距離（direction 為單位向量）。"""
        best = None
        W, H = self.w * self.cell - 1e-6, self.h * self.cell - 1e-6
        t_end = max_len
        for i, lim in ((0, W), (1, H)):
            d = direction[i]
            if d > 1e-12:
                t_end = min(t_end, (lim - origin[i]) / d)
            elif d < -1e-12:
                t_end = min(t_end, (0.0 - origin[i]) / d)
        t_end = max(0.0, t_end)
        a = (min(max(origin[0], 0.0), W), min(max(origin[1], 0.0), H), origin[2])
        b = (min(max(origin[0] + direction[0] * t_end, 0.0), W),
             min(max(origin[1] + direction[1] * t_end, 0.0), H), origin[2])
        for box in self._boxes_along(a, b):
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
