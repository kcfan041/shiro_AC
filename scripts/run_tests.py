import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE = ["multi_test.py", "mission_test.py", "layout_test.py", "belief_test.py", "map_test.py", "enemy_test.py",
           "tactical_test.py", "tworld_test.py", "engage_test.py", "enemy_tactics_test.py", "awareness_test.py", "breach_test.py", "reserve_test.py", "persona_test.py", "remnant_test.py", "handover_test.py", "sop_test.py", "multi_engage_test.py", "web_engine_test.py", "web_test.py", "zone_test.py", "event_test.py", "allies_test.py", "lock_test.py", "planjob_test.py", "timeline_test.py", "overload_test.py", "presentation_test.py", "terrain_test.py", "cells_test.py", "llm_runner_test.py", "facing_test.py", "behavior_test.py", "stance_test.py", "terrain_edits_test.py", "dig_test.py"]


def main() -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed, passed = [], 0
    for name in OFFLINE:
        path = os.path.join(ROOT, "scripts", name)
        if not os.path.exists(path):
            print(f"-- {name}：略過（不存在）")
            continue
        passed += 1
        start = time.monotonic()
        proc = subprocess.run([sys.executable, path], cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        took = time.monotonic() - start
        if proc.returncode == 0:
            print(f"OK   {name}（{took:.1f}s）")
        else:
            failed.append(name)
            passed -= 1
            print(f"FAIL {name}（{took:.1f}s）")
            print((proc.stdout + proc.stderr)[-2000:])
    # 戰術地圖前端（web/tac）的 Vitest：要有 Node 與 node_modules（python scripts/build_web.py 會裝）
    tac = os.path.join(ROOT, "web", "tac")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm and os.path.isdir(os.path.join(tac, "node_modules")):
        start = time.monotonic()
        proc = subprocess.run([npm, "test", "--silent"], cwd=tac, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        took = time.monotonic() - start
        if proc.returncode == 0:
            passed += 1
            print(f"OK   web/tac Vitest（{took:.1f}s）")
        else:
            failed.append("web/tac Vitest")
            print(f"FAIL web/tac Vitest（{took:.1f}s）")
            print((proc.stdout + proc.stderr)[-2000:])
    else:
        print("-- web/tac Vitest：略過（沒有 Node 或還沒跑 python scripts/build_web.py）")
    print(f"\n{passed} 通過，{len(failed)} 失敗" + (f"：{', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
