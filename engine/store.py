from __future__ import annotations

import json
import logging
import os

from engine.models import World

log = logging.getLogger(__name__)


def load(path: str) -> World | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf8") as f:
        data = json.load(f)
    from engine import scenario
    if data.get("scenario") != scenario.SCENARIO_ID:
        backup = f"{path}.old_scenario"
        os.replace(path, backup)
        log.warning("存檔場景（%s）與目前場景（%s）不同，已移至 %s；請以 /ac_wipe 清除舊頻道後重新部署。",
                    data.get("scenario"), scenario.SCENARIO_ID, backup)
        return None
    try:
        return World.from_dict(data)
    except (TypeError, KeyError) as e:
        backup = f"{path}.incompatible"
        os.replace(path, backup)
        log.warning("存檔格式不相容，已移至 %s：%r", backup, e)
        return None


def retire_old(path: str) -> str | None:
    """存檔是舊版模擬（sim_version 比 models.SIM_VERSION 小，或沒有這個欄位）→ 移到 <path>.pre_v<版本>，回傳備份路徑。
    網頁版啟動時呼叫（模擬重構 sim_v3 合回時重置世界，帳號另存不受影響）。"""
    from engine.models import SIM_VERSION
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf8") as f:
            ver = json.load(f).get("sim_version", 1)
    except (OSError, ValueError):
        return None
    if ver >= SIM_VERSION:
        return None
    backup = f"{path}.pre_v{SIM_VERSION}"
    os.replace(path, backup)
    log.warning("存檔是舊版模擬（v%s，目前 v%s），已移至 %s，開新世界（帳號保留）。", ver, SIM_VERSION, backup)
    return backup


def save(world: World, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(world.to_dict(), f, ensure_ascii=False, indent=2)
    import time
    for i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 9:
                raise
            time.sleep(0.05 * (i + 1))
