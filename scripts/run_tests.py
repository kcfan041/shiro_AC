import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFLINE = ["multi_test.py", "mission_test.py", "layout_test.py", "belief_test.py", "map_test.py", "enemy_test.py",
           "tactical_test.py", "tworld_test.py", "engage_test.py", "enemy_tactics_test.py", "awareness_test.py", "breach_test.py", "reserve_test.py", "persona_test.py"]


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
    print(f"\n{passed} 通過，{len(failed)} 失敗" + (f"：{', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
