import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord

from core.hq_layout import HQLayout
from engine.models import Commander


class FakeResponse:
    status = 404
    reason = "Not Found"


class FakeMessage:
    def __init__(self, channel, mid, kind, content=None):
        self.channel = channel
        self.id = mid
        self.kind = kind
        self.content = content

    async def edit(self, **kwargs):
        self.channel.edits += 1

    async def delete(self):
        self.channel.messages = [m for m in self.channel.messages if m.id != self.id]


class FakeChannel:
    def __init__(self):
        self.messages: list[FakeMessage] = []
        self.next_id = 100
        self.edits = 0
        self.sends = 0

    async def send(self, content=None, embed=None, view=None):
        self.next_id += 1
        self.sends += 1
        kind = "panel" if view is not None else ("board" if embed is not None else "notice")
        msg = FakeMessage(self, self.next_id, kind, content)
        self.messages.append(msg)
        return msg

    async def fetch_message(self, mid):
        for m in self.messages:
            if m.id == mid:
                return m
        raise discord.NotFound(FakeResponse(), "missing")

    def history(self, limit=50, after=None):
        items = [m for m in self.messages if m.id > after.id][:limit]

        async def gen():
            for m in items:
                yield m
        return gen()

    def order(self):
        return [m.kind for m in self.messages]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(clock):
    return HQLayout(
        build_board=lambda c: discord.Embed(title="board"),
        build_panel=lambda c: (discord.Embed(title="panel"), SimpleNamespace()),
        save=lambda: None,
        notice_seconds=60, debounce=5, clock=clock,
    )


async def main():
    clock = Clock()
    layout = make(clock)
    ch = FakeChannel()
    c = Commander(id="1", name="甲")

    await layout.update_panel(ch, c)
    assert ch.order() == ["board", "panel"], ch.order()

    await layout.post_notice(ch, c, "📜 新主線任務")
    await layout.post_notice(ch, c, "✅ 達成")
    assert ch.order() == ["board", "panel", "notice", "notice"], ch.order()

    sends = ch.sends
    await layout.update_board(ch, c)
    await layout.update_panel(ch, c)
    assert ch.sends == sends and ch.edits >= 2, "存在時只編輯"

    clock.t += 30
    await layout.post_notice(ch, c, "📜 新支線")
    clock.t += 31
    await layout.sweep(ch, c)
    assert ch.order() == ["board", "panel", "notice"], ch.order()
    assert [n["text"] for n in c.pending_notices] == ["📜 新支線"]

    board_msg = await ch.fetch_message(c.board_message)
    await board_msg.delete()
    await layout.update_board(ch, c)
    assert ch.order() == ["board", "panel", "notice"], ch.order()
    assert c.pending_notices[0]["text"] == "📜 新支線"
    assert ch.messages[-1].content == "📜 新支線", "通知重發且內容保留"

    panel_msg = await ch.fetch_message(c.panel_message)
    await panel_msg.delete()
    await layout.update_panel(ch, c)
    assert ch.order() == ["board", "panel", "notice"], ch.order()

    ch.next_id += 1
    ch.messages.append(FakeMessage(ch, ch.next_id, "stray"))
    clock.t += 10
    reposted = await layout.verify(ch, c)
    assert reposted and ch.order() == ["board", "stray", "panel", "notice"], ch.order()

    ch.next_id += 1
    ch.messages.append(FakeMessage(ch, ch.next_id, "stray"))
    reposted = await layout.verify(ch, c)
    assert not reposted, "5 秒防抖"

    c.pending_notices.append({"id": 99999, "text": "ghost", "delete_at": 0})
    await layout.sweep(ch, c)
    assert all(n["id"] != 99999 for n in c.pending_notices), "NotFound 也移除"

    clock.t += 120
    await layout.sweep(ch, c)
    assert not c.pending_notices and "notice" not in ch.order()

    d = Commander(**{**c.__dict__})
    assert d.board_message == c.board_message and d.pending_notices == c.pending_notices

    print("layout_test OK")


if __name__ == "__main__":
    asyncio.run(main())
