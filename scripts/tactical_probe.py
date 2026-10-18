import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tactical import runner, scenarios


def main():
    names = sys.argv[1:] or list(scenarios.SCENARIOS)
    trace = os.environ.get("TRACE") == "1"
    for name in names:
        t = time.time()
        r = runner.run(scenarios.SCENARIOS[name], seed=int(os.environ.get("SEED", 1)), trace=trace)
        print(f"{name}: winner={r['winner']} ticks={r['ticks']} first_down={r['first_down']} "
              f"lost b/r={r['blue_lost']}/{r['red_lost']} reports={len(r['reports'])} {time.time() - t:.2f}s")
        if trace:
            print("\n".join(r["battle"].log[-30:]))


if __name__ == "__main__":
    main()
