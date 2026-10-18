WORLD_NOTE = (
    "近未來。城市已廢棄，沒有平民。前線由戰鬥用生化人與機器人作戰。"
    "敵方具備干擾、甚至偽造通訊的電子戰能力，這是前線常識。"
)

UNIT_TYPES = {
    "infantry": {
        "name": "步兵小隊",
        "capabilities": [
            "突擊步槍",
            "短距戰術無線電（只能與指揮官通訊）",
            "基本光學／熱感測",
            "急救模組（只能穩定傷勢，無法修復）",
        ],
        "lacks": [
            "重武器、爆裂物",
            "載具",
            "空中支援、火砲支援",
            "超出短距的通訊",
        ],
    },
}


def capability_text(card: dict | None = None, unit_type: str = "infantry") -> str:
    unit = UNIT_TYPES[unit_type]
    caps = list(unit["capabilities"]) + list((card or {}).get("extra_capabilities", []))
    return (
        f"小隊類型：{unit['name']}\n"
        f"具備：{'、'.join(caps)}\n"
        f"不具備：{'、'.join(unit['lacks'])}\n"
        f"世界觀：{WORLD_NOTE}"
    )
