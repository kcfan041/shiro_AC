"""網頁版入口：python server.py → http://localhost:8080

設定：setting/web.json（可選）——host、port、gm（GM 暱稱清單）、starter_cards、max_commanders、tick_seconds 等。
存檔：data/web_world.json（與 Discord 版分開，兩者不要同時開同一份存檔）。
"""
import asyncio
import logging
import os

from aiohttp import web
from dotenv import load_dotenv

from lib.llm import LLMClient
from web.app import Game, load_config, make_app


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv()
    os.makedirs("log", exist_ok=True)
    logging.basicConfig(filename="log/web.log", level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    cfg = load_config()
    game = Game(cfg, LLMClient(max_per_minute=cfg["llm_max_per_minute"]))
    print(f"shiro_AC 網頁版：http://localhost:{cfg['port']}（GM：{', '.join(cfg['gm']) or '未設定'}）")
    web.run_app(make_app(game), host=cfg["host"], port=cfg["port"], print=None)


if __name__ == "__main__":
    main()
