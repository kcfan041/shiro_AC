"""一次跑完需要 LLM 的實測腳本（需要 .env 的 LLM 設定）。
失敗＝沒跑完（結束碼不是 0、有 Traceback），或內容檢查沒過（輸出有 ✗ 或「問題：」）；其他內容請看輸出。

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


def problems(returncode: int, out: str) -> list[str]:
    """這個腳本的問題（空的＝通過）：沒跑完，或內容檢查打了 ✗、最後印了「問題：」。"""
    found = []
    if returncode != 0:
        found.append(f"結束碼 {returncode}")
    if "Traceback" in out:
        found.append("有 Traceback")
    found += [f"檢查沒過：{line.strip()}" for line in out.splitlines() if "✗" in line]
    if any(line.lstrip().startswith("問題：") for line in out.splitlines()):
        found.append("腳本回報問題")
    return found


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
        found = problems(r.returncode, out)
        print(f"{'FAIL' if found else 'OK  '} {name}（{time.time() - t0:.0f}s）", flush=True)
        if found:
            failed.append(name)
            print("\n".join(f"  {p}" for p in found[:12]))
            if r.returncode != 0 or "Traceback" in out:
                print("\n".join(out.splitlines()[-12:]))
    print(f"\n{len(SCRIPTS) - len(failed)} 通過，{len(failed)} 失敗{'：' + ', '.join(failed) if failed else ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
