"""run_llm_tests.py 的失敗判斷（不需要 LLM）：沒跑完、內容檢查打 ✗、腳本回報「問題：」都算失敗。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_llm_tests import problems


def test_problems():
    ok = "== 地名命令 ==\n  ✓ 「撤回南側集結點」→ A3\n\n全部通過\n"
    assert problems(0, ok) == []
    bad = "  ✓ 甲\n  ✗ 「去醫院廢墟看看情況」→ B4（得到 None）\n\n問題：「去醫院廢墟看看情況」→ B4（得到 None）\n"
    found = problems(0, bad)
    assert any("醫院廢墟" in p for p in found) and "腳本回報問題" in found, found
    assert problems(1, ok) == ["結束碼 1"]
    assert "有 Traceback" in problems(0, "Traceback (most recent call last):\n  x\n")
    assert problems(0, "隊長說：這沒有問題：照辦\n") == [], "句子中間出現「問題：」不算"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    test_problems()
    print("OK test_problems")
    print("llm_runner_test OK")


if __name__ == "__main__":
    main()
