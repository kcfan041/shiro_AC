"""Phase E 結構化事件測試（不需要 LLM）：
1. 結果不變：固定遭遇（scripts/encounter.py）逐步狀態摘要與記錄事件前的基準相同。
2. 不洩漏：一般玩家收到的事件不含看不到的敵人 ID 或精確位置；GM 拿完整資料。
3. 同時結算：同一批射擊屬於同一個 resolution group；命中／倒地帶有射擊的 action id。
4. 足以重建：固定遭遇的事件涵蓋射擊、命中、倒地、壓制、發現、行動理由；序號遞增。

用法：python scripts/event_test.py [--digest]（--digest 只印目前的摘要；改了戰鬥規則才需要更新 GOLDEN）
"""
import asyncio
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.replay import for_viewer
from scripts import encounter

GOLDEN = "e34e12f686a0"
# 記錄事件前（c4ec33e）＝5fe1d25e…；架槍避開隊友擋線（改變 AI 行為）後＝942d92fed128；
# 地圖加上自然地形（engine/tactical/relief.py）後＝e54e9f74a4fa；面向細分（engine/tactical/facing.py）後更新為上面的值。
# 只有刻意改戰鬥規則／AI 時才更新；純記錄、介面改動必須保持相同。


def state_digest(battle) -> str:
    rows = []
    for uid, u in sorted(battle.units.items()):
        rows.append((uid, tuple(u.pos), u.posture, tuple(u.facing), u.down, u.dead, u.exited, u.surrendered,
                     round(u.suppression, 3), u.ammo, u.grenades, u.smokes, u.flashes, u.torso_hits,
                     tuple(sorted((k, bool(v)) for k, v in u.damage.items())), bool(u.braced), u.stabilized))
    intel = {s: (sorted(i.spotted.items()), sorted((k, tuple(v[0]), v[1]) for k, v in i.last_known.items()))
             for s, i in sorted(battle.intel.items())}
    return json.dumps([battle.tick, rows, intel, sorted(battle.morale.items())], default=str)


async def run_digest(collect=None) -> tuple[str, dict]:
    h = hashlib.sha256()

    def on_step(sim, z):
        h.update(state_digest(z.battle).encode())
        if collect is not None:
            collect(sim, z)
    res = await encounter.run(on_step=on_step, replays=collect is not None)
    return h.hexdigest(), res


