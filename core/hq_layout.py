from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import discord

from engine.models import Commander

log = logging.getLogger(__name__)

BuildPanel = Callable[[Commander], tuple[discord.Embed, discord.ui.View]]
BuildBoard = Callable[[Commander], discord.Embed]


class HQLayout:
    """維持 #指揮所 的訊息順序：任務看板 → 副官面板 → 未到期的短通知。"""

    def __init__(self, build_board: BuildBoard, build_panel: BuildPanel, save: Callable[[], None],
                 notice_seconds: float = 60, debounce: float = 5, clock: Callable[[], float] = time.time):
        self.build_board = build_board
        self.build_panel = build_panel
        self.save = save
        self.notice_seconds = notice_seconds
        self.debounce = debounce
        self.clock = clock
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_repost: dict[str, float] = {}

    def lock(self, cid: str) -> asyncio.Lock:
        return self.locks.setdefault(cid, asyncio.Lock())

    @staticmethod
    async def _fetch(channel, message_id: int | None):
        if not message_id:
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            return None

    @staticmethod
    async def _delete(channel, message_id: int):
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.exception("刪除訊息失敗 %s", message_id)

    async def _send_panel(self, channel, c: Commander):
        embed, view = self.build_panel(c)
        message = await channel.send(embed=embed, view=view)
        c.panel_message = message.id

    async def _resend_notices(self, channel, c: Commander):
        kept = []
        for n in c.pending_notices:
            await self._delete(channel, n["id"])
            if n["delete_at"] > self.clock():
                message = await channel.send(n["text"])
                kept.append({**n, "id": message.id})
        c.pending_notices = kept

    async def _repost_tail(self, channel, c: Commander):
        if c.panel_message:
            await self._delete(channel, c.panel_message)
        await self._send_panel(channel, c)
        await self._resend_notices(channel, c)
        self.last_repost[c.id] = self.clock()

    async def update_board(self, channel, c: Commander):
        async with self.lock(c.id):
            embed = self.build_board(c)
            board = await self._fetch(channel, c.board_message)
            if board:
                await board.edit(embed=embed)
                return
            message = await channel.send(embed=embed)
            c.board_message = message.id
            await self._repost_tail(channel, c)
            self.save()

    async def update_panel(self, channel, c: Commander):
        async with self.lock(c.id):
            if not c.board_message or not await self._fetch(channel, c.board_message):
                message = await channel.send(embed=self.build_board(c))
                c.board_message = message.id
                await self._repost_tail(channel, c)
                self.save()
                return
            panel = await self._fetch(channel, c.panel_message)
            embed, view = self.build_panel(c)
            if panel:
                await panel.edit(embed=embed, view=view)
                return
            await self._repost_tail(channel, c)
            self.save()

    async def post_notice(self, channel, c: Commander, text: str):
        async with self.lock(c.id):
            message = await channel.send(text)
            c.pending_notices.append({"id": message.id, "text": text,
                                      "delete_at": self.clock() + self.notice_seconds})
            self.save()

    async def sweep(self, channel, c: Commander):
        now = self.clock()
        due = [n for n in c.pending_notices if n["delete_at"] <= now]
        if not due:
            return
        async with self.lock(c.id):
            for n in due:
                await self._delete(channel, n["id"])
            ids = {n["id"] for n in due}
            c.pending_notices = [n for n in c.pending_notices if n["id"] not in ids]
            self.save()

    async def verify(self, channel, c: Commander, history_limit: int = 50):
        """啟動時檢查：面板之後只允許未到期短通知，否則重排。"""
        await self.sweep(channel, c)
        async with self.lock(c.id):
            if not c.board_message or not await self._fetch(channel, c.board_message):
                return False
            if not await self._fetch(channel, c.panel_message):
                await self._repost_tail(channel, c)
                self.save()
                return True
            allowed = {n["id"] for n in c.pending_notices}
            stray = False
            async for message in channel.history(limit=history_limit, after=discord.Object(id=c.panel_message)):
                if message.id not in allowed:
                    stray = True
                    break
            if stray and self.clock() - self.last_repost.get(c.id, 0) >= self.debounce:
                await self._repost_tail(channel, c)
                self.save()
                return True
            return False
