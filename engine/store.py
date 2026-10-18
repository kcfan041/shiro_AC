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


def save(world: World, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(world.to_dict(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