def synthetic_filter():
    """合成事件：看不到的射手 e1 打中我方 s1、壓制 s2；e2 被我方看到；e3 聽到槍聲；e4 完全沒有情報。"""
    from engine.replay import filter_events
    grp = "t5-fire"
    evs = [
        {"seq": 1, "t": 5, "phase": "fire", "sub": 0, "type": "shot", "actor": "e1", "target": "s1", "aid": "a5.1",
         "grp": grp, "data": {"mode": "aimed", "ow": False, "rounds": 1, "weapon": "dmr", "frm": [10.0, 10.0],
                              "to": [[20.0, 10.0]], "impact": ["body"]}},
        {"seq": 2, "t": 5, "phase": "fire", "sub": 0, "type": "shot", "actor": "s2", "target": "e2", "aid": "a5.2",
         "grp": grp, "data": {"mode": "burst", "ow": False, "rounds": 3, "weapon": "rifle", "frm": [20.0, 12.0],
                              "to": [[25.0, 12.0]], "impact": ["cover"]}},
        {"seq": 3, "t": 5, "phase": "fire", "sub": 0, "type": "hit", "actor": "e1", "target": "s1", "aid": "a5.1",
         "grp": grp, "data": {"part": "torso"}},
        {"seq": 4, "t": 5, "phase": "fire", "sub": 0, "type": "hit", "actor": "s2", "target": "e4", "aid": "a5.2",
         "grp": grp, "data": {"part": "torso"}},
        {"seq": 5, "t": 5, "phase": "fire", "sub": 0, "type": "suppress", "actor": "e1", "target": "s2", "grp": grp,
         "data": {"amount": 20, "level": "light", "was": "none", "sources": ["e1"]}},
        {"seq": 6, "t": 5, "phase": "fire", "sub": 0, "type": "hear", "actor": None, "target": "e3",
         "data": {"side": "blue|x", "how": "gunfire", "at": [30, 30], "err": 4.0}},
        {"seq": 7, "t": 5, "phase": "fire", "sub": 0, "type": "hear", "actor": None, "target": "s1",
         "data": {"side": "red", "how": "gunfire", "at": [20, 10], "err": 2.0}},
        {"seq": 8, "t": 5, "phase": "end", "sub": 0, "type": "brace", "actor": "e4", "data": {"state": "ready"}},
        {"seq": 9, "t": 5, "phase": "support", "sub": 0, "type": "support", "actor": None,
         "data": {"what": "drone", "commander": "2", "state": "active"}},
        {"seq": 10, "t": 5, "phase": "support", "sub": 0, "type": "support", "actor": None,
         "data": {"what": "smoke", "commander": "2", "state": "done"}},
    ]
    out = filter_events(evs, friendly={"s1", "s2"}, spotted={"e2"}, cid="1", tracked={"e3"})
    text = json.dumps(out)
    check('"e1"' not in text and '"e4"' not in text, "合成：看不到的 e1、e4 不出現在玩家事件")
    s = next(e for e in out if e["seq"] == 1)
    check(s["actor"] is None and s["data"]["unseen"] and s["data"]["weapon"] == "dmr"
          and abs(s["data"]["frm"][0] - 16.6) < 0.01, "合成：看不到的射手只留彈道後段、沒有射手，但聽得出槍種")
    check(any(e["seq"] == 3 and e["actor"] is None for e in out), "合成：我方中彈事件保留、射手 ID 拿掉")
    check(not any(e["seq"] == 4 for e in out), "合成：打中看不到的敵人不送")
    check(next(e for e in out if e["seq"] == 5)["data"]["sources"] == [], "合成：壓制來源只留看得到的")
    check(any(e["seq"] == 6 and e["target"] == "e3" and "side" not in e["data"] for e in out),
          "合成：我方聽到槍聲保留（已有 ghost 的 ID）")
    check(not any(e["seq"] == 7 for e in out), "合成：敵方的情報事件不送")
    check(not any(e["seq"] == 8 for e in out), "合成：看不到的敵人架槍不送")
    check([e["seq"] for e in out if e["type"] == "support"] == [10], "合成：別人的無人機不送、煙幕都看得到")
    hidden_hit = {"seq": 11, "t": 5, "phase": "fire", "sub": 0, "type": "shot", "actor": "s2", "target": "e4", "aid": "a5.3",
                  "grp": grp, "data": {"mode": "aimed", "ow": False, "rounds": 2, "weapon": "rifle", "frm": [20.0, 12.0],
                                       "to": [[28.0, 12.0], [28.0, 13.0]], "impact": ["body", "cover"]}}
    o = filter_events([hidden_hit], friendly={"s1", "s2"}, spotted={"e2"}, cid="1")[0]
    check(o["target"] is None and o["data"]["impact"] == ["unknown", "cover"], "合成：打中看不到的敵人，彈著只說「不明」（不洩漏位置）")
    from engine.replay import for_viewer, impact_material
    from engine.tactical.geometry import Box
    check([impact_material(s) for s in ({"kind": "cover", "ref": Box((0, 0, 0), (1, 1, 1), "solid", "lowwall")},
                                        {"kind": "cover", "ref": Box((0, 0, 0), (1, 1, 1), "solid", "window")},
                                        {"kind": "ground"}, {"kind": "body", "ref": ("e1", "torso")}, {"kind": "none"})]
          == ["low", "wall", "ground", "body", "air"], "彈著材質：矮牆、窗台算牆、地面、人、沒打到東西")
    shot = lambda hit, mat: {"shooter": "s1", "from": [0, 0], "to": [5, 0], "kind": "body" if hit else "cover", "hit": hit,
                             "aid": "a", "ow": False, "mode": "aimed", "ts": 1.0, "mat": mat}
    frame = {"t": 1, "world_tick": 1, "ts0": 0.5, "ts1": 1.0, "step_seconds": 0.5, "blasts": [], "smokes": [], "events": [],
             "ev": [], "supports": [], "intel": {"blue": {"spotted": ["e2"], "seen": ["e2"], "last": {}}},
             "units": [{"id": "s1", "kind": "g", "group": "r", "side": "blue", "dead": False},
                       {"id": "e2", "kind": None, "group": None, "side": "red", "dead": False},
                       {"id": "e4", "kind": None, "group": None, "side": "red", "dead": False, "weapon": "lmg"}],
             "shots": [shot("e4", "body"), shot("e2", "body"), shot(None, "low"), dict(shot(None, "wall"), shooter="e4")]}
    pv = for_viewer(frame, "1", world=None)
    check(pv["shots"][0]["hit"] is None and pv["shots"][0]["mat"] == "unk" and pv["shots"][0]["kind"] == "unknown",
          "影格：打中看不到的敵人，命中對象與材質都拿掉")
    check(pv["shots"][1]["hit"] == "e2" and pv["shots"][1]["mat"] == "body" and pv["shots"][2]["mat"] == "low",
          "影格：打中看得到的人、打在矮牆上照送")
    un = pv["shots"][3]
    check(un["shooter"] is None and un["unseen"] and un["wpn"] == "lmg" and un["from"][0] > 3,
          "影格：看不到的射手沒有 ID、只留彈道後段，但帶槍種（聽得出是機槍）")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok  {msg}")


