"""建置戰術地圖前端（web/tac → web/static/dist）：需要 Node（20.19 以上）。伺服器執行時不需要 Node。

用法：python scripts/build_web.py [--test]
  沒有 node_modules 或 package-lock.json 比它新 → 先 npm ci；再型別檢查＋打包；--test 另外跑 Vitest。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAC = os.path.join(ROOT, "web", "tac")
DIST = os.path.join(ROOT, "web", "static", "dist")


def npm(*args: str) -> int:
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not exe:
        print("找不到 npm：要先安裝 Node（20.19 以上）。")
        return 127
    return subprocess.run([exe, *args], cwd=TAC).returncode


def need_install() -> bool:
    mods = os.path.join(TAC, "node_modules")
    lock = os.path.join(TAC, "package-lock.json")
    stamp = os.path.join(mods, ".package-lock.json")
    return not os.path.isdir(mods) or not os.path.exists(stamp) or os.path.getmtime(lock) > os.path.getmtime(stamp)


def main() -> int:
    if need_install() and (rc := npm("ci")):
        return rc
    if rc := npm("run", "build"):
        return rc
    if "--test" in sys.argv and (rc := npm("test")):
        return rc
    print(f"已建置：{DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
