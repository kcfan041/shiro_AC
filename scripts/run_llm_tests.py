"""一次跑完需要 LLM 的實測腳本（需要 .env 的 LLM 設定）。只檢查能否跑完不出錯；內容請看輸出。

用法：python scripts/run_llm_tests.py [輸出資料夾，預設 ./data/llm_runs]
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = ["sim_test.py", "dialogue_test.py", "reflect_test.py", "card_test.py", "play_test.py",
           "combat_play_test.py", "reserve_play_test.py", "remnant_play_test.py", "handover_play_test.py",
           "multi_play_test.py", "zone_play_test.py"]


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "llm_runs")
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = []
    for name in SCRIPTS:
        path = os.path.join(ROOT, "scripts", name)
        if not os.path.exists(path):
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, "-B", path], cwd=ROOT, env=env, capture_output=True)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        with open(os.path.join(out_dir, name.replace(".py", ".txt")), "w", encoding="utf-8") as f:
            f.write(out)
        ok = r.returncode == 0 and "Traceback" not in out
        print(f"{'OK  ' if ok else 'FAIL'} {name}（{time.time() - t0:.0f}s）", flush=True)
        if not ok:
            failed.append(name)
            print("\n".join(out.splitlines()[-12:]))
    print(f"\n{len(SCRIPTS) - len(failed)} 通過，{len(failed)} 失敗{'：' + ', '.join(failed) if failed else ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