async def main():
    if "--digest" in sys.argv:
        d, res = await run_digest()
        print(d)
        print(json.dumps(res, ensure_ascii=False))
        return

    frames = []      # (真相影格, 玩家視圖, GM 視圖, 這步之後的真相位置)

    def collect(sim, z):
        f = z.last_frame
        if f is None or not f.get("ev"):
            return
        z.recorder.frames = z.recorder.frames[-50:]
        truth = {uid: tuple(u.pos[:2]) for uid, u in z.battle.units.items()}
        frames.append((f, for_viewer(f, "1", sim.world), for_viewer(f, "1", sim.world, gm=True), truth,
                       {uid for uid, o in z.owner.items() if o[0] == "e"}))
    digest, res = await run_digest(collect)

    print("1. 結果不變")
    check(digest.startswith(GOLDEN), f"固定遭遇逐步狀態摘要與基準相同（{digest[:12]}）")

    allev = [e for f, *_ in frames for e in f["ev"]]
    types = {e["type"] for e in allev}
    print("2. 事件涵蓋與順序")
    for k in ("shot", "hit", "down", "suppress", "spot", "hear", "throw", "support", "brace"):
        check(k in types, f"有 {k} 事件")
    seqs = [e["seq"] for e in allev]
    check(seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "序號在整場內遞增且不重複")
    check(all(e["data"].get("ow") for e in allev if e["type"] == "shot" and e["phase"] == "overwatch"),
          "警戒射擊有標記 ow")
    check(all(e["phase"] in ("pre", "move", "overwatch", "post_move", "fire", "throw", "care", "end", "support")
              for e in allev), "每個事件都有階段")
    whys = [u.get("why") for f, *_ in frames for u in f["units"] if u.get("why")]
    check(len(whys) > 20, f"行動理由 {len(whys)} 筆（例：{sorted(set(whys))[:5]}）")

    print("3. 同時結算與因果")
    shot_aid = {e["aid"]: e for e in allev if e["type"] == "shot"}
    for e in allev:
        if e["type"] in ("hit", "down") and e.get("actor") and e.get("aid"):
            s = shot_aid.get(e["aid"])
            if s is not None:
                assert s["actor"] == e["actor"], e
                assert s["grp"] == e["grp"], e
    linked = [e for e in allev if e["type"] in ("hit", "down") and e.get("aid") in shot_aid]
    check(linked, f"命中／倒地串到射擊的 action id（{len(linked)} 筆）")
    for f, *_ in frames:
        groups = {}
        for e in f["ev"]:
            if e["type"] == "shot":
                groups.setdefault((e["phase"], e["ts"], e["data"].get("ow")), set()).add(e["grp"])
        for k, g in groups.items():
            if k[0] == "fire":
                assert len(g) == 1, (k, g)
    check(True, "同一時刻的一般射擊全部屬於同一個 resolution group")
    fire_grps = [e["grp"] for e in allev if e["type"] == "shot" and e["phase"] == "fire"]
    multi = [g for g in set(fire_grps) if fire_grps.count(g) > 1]
    check(multi, f"有多人同時射擊的群組（{len(multi)} 組）")
    ow = [e for e in allev if e["type"] == "shot" and e["phase"] == "overwatch"]
    check(all("-ow" in e["grp"] for e in ow), f"警戒射擊群組獨立（{len(ow)} 次）")
    # 同一次射擊的每一發：影格子彈帶第幾發（r，0 不送），畫面依射速錯開；時間仍是同一個（規則上同時結算）
    multi_round = 0
    for f, *_ in frames:
        by_aid = {}
        for x in f["shots"]:
            by_aid.setdefault(x["aid"], []).append(x)
        for e in f["ev"]:
            if e["type"] != "shot" or e["aid"] not in by_aid:
                continue
            rs = sorted({x.get("r", 0) for x in by_aid[e["aid"]]})
            assert rs == list(range(e["data"]["rounds"])), (e["data"]["rounds"], rs)
            assert len({x["ts"] for x in by_aid[e["aid"]]}) == 1, by_aid[e["aid"]]
            multi_round += e["data"]["rounds"] > 1
    check(multi_round, f"多發的射擊每一發有序號 0～n−1、時間相同（{multi_round} 次）")

    print("4. 迷霧過濾")
    leaks = 0
    masked = 0
    for f, pv, gm, truth, enemies in frames:
        seen = set(f["intel"].get("blue", {}).get("seen", [])) | set(f["intel"].get("blue", {}).get("spotted", []))
        tracked = set(f["intel"].get("blue", {}).get("last", {}))
        hidden = enemies - seen
        text = json.dumps(pv["ev"], ensure_ascii=False)
        for uid in hidden - tracked:
            if f'"{uid}"' in text:
                leaks += 1
        for e in pv["ev"]:
            for k in ("actor", "target"):
                if e.get(k) in hidden and not (e["type"] in ("hear", "lost") and k == "target"):
                    leaks += 1
            if e["type"] == "shot" and e["data"].get("unseen"):
                masked += 1
                assert e["actor"] is None and e["data"].get("weapon"), e
            if e["type"] == "hear":
                at = e["data"].get("at")
                tid = e.get("target")
                if tid in hidden and at is not None and tuple(at) == truth.get(tid) and e["data"].get("err", 0) > 2:
                    pass
            if e["type"] in ("hit", "down", "kia") and e["target"] in hidden:
                leaks += 1
        for u in pv["units"]:
            if u["side"] == "enemy":
                assert "why" not in u and "supp" not in u, u
        assert len(gm["ev"]) == len(f["ev"]), "GM 拿完整事件"
    check(leaks == 0, "玩家視圖不含看不到的敵人 ID（行動者／目標／命中）")
    print(f"  --  固定遭遇中看不到的射手 {masked} 次、警戒射擊 {len(ow)} 次（場景特性，另以合成資料測）")
    synthetic_filter()
    pv_types = {e["type"] for _, pv, *_ in frames for e in pv["ev"]}
    check({"shot", "hit", "suppress"} <= pv_types, f"玩家仍看得到主要戰鬥事件（{sorted(pv_types)}）")

    print("5. 足以重建")
    downs = [e for e in allev if e["type"] == "down"]
    check(len(downs) >= 1, f"固定遭遇倒地 {len(downs)} 次，每次都有射手與 action id"
          if all(e.get("actor") and e.get("aid") for e in downs) else "倒地事件缺射手或 action id")
    first_hit = next((e for e in allev if e["type"] == "hit"), None)
    check(first_hit is not None and first_hit.get("aid") in shot_aid,
          f"第一次命中可追溯到射擊（{first_hit and shot_aid.get(first_hit.get('aid'), {}).get('data', {}).get('mode')}）")
    print("全部通過")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
