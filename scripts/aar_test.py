"""戰後摘要進事件欄（engine/replay.py aar_text、Simulation._replay_saved；TODO P1 戰後復盤）：交戰存成回放時，
用各指揮官的玩家視圖整理一行摘要寫進事件欄與通知。不需要 LLM。"""
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import replay as rp
from engine.models import Commander


def _unit(uid, kind, group, name, x=5, y=5, dead=False):
    return {"id": uid, "kind": kind, "group": group, "name": name, "pos": [x, y, 0], "from": [x, y, 0], "path": [],
            "dead": dead, "fired": False, "weapon": "rifle"}


def _frame(t, ev, spotted=()):
    units = [_unit("s:a:K", "s", "a", "K-07"), _unit("s:a:R", "s", "a", "R-12"), _unit("s:b:V", "s", "b", "V-21"),
             _unit("e:g1:0", "e", "g1", "敵0", 9, 9), _unit("e:g1:1", "e", "g1", "敵1", 9, 10)]
    return {"t": t, "world_tick": t, "ts0": t * 0.5, "ts1": t * 0.5 + 0.5, "step_seconds": 0.5, "units": units,
            "shots": [], "blasts": [], "smokes": [], "supports": [], "intents": {}, "events": [],
            "intel": {"blue": {"spotted": list(spotted), "last": {}}}, "ev": ev}


def _ev(seq, kind, actor=None, target=None, **data):
    return {"seq": seq, "t": 0, "ts": seq * 1.0, "phase": "x", "sub": 0, "type": kind, "actor": actor, "target": target,
            "data": data}


def world():
    return SimpleNamespace(squads={"a": SimpleNamespace(commander="c1"), "b": SimpleNamespace(commander="c2")})


def test_summary_text():
    """我方倒地、陣亡、止血照重要性列；只算看得到的敵兵倒地；看不到的射手；時間；別的指揮官的小隊算友軍、不列他的倒地。"""
    frames = [
        _frame(0, [_ev(1, "down", "e:g1:0", "s:a:K"), _ev(2, "down", "s:a:R", "e:g1:0")], spotted=["e:g1:0"]),
        _frame(1, [_ev(3, "aid", "s:a:R", "s:a:K"), _ev(4, "down", "s:a:R", "e:g1:1"), _ev(5, "blast", "s:a:R", item="grenades")]),
        _frame(2, [_ev(6, "down", None, "s:b:V")]),
        _frame(130, []),
    ]
    data = {"cell": "C3", "frames": frames}
    t = rp.aar_text(data, "c1", world())
    assert t.startswith("西南排水渠（A1）交戰結束（1:05）"), t
    assert "我方倒地 1、陣亡 0" in t and "擊倒敵兵 1" in t and "手榴彈 1" in t, t
    assert "K-07 倒地（看不到的射手打中）" not in t and "K-07 倒地（敵兵打中）" in t, t
    assert t.index("K-07 倒地") < t.index("R-12 替 K-07 止血"), t
    assert "V-21" not in t, "友軍的倒地不列在我方"
    t2 = rp.aar_text(data, "c2", world())
    assert "我方倒地 1" in t2 and "V-21 倒地（看不到的射手打中）" in t2, t2
    assert rp.aar_text({"cell": "C3", "frames": []}, "c1", world()) == ""


def test_saved_replay_goes_to_events():
    """回放存好 → 相關指揮官各自收到通知（帶摘要），事件欄多一行 🎞 摘要，副官讀的情報紀錄也有。"""
    from engine.sim import Simulation
    root = tempfile.mkdtemp()
    try:
        store = rp.ReplayStore(root)
        frames = [_frame(0, [_ev(1, "down", "e:g1:0", "s:a:K")], spotted=["e:g1:0"]), _frame(20, [])]
        entry = {"id": "x1", "cell": "C3", "commanders": ["c1"], "start_tick": 0, "end_tick": 20, "frames": 2,
                 "squads": ["a"], "zone": True}
        store.index.append(entry)
        import json
        with open(os.path.join(root, "x1.json"), "w", encoding="utf8") as f:
            json.dump({"id": "x1", "cell": "C3", "frames": frames}, f, ensure_ascii=False)
        s = Simulation.__new__(Simulation)
        c = Commander(id="c1", name="甲")
        s.world = SimpleNamespace(tick=20, squads=world().squads, commanders={"c1": c})
        s.replays, s.outbox, s.dirty = store, [], set()
        s._replay_saved(entry)
        assert s.outbox and s.outbox[0]["type"] == "replay" and "交戰結束" in s.outbox[0]["aar"], s.outbox
        assert any(e.startswith("T20 🎞 西南排水渠（A1）交戰結束") for e in c.events), c.events
        assert any("戰後摘要：" in x for x in c.intel["log"]), "副官讀的情報紀錄也有"
        assert "c1" in s.dirty
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("OK", k)
    print("aar_test OK")


if __name__ == "__main__":
    main()
