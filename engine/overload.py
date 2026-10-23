"""過載處理（模擬重構第 10 步）：網頁版每一拍量模擬花了多久，決定 AI 降級等級與顯示的時間倍率。

- 降級等級（`Simulation.lod`，玩家小隊永遠完整規劃）：
  0 正常；1 非玩家而且未接敵的組每 2.5 秒才規劃；2 所有非玩家的組（敵軍、友軍、殘部）每 2.5 秒才規劃。
- 近 10 拍平均負載（模擬耗時 ÷ 一步秒數）> 0.8 → 升一級；< 0.4 → 降一級；每次調整後至少隔 10 拍再調。
- 降到最高級仍跟不上，背景迴圈就自然變慢（一拍算完才排下一拍），時間倍率＝遊戲秒數 ÷ 實際秒數（近 20 拍）。
- 只有網頁版背景迴圈會用；測試與批次不經過這裡，結果和電腦速度無關。
"""
from __future__ import annotations

LOD_MAX = 2
WINDOW = 10
HIGH, LOW = 0.8, 0.4
SCALE_WINDOW = 20
SLOW_SHOW = 0.95          # 時間倍率低於這個值，介面才顯示


class Overload:
    def __init__(self):
        self.lod = 0
        self.loads: list[float] = []
        self.walls: list[tuple[float, float]] = []    # (遊戲秒數, 實際秒數)
        self.cooldown = 0

    def record(self, sim_secs: float, step_secs: float, wall_secs: float | None = None) -> int:
        """記一拍：sim_secs＝模擬耗時；wall_secs＝這一拍從開始到下一拍開始的實際秒數（含等待）。回傳新的降級等級。"""
        step_secs = max(1e-6, step_secs)
        self.loads = (self.loads + [sim_secs / step_secs])[-WINDOW:]
        if wall_secs is not None:
            self.walls = (self.walls + [(step_secs, max(1e-6, wall_secs))])[-SCALE_WINDOW:]
        if self.cooldown > 0:
            self.cooldown -= 1
            return self.lod
        if len(self.loads) < WINDOW:
            return self.lod
        avg = sum(self.loads) / len(self.loads)
        if avg > HIGH and self.lod < LOD_MAX:
            self.lod += 1
            self.cooldown = WINDOW
        elif avg < LOW and self.lod > 0:
            self.lod -= 1
            self.cooldown = WINDOW
        return self.lod

    @property
    def time_scale(self) -> float:
        if not self.walls:
            return 1.0
        game = sum(g for g, _ in self.walls)
        wall = sum(w for _, w in self.walls)
        return min(1.0, game / wall)

    def view(self) -> dict:
        """給介面：時間倍率（只在變慢時給）與降級等級。"""
        s = round(self.time_scale, 2)
        return {"time_scale": s if s < SLOW_SHOW else 1.0, "lod": self.lod}
