from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.classes import CE
from core.hq_layout import HQLayout
from engine import enemy_ai, obedience, scenario
from engine.tactical import worldmap
from engine.models import Commander
from engine.sim import Simulation
from lib.llm import LLMClient

log = logging.getLogger(__name__)

CONFIG_DEFAULTS = {
    "tick_seconds": 10,
    "llm_max_per_minute": 60,
    "adjutant_min_interval": 30,
    "lobby_category": "AC 大廳",
    "lobby_channel": "報到處",
    "spectator_role": "AC 觀戰",
    "commander_category_prefix": "指揮官",
    "hq_channel": "指揮所",
    "ephemeral_short": 8,
    "ephemeral_long": 30,
    "mission_notice_seconds": 60,
}
MAX_SUGGESTIONS = 4
LEGACY_CATEGORIES = ("AC 指揮所", "AC 通訊頻道")


async def _delete_later(message: discord.WebhookMessage, delay: float):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except discord.HTTPException:
        pass


async def notify(i: discord.Interaction, text: str | None = None, *, seconds: float | None = 8, **kwargs):
    if not i.response.is_done():
        if seconds:
            kwargs["delete_after"] = seconds
        await i.response.send_message(text, ephemeral=True, **kwargs)
        return
    message = await i.followup.send(text, ephemeral=True, wait=True, **kwargs)
    if seconds:
        asyncio.create_task(_delete_later(message, seconds))


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf8") as f:
        return json.load(f)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(load_json("./setting/ac_game.json", {}) or {})
    return cfg


def load_card(squad_id: str) -> dict | None:
    for base in ("./setting/squads", "./setting example/squads"):
        card = load_json(os.path.join(base, f"{squad_id}.json"))
        if card:
            return card
    return None


def save_card(card: dict):
    os.makedirs("./setting/squads", exist_ok=True)
    path = os.path.join("./setting/squads", f"{card['id']}.json")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf8") as f:
        json.dump(card, f, ensure_ascii=False, indent=4)


def is_gm(user_id: int) -> bool:
    setting = load_json("./setting/setting.json", {}) or {}
    return user_id == setting.get("owner")


VIEW_ONLY = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False,
                                        send_messages_in_threads=False, create_public_threads=False,
                                        create_private_threads=False, add_reactions=False)
OWNER_PERMS = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True,
                                          add_reactions=True)
BOT_PERMS = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True,
                                        manage_webhooks=True, manage_messages=True, embed_links=True)


# ---------------------------------------------------------------------- views


class LobbyView(discord.ui.View):
    def __init__(self, cog: "Squad"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="觀戰", style=discord.ButtonStyle.secondary, emoji="👁", custom_id="ac:spectate")
    async def spectate(self, i: discord.Interaction, button: discord.ui.Button):
        role = await self.cog.ensure_spectator_role(i.guild)
        if role in i.user.roles:
            await i.user.remove_roles(role, reason="AC 取消觀戰")
            await notify(i, "已取消觀戰。", seconds=self.cog.short)
        else:
            await i.user.add_roles(role, reason="AC 觀戰")
            await notify(i, "已取得觀戰身分，可檢視所有指揮官的頻道（唯讀）。再按一次可取消。", seconds=self.cog.short)


class AdjutantView(discord.ui.View):
    def __init__(self, cog: "Squad", suggestions: list[dict] | None = None):
        super().__init__(timeout=None)
        self.cog = cog
        if suggestions is None:
            suggestions = [{"label": f"建議 {n + 1}"} for n in range(MAX_SUGGESTIONS)]
        for idx, s in enumerate(suggestions[:MAX_SUGGESTIONS]):
            button = discord.ui.Button(label=s["label"][:80], style=discord.ButtonStyle.primary,
                                       custom_id=f"ac:sug:{idx}", row=0 if idx < 2 else 1)
            button.callback = self._on_suggestion(idx)
            self.add_item(button)
        refresh = discord.ui.Button(label="更新現況", style=discord.ButtonStyle.secondary,
                                    custom_id="ac:refresh", emoji="🔄", row=2)
        refresh.callback = self._on_refresh
        self.add_item(refresh)
        share = discord.ui.Button(label="情報共享", style=discord.ButtonStyle.secondary,
                                  custom_id="ac:share", emoji="🔗", row=2)
        share.callback = self._on_share
        self.add_item(share)
        supply = discord.ui.Button(label="請求補給", style=discord.ButtonStyle.secondary,
                                   custom_id="ac:supply", emoji="📦", row=2)
        supply.callback = self._on_supply
        self.add_item(supply)

    async def _on_supply(self, i: discord.Interaction):
        commander = await self.cog.commander_of_panel(i)
        if not commander:
            return
        squads = [s for s in self.cog.sim.world.squads_of(commander.id) if s.status == "active"]
        if not squads:
            await notify(i, "目前沒有可補給的小隊。", seconds=self.cog.short)
            return
        if len(squads) == 1:
            await i.response.defer(ephemeral=True, thinking=True)
            ok, msg = await self.cog.sim.request_supply(squads[0].id)
            await self.cog.flush()
            await notify(i, ("📦 " if ok else "⛔ ") + msg, seconds=self.cog.short)
            return
        view = SupplyView(self.cog, squads)
        await i.response.send_message("選擇要空投補給的小隊：", view=view, ephemeral=True, delete_after=60)

    def _on_suggestion(self, idx: int):
        async def callback(i: discord.Interaction):
            commander = await self.cog.commander_of_panel(i)
            if commander:
                await self.cog.use_suggestion(i, commander, idx)
        return callback

    async def _on_refresh(self, i: discord.Interaction):
        commander = await self.cog.commander_of_panel(i)
        if not commander:
            return
        await i.response.defer(ephemeral=True, thinking=True)
        await self.cog.update_panel(commander.id, force=True)
        await notify(i, "副官已更新現況。", seconds=self.cog.short)

    async def _on_share(self, i: discord.Interaction):
        commander = await self.cog.commander_of_panel(i)
        if not commander:
            return
        view = ShareView(self.cog, commander.id)
        await i.response.send_message(embed=self.cog.share_embed(commander, i.guild), view=view, ephemeral=True)
        view.interaction = i


class SupplyView(discord.ui.View):
    def __init__(self, cog: "Squad", squads: list):
        super().__init__(timeout=60)
        self.cog = cog
        select = discord.ui.Select(placeholder="選擇小隊", options=[
            discord.SelectOption(label=f"{s.callsign}（{s.pos}）", value=s.id,
                                 description=f"彈藥 {s.ammo}%｜{len(s.alive_members())}/{len(s.members)} 人")
            for s in squads[:25]
        ])
        select.callback = self._on_select
        self.select = select
        self.add_item(select)

    async def _on_select(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        ok, msg = await self.cog.sim.request_supply(self.select.values[0])
        await self.cog.flush()
        await notify(i, ("📦 " if ok else "⛔ ") + msg, seconds=self.cog.short)


class ShareView(discord.ui.View):
    def __init__(self, cog: "Squad", commander_id: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.commander_id = commander_id
        self.interaction: discord.Interaction | None = None

    async def on_timeout(self):
        if self.interaction:
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                pass

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="選擇要開放檢視的人（唯讀）", min_values=1, max_values=10)
    async def add(self, i: discord.Interaction, select: discord.ui.UserSelect):
        await i.response.defer(ephemeral=True)
        await self.cog.set_viewers(i.guild, self.commander_id, [u for u in select.values], grant=True)
        c = self.cog.sim.world.commanders[self.commander_id]
        await i.edit_original_response(embed=self.cog.share_embed(c, i.guild), view=self)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="選擇要收回權限的人", min_values=1, max_values=10)
    async def remove(self, i: discord.Interaction, select: discord.ui.UserSelect):
        await i.response.defer(ephemeral=True)
        await self.cog.set_viewers(i.guild, self.commander_id, [u for u in select.values], grant=False)
        c = self.cog.sim.world.commanders[self.commander_id]
        await i.edit_original_response(embed=self.cog.share_embed(c, i.guild), view=self)


class WipeView(discord.ui.View):
    def __init__(self, cog: "Squad", user_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.interaction: discord.Interaction | None = None

    async def interaction_check(self, i: discord.Interaction) -> bool:
        return i.user.id == self.user_id

    async def on_timeout(self):
        if self.interaction:
            try:
                await self.interaction.delete_original_response()
            except discord.HTTPException:
                pass

    @discord.ui.button(label="確認全部清除", style=discord.ButtonStyle.danger, emoji="🧨")
    async def confirm(self, i: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await i.response.edit_message(content="🧨 清除中……", embed=None, view=None)
        report = await self.cog.wipe_all(i.guild)
        await i.edit_original_response(content=report[:1990])

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, i: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await i.response.edit_message(content="已取消。", embed=None, view=None, delete_after=self.cog.short)


# ---------------------------------------------------------------------- cog


class Squad(CE):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.cfg = load_config()
        self.llm = LLMClient(max_per_minute=self.cfg["llm_max_per_minute"])
        self.sim = Simulation(self.llm, tick_seconds=self.cfg["tick_seconds"])
        self.flush_lock = asyncio.Lock()
        self.panel_locks: dict[str, asyncio.Lock] = {}
        self.last_panel_refresh: dict[str, float] = {}
        self.layout = HQLayout(self.build_board_embed, self.build_panel, self.sim.save,
                               notice_seconds=self.cfg["mission_notice_seconds"])
        self.verified = False
        self.board_sig: dict[str, tuple] = {}
        filled = self.sim.backfill_beliefs(load_card)
        if filled:
            log.info("已從角色卡補上信念：%s", filled)
        self.ticker.change_interval(seconds=self.cfg["tick_seconds"])

    async def cog_load(self):
        self.bot.add_view(AdjutantView(self))
        self.bot.add_view(LobbyView(self))
        if self.sim.world.running:
            self.ticker.start()

    async def cog_unload(self):
        self.ticker.cancel()
        await self.llm.close()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} 已載入")
        if self.verified:
            return
        self.verified = True
        for c in list(self.sim.world.commanders.values()):
            channel = self.bot.get_channel(c.hq_channel or 0)
            if channel is None:
                continue
            try:
                if not await self.layout.verify(channel, c):
                    await self.layout.update_board(channel, c)
            except discord.HTTPException:
                log.exception("指揮所檢查失敗 %s", c.id)

    # ------------------------------------------------------------------ helpers

    @property
    def short(self) -> float:
        return self.cfg["ephemeral_short"]

    @property
    def long(self) -> float:
        return self.cfg["ephemeral_long"]

    @property
    def dstate(self) -> dict:
        return self.sim.world.discord

    def squad_of_channel(self, channel_id: int) -> str | None:
        for sid, info in self.dstate["squads"].items():
            if info.get("channel") == channel_id:
                return sid
        return None

    def commander_of_hq(self, channel_id: int) -> Commander | None:
        for c in self.sim.world.commanders.values():
            if c.hq_channel == channel_id:
                return c
        return None

    async def commander_of_panel(self, i: discord.Interaction) -> Commander | None:
        c = self.commander_of_hq(i.channel_id)
        if c is None:
            await notify(i, "這個面板已失效。", seconds=self.short)
            return None
        if str(i.user.id) != c.id:
            await notify(i, "你不是這個區域的指揮官。", seconds=self.short)
            return None
        return c

    async def gm_check(self, i: discord.Interaction) -> bool:
        if is_gm(i.user.id):
            return True
        await notify(i, "你沒有權限執行此命令。", seconds=self.short)
        return False

    async def ensure_category(self, guild: discord.Guild, name: str,
                              overwrites: dict | None = None) -> discord.CategoryChannel:
        category = discord.utils.get(guild.categories, name=name)
        return category or await guild.create_category(name, overwrites=overwrites or {})

    async def ensure_spectator_role(self, guild: discord.Guild) -> discord.Role:
        role = guild.get_role(self.dstate.get("spectator_role") or 0)
        if role is None:
            role = discord.utils.get(guild.roles, name=self.cfg["spectator_role"])
        if role is None:
            role = await guild.create_role(name=self.cfg["spectator_role"], reason="AC 觀戰身分組")
        if self.dstate.get("spectator_role") != role.id:
            self.dstate["spectator_role"] = role.id
            self.sim.save()
        return role

    def commander_overwrites(self, guild: discord.Guild, c: Commander, spectator: discord.Role) -> dict:
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: BOT_PERMS,
            spectator: VIEW_ONLY,
        }
        for vid in c.viewers:
            member = guild.get_member(vid)
            if member:
                ow[member] = VIEW_ONLY
        owner = guild.get_member(int(c.id))
        if owner:
            ow[owner] = OWNER_PERMS
        return ow

    def hq_overwrites(self, guild: discord.Guild, c: Commander, spectator: discord.Role) -> dict:
        ow = self.commander_overwrites(guild, c, spectator)
        owner = guild.get_member(int(c.id))
        if owner:
            ow[owner] = VIEW_ONLY
        return ow

    async def apply_commander_perms(self, guild: discord.Guild, c: Commander):
        category = guild.get_channel(c.category or 0)
        if category is None:
            return
        spectator = await self.ensure_spectator_role(guild)
        await category.edit(overwrites=self.commander_overwrites(guild, c, spectator))
        for channel in category.channels:
            if channel.id == c.hq_channel:
                await channel.edit(overwrites=self.hq_overwrites(guild, c, spectator))
            elif not channel.permissions_synced:
                await channel.edit(sync_permissions=True)

    async def ensure_commander_space(self, guild: discord.Guild, member: discord.Member) -> Commander:
        c = self.sim.ensure_commander(str(member.id), member.display_name)
        spectator = await self.ensure_spectator_role(guild)
        category = guild.get_channel(c.category or 0)
        if category is None:
            category = await guild.create_category(
                f"{self.cfg['commander_category_prefix']} {member.display_name}",
                overwrites=self.commander_overwrites(guild, c, spectator),
            )
            c.category = category.id
        hq = guild.get_channel(c.hq_channel or 0)
        if hq is None:
            hq = await guild.create_text_channel(self.cfg["hq_channel"], category=category,
                                                 overwrites=self.hq_overwrites(guild, c, spectator))
            c.hq_channel = hq.id
            c.panel_message = None
            c.board_message = None
            c.pending_notices = []
        else:
            await hq.edit(overwrites=self.hq_overwrites(guild, c, spectator))
        self.sim.save()
        return c

    async def ensure_lobby(self, guild: discord.Guild) -> discord.TextChannel:
        lobby = guild.get_channel(self.dstate.get("lobby_channel") or 0)
        if lobby is None:
            category = await self.ensure_category(guild, self.cfg["lobby_category"])
            lobby = await guild.create_text_channel(
                self.cfg["lobby_channel"], category=category,
                overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                            guild.me: BOT_PERMS},
            )
            self.dstate["lobby_channel"] = lobby.id
            self.dstate["lobby_message"] = None
        embed = discord.Embed(
            title="Area Commander｜報到處",
            description=("宏大戰役進行中。各區域由地區指揮官透過通訊指揮機器人小隊作戰。\n\n"
                         "👁 **觀戰**：取得觀戰身分，可唯讀檢視所有指揮官的頻道。\n"
                         "（指揮官就任功能開發中，目前由 GM 分配部隊）"),
            color=discord.Color.dark_gold(),
        )
        message_id = self.dstate.get("lobby_message")
        try:
            if message_id:
                message = await lobby.fetch_message(message_id)
                await message.edit(embed=embed, view=LobbyView(self))
                return lobby
        except discord.NotFound:
            pass
        message = await lobby.send(embed=embed, view=LobbyView(self))
        self.dstate["lobby_message"] = message.id
        self.sim.save()
        return lobby

    async def set_viewers(self, guild: discord.Guild, commander_id: str, users: list, grant: bool):
        c = self.sim.world.commanders[commander_id]
        for u in users:
            if u.bot or str(u.id) == c.id:
                continue
            if grant and u.id not in c.viewers:
                c.viewers.append(u.id)
            if not grant and u.id in c.viewers:
                c.viewers.remove(u.id)
        self.sim.save()
        await self.apply_commander_perms(guild, c)

    def share_embed(self, c: Commander, guild: discord.Guild) -> discord.Embed:
        names = []
        for vid in c.viewers:
            m = guild.get_member(vid)
            names.append(m.mention if m else f"<@{vid}>")
        return discord.Embed(
            title="🔗 情報共享",
            description=("被開放的人可以**唯讀**檢視你的指揮所與所有小隊頻道，但不能下令。\n\n"
                         f"目前開放：{'、'.join(names) if names else '（無）'}"),
            color=discord.Color.blurple(),
        )

    # ------------------------------------------------------------------ ticker / flush

    @tasks.loop(seconds=10)
    async def ticker(self):
        try:
            await self.sim.tick()
            await self.flush()
        except Exception:
            log.exception("tick failed")

    @ticker.before_loop
    async def before_ticker(self):
        await self.bot.wait_until_ready()

    async def flush(self):
        async with self.flush_lock:
            for card in self.sim.new_cards:
                save_card(card)
            self.sim.new_cards.clear()
            items = self.sim.drain()
            for item in items:
                if item["type"] == "reinforce":
                    await self.open_squad_channel(item["commander"], item["squad"], announce=True)
            for item in items:
                if item["type"] == "reinforce":
                    continue
                if item["type"] == "mission":
                    await self.post_mission(item)
                    continue
                info = self.dstate["squads"].get(item["squad"])
                if not info:
                    continue
                try:
                    webhook = discord.Webhook.from_url(info["webhook"], client=self.bot)
                    squad = self.sim.world.squads.get(item["squad"])
                    callsign = squad.callsign if squad else item["squad"]
                    if item["type"] == "report":
                        await webhook.send(content=item["text"], username=f"{item['name']}｜{callsign}")
                    else:
                        await webhook.send(content=f"*{item['text']}*", username=f"通訊系統｜{callsign}")
                except discord.HTTPException:
                    log.exception("webhook send failed")
        now = time.monotonic()
        for cid in list(self.sim.dirty):
            if now - self.last_panel_refresh.get(cid, 0) >= self.cfg["adjutant_min_interval"]:
                await self.update_panel(cid)
        await self.housekeeping()

    async def housekeeping(self):
        tick = self.sim.world.tick
        for c in list(self.sim.world.commanders.values()):
            channel = self.bot.get_channel(c.hq_channel or 0)
            if channel is None:
                continue
            try:
                await self.layout.sweep(channel, c)
                sig = (len(c.events), c.events[-1] if c.events else "", len(c.history))
                if sig != self.board_sig.get(c.id) or tick % 6 == 0:
                    self.board_sig[c.id] = sig
                    await self.layout.update_board(channel, c)
            except discord.HTTPException:
                log.exception("指揮所維護失敗 %s", c.id)

    async def post_mission(self, item: dict):
        c = self.sim.world.commanders.get(item["commander"])
        channel = self.bot.get_channel(c.hq_channel or 0) if c else None
        if channel is None:
            return
        m = item["mission"]
        line = "主線" if m["line"] == "main" else "支線"
        if item["event"] == "start":
            limit = (f"約 {max(1, self.sim.seconds(m['time_limit']) // 60)} 分鐘" if m.get("time_limit")
                     else "無時限")
            text = f"📜 新{line}任務：**{m['title']}**（{scenario.cell_desc(m['cell'])}，{limit}）"
        elif item["event"] == "done":
            text = f"✅ {line}達成：**{m['title']}**"
        else:
            text = f"❌ {line}失敗：**{m['title']}**（{item.get('reason', '')}）"
        try:
            await self.layout.update_board(channel, c)
            await self.layout.post_notice(channel, c, text)
        except discord.HTTPException:
            log.exception("mission post failed")

    def build_board_embed(self, c: Commander) -> discord.Embed:
        embed = discord.Embed(title="📜 指揮部任務看板", color=discord.Color.gold())
        now = self.sim.world.tick
        embed.add_field(name="🗺 戰情圖（依你的情報）", value=f"```\n{self.sim.intel_map(c.id)}\n```", inline=False)
        embed.add_field(name="⚖ 兵力", value=self.sim.force_summary(c.id) or "—", inline=False)
        if c.events:
            embed.add_field(name="📡 最近戰況", value="\n".join(c.events[-6:])[:1000], inline=False)
        if c.missions:
            for m in c.missions:
                extra = f"\n守住進度：{m.get('progress', 0)}/{m['hold_ticks']}" if m["type"] == "hold" else ""
                if m["type"] == "remnant":
                    extra = "\n" + self.sim.remnant_status_text(m)
                if m.get("deadline") is None:
                    when = "無時限"
                else:
                    left = max(0, m["deadline"] - now)
                    when = f"剩約 {max(0, self.sim.seconds(left) // 60)} 分 {self.sim.seconds(left) % 60} 秒"
                embed.add_field(
                    name=f"{'🟨 主線' if m['line'] == 'main' else '🟦 支線'}｜{m['title']}",
                    value=f"{m['brief']}\n地點：{scenario.cell_desc(m['cell'])}｜{when}{extra}",
                    inline=False,
                )
        else:
            embed.description = "目前沒有進行中的任務。"
        if c.history:
            lines = []
            for h in c.history[-5:][::-1]:
                mark = "✅" if h["success"] else "❌"
                reason = f"：{h['reason']}" if not h["success"] and h.get("reason") else ""
                lines.append(f"{mark} {h['title']}{reason}（T{h['t']}）")
            embed.add_field(name="最近結果", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"現在 T{now}")
        return embed

    def build_panel(self, c: Commander) -> tuple[discord.Embed, discord.ui.View]:
        return self.build_embed(c), AdjutantView(self, c.adjutant.get("suggestions", []))

    # ------------------------------------------------------------------ adjutant panel

    def build_embed(self, c: Commander) -> discord.Embed:
        adj = c.adjutant
        embed = discord.Embed(title=f"副官｜戰況簡報（T{adj.get('tick', 0)}）",
                              description=adj.get("summary") or "尚無簡報，按「更新現況」。",
                              color=discord.Color.dark_teal())
        for sid, s in c.intel["squads"].items():
            sq = self.sim.world.squads.get(sid)
            linked = sq.linked if sq else s["linked"]
            state = "📵 失聯" if not linked else ("☠ 全滅" if s["status"] == "destroyed" else "📶 通訊正常")
            embed.add_field(
                name=f"{s['callsign']}（{state}）",
                value=(f"位置 {scenario.cell_desc(s['pos'])}\n"
                       f"人員 {s['alive']}/{s['total']}｜彈藥 {s['ammo']}%｜士氣 {s['morale']}\n"
                       f"行動：{s['action']}"
                       f"{chr(10) + '🔧 ' + s['rest'] if s.get('rest') else ''}\n最後回報 T{s['tick']}"),
                inline=False,
            )
        enemies = c.intel["enemies"]
        embed.add_field(
            name="敵情",
            value="\n".join(f"{scenario.cell_desc(x)}：約 {e['est']}（T{e['tick']}{'，疑似' if e.get('suspected') else ''}）"
                            for x, e in enemies.items()) or "無確切情報",
            inline=False,
        )
        embed.add_field(name="任務", value=self.sim.missions_brief(c.id)[:1000], inline=False)
        embed.set_footer(text=f"指揮官 {c.name}｜現在 T{self.sim.world.tick}｜副官僅依情報表判斷，情報可能過時或錯誤")
        return embed

    async def update_panel(self, commander_id: str, force: bool = False):
        lock = self.panel_locks.setdefault(commander_id, asyncio.Lock())
        async with lock:
            if not force and commander_id not in self.sim.dirty:
                return
            self.last_panel_refresh[commander_id] = time.monotonic()
            adj = await self.sim.refresh_adjutant(commander_id)
            c = self.sim.world.commanders.get(commander_id)
            channel = self.bot.get_channel(c.hq_channel or 0) if c else None
            if adj is None or channel is None:
                return
            try:
                await self.layout.update_panel(channel, c)
            except discord.HTTPException:
                log.exception("副官面板更新失敗")

    async def use_suggestion(self, i: discord.Interaction, c: Commander, idx: int):
        suggestions = c.adjutant.get("suggestions", [])
        if idx >= len(suggestions):
            await notify(i, "這個建議已失效，請按「更新現況」。", seconds=self.short)
            return
        s = suggestions[idx]
        squad = self.sim.world.squads.get(s["squad"])
        info = self.dstate["squads"].get(s["squad"])
        channel = self.bot.get_channel(info["channel"]) if info else None
        if channel is None or squad is None or squad.commander != c.id:
            await notify(i, "找不到該小隊頻道。", seconds=self.short)
            return
        await notify(i, f"已透過 {channel.mention} 下令：{s['order']}", seconds=self.short)
        await channel.send(f"🎙 **{i.user.display_name}**（副官建議）：{s['order']}")
        asyncio.create_task(self.run_order(channel, s["squad"], s["order"]))

    async def run_order(self, channel: discord.abc.Messageable, squad_id: str, text: str):
        try:
            async with channel.typing():
                await self.sim.issue_order(squad_id, text)
            await self.flush()
        except Exception:
            log.exception("order failed")

    # ------------------------------------------------------------------ orders from chat

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or not message.content:
            return
        squad_id = self.squad_of_channel(message.channel.id)
        if not squad_id or message.content.startswith("."):
            return
        squad = self.sim.world.squads.get(squad_id)
        if squad is None or squad.commander != str(message.author.id):
            return
        await self.run_order(message.channel, squad_id, message.content)

    async def ensure_squad_channel(self, guild: discord.Guild, c: Commander, squad_id: str,
                                   callsign: str) -> discord.TextChannel:
        category = guild.get_channel(c.category)
        info = self.dstate["squads"].get(squad_id)
        channel = self.bot.get_channel(info["channel"]) if info else None
        if channel is not None and channel.category_id != c.category:
            await channel.edit(category=category, sync_permissions=True)
        if channel is None:
            channel = await guild.create_text_channel(f"{callsign.lower()}-小隊", category=category)
            info = None
        squad_obj = self.sim.world.squads.get(squad_id)
        short = (squad_obj.card_id if squad_obj and squad_obj.card_id else squad_id)[:40]
        webhook_ok = False
        if info and info.get("webhook"):
            try:
                await discord.Webhook.from_url(info["webhook"], client=self.bot).fetch()
                webhook_ok = True
            except (discord.NotFound, discord.HTTPException):
                webhook_ok = False
        if not webhook_ok:
            webhook = await channel.create_webhook(name=f"AC-{short}")
            self.dstate["squads"][squad_id] = {"channel": channel.id, "webhook": webhook.url}
            self.sim.save()
        return channel

    async def open_squad_channel(self, commander_id: str, squad_id: str, announce: bool = False):
        c = self.sim.world.commanders.get(commander_id)
        squad = self.sim.world.squads.get(squad_id)
        guild = self.bot.get_guild(self.dstate.get("guild_id") or 0)
        if not c or not squad or guild is None or not c.category:
            return
        try:
            channel = await self.ensure_squad_channel(guild, c, squad.id, squad.callsign)
            if announce:
                await channel.send(f"📡 指揮部增援：**{squad.callsign}** 小隊已抵達 {scenario.cell_desc(squad.pos)}，"
                                   f"並與 <@{c.id}> 建立通訊。")
        except discord.HTTPException:
            log.exception("增援頻道建立失敗")

    # ------------------------------------------------------------------ GM commands

    @app_commands.guild_only()
    @app_commands.command(name="ac_squad_spawn", description="（GM 暫時用）部署小隊給指揮官")
    @app_commands.describe(squad="小隊角色卡 id（setting/squads/<id>.json）；填 new 由 LLM 生成全新小隊",
                           commander="所屬指揮官（預設為自己）",
                           reset="已存在時是否以角色卡重新部署（狀態會被覆蓋）")
    async def ac_squad_spawn(self, i: discord.Interaction, squad: str = "alpha",
                             commander: discord.Member | None = None, reset: bool = False):
        if not await self.gm_check(i):
            return
        if squad.strip().lower() == "new":
            await i.response.defer(ephemeral=True, thinking=True)
            card = await self.sim.generate_card("指揮部新編並派往前線的小隊")
            if not card:
                await notify(i, "角色卡生成失敗（LLM 無回應），請再試一次。", seconds=self.short)
                return
            save_card(card)
        else:
            card = load_card(squad)
            if not card:
                await notify(i, f"找不到角色卡：{squad}", seconds=self.short)
                return
            await i.response.defer(ephemeral=True, thinking=True)
        guild = i.guild
        self.dstate["guild_id"] = guild.id
        await self.ensure_lobby(guild)

        member = commander or i.user
        c = await self.ensure_commander_space(guild, member)
        sq, created = await self.sim.deploy_squad(card, c.id, reset=reset)
        channel = await self.ensure_squad_channel(guild, c, sq.id, sq.callsign)
        self.sim.world.running = True
        self.sim.save()
        if not self.ticker.is_running():
            self.ticker.start()
        hq = guild.get_channel(c.hq_channel)
        if created:
            await channel.send(f"📡 已與 **{sq.callsign}** 小隊建立通訊。指揮官 {member.mention} 可直接在此頻道輸入命令。")
            await self.update_panel(c.id, force=True)
            await notify(i, f"已部署 {sq.callsign} → {member.mention}：{channel.mention}，指揮所：{hq.mention}",
                         seconds=self.long)
        else:
            await notify(i, f"{sq.callsign} 已在戰場上（{scenario.cell_desc(sq.pos)}，"
                                  f"{len(sq.alive_members())}/{len(sq.members)} 人，{sq.status}），未重置。"
                                  f"頻道：{channel.mention}", seconds=self.long)

    @app_commands.guild_only()
    @app_commands.command(name="ac_lobby", description="（GM）建立或更新報到處與觀戰身分組")
    async def ac_lobby(self, i: discord.Interaction):
        if not await self.gm_check(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        self.dstate["guild_id"] = i.guild.id
        lobby = await self.ensure_lobby(i.guild)
        for c in self.sim.world.commanders.values():
            await self.apply_commander_perms(i.guild, c)
        await notify(i, f"報到處：{lobby.mention}，觀戰身分組與所有指揮官頻道權限已同步。", seconds=self.short)

    @app_commands.command(name="ac_link", description="（GM）切斷或恢復小隊通訊")
    @app_commands.describe(squad="小隊 id", state="on=恢復 / off=切斷")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"),
                                 app_commands.Choice(name="off", value="off")])
    async def ac_link(self, i: discord.Interaction, squad: str, state: app_commands.Choice[str]):
        if not await self.gm_check(i):
            return
        s = self.sim.resolve(squad)
        if s is None:
            await notify(i, f"沒有小隊或不唯一：{squad}（請用完整 id）", seconds=self.short)
            return
        await notify(i, f"{s.callsign} 通訊 → {state.value}", seconds=self.short)
        await self.sim.set_link(s.id, state.value == "on")
        await self.flush()

    @app_commands.command(name="ac_tick", description="（GM）手動推進 tick")
    @app_commands.describe(count="推進次數")
    async def ac_tick(self, i: discord.Interaction, count: app_commands.Range[int, 1, 30] = 1):
        if not await self.gm_check(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        for _ in range(count):
            await self.sim.tick()
            await self.flush()
        await notify(i, f"已推進 {count} tick，現在 T{self.sim.world.tick}", seconds=self.short)

    @app_commands.command(name="ac_run", description="（GM）暫停或繼續背景 tick")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"),
                                 app_commands.Choice(name="off", value="off")])
    async def ac_run(self, i: discord.Interaction, state: app_commands.Choice[str]):
        if not await self.gm_check(i):
            return
        running = state.value == "on"
        self.sim.world.running = running
        self.sim.save()
        if running and not self.ticker.is_running():
            self.ticker.start()
        if not running and self.ticker.is_running():
            self.ticker.cancel()
        await notify(i, f"背景 tick：{state.value}", seconds=self.short)

    @app_commands.command(name="ac_tmap", description="（GM）查看某一格的戰術地圖與個體位置")
    @app_commands.describe(cell="作戰層格子，例如 D3")
    async def ac_tmap(self, i: discord.Interaction, cell: str):
        if not await self.gm_check(i):
            return
        cell = cell.strip().upper()
        if cell not in scenario.CELLS:
            await notify(i, f"沒有這一格：{cell}", seconds=self.short)
            return
        w = self.sim.world
        art = worldmap.render(w, cell)
        squads = [f"{s.callsign}：" + "、".join(f"{m.name}({m.weapon}){tuple(m.tpos) if m.tpos else ''}"
                                                for m in s.alive_members())
                  for s in w.squads.values() if s.pos == cell and s.status == "active"]
        foes = [f"{e.id}（{e.tag}）{len(e.soldiers)} 人" for e in w.enemies.values() if e.pos == cell and e.alive]
        text = (f"**{scenario.cell_desc(cell)}**｜模板 {worldmap.template(cell)}｜"
                f"大寫＝我方小隊、e＝敵兵、#牆 W窗 =矮牆 %瓦礫 \"植被 H樓梯\n"
                f"```\n{art}\n```" + ("\n".join(squads + foes) or "（無人）"))
        await notify(i, text[:1990], seconds=None)

    @app_commands.command(name="ac_status", description="（GM）查看戰場真相")
    @app_commands.describe(squad="指定小隊時顯示其經歷與內心解讀")
    async def ac_status(self, i: discord.Interaction, squad: str | None = None):
        if not await self.gm_check(i):
            return
        w = self.sim.world
        if squad:
            s = self.sim.resolve(squad)
            if s is None:
                await notify(i, f"沒有小隊：{squad}（可用完整 id、角色卡 id 或呼號）", seconds=self.short)
                return
            head = (f"**{s.callsign}**｜服從基準 {obedience.score(s)}（危險命令 {obedience.score(s, True)}）"
                    f"｜自身經驗信念比例 {obedience.self_ratio(s):.0%}\n"
                    f"**信念**\n{obedience.beliefs_text(s)}\n**傾向**：{obedience.tendency_text(s)}\n"
                    f"**長期記憶**：{s.long_memory or '（無）'}\n")
            budget = 1990 - len(head) - 30
            exp = self.sim.experience_text(s, n=12, show_interp=True)
            if len(exp) > budget:
                exp = "…" + exp[-budget:]
            await notify(i, f"{head}**經歷（含內心解讀）**\n```\n{exp}\n```", seconds=None)
            return
        lines = [f"**T{w.tick}**｜背景 tick：{'on' if self.ticker.is_running() else 'off'}"]
        for s in w.squads.values():
            cname = w.commanders[s.commander].name if s.commander in w.commanders else "-"
            lines.append(
                f"**{s.callsign}**（{cname}）{s.pos} {s.status} {'📶' if s.linked else '📵'}｜"
                f"服從 {obedience.score(s)}｜{s.tendencies.get('execution')}"
                f"{'｜驗證' if s.tendencies.get('verify') else ''}｜士氣 {s.morale} 彈藥 {s.ammo}｜"
                f"行動 {s.action.type} {s.action.target or ''} {s.action.progress}/{s.action.duration}｜"
                f"{(self.sim.rest_progress(s) + '｜') if self.sim.rest_progress(s) else ''}"
                f"接觸 {s.contact or '-'}\n　" + " ".join(f"{m.name}({m.hp})" for m in s.members)
            )
        for e in w.enemies.values():
            if not e.alive:
                continue
            lines.append(f"敵 {e.id}（{e.tag}）：{e.pos} 戰力 {e.strength}"
                         f"{f'｜{e.order}→{e.goal}' if e.order else ''}｜交戰 {e.engaged_with or '-'}")
        lines.append(f"敵方總兵力 {enemy_ai.total_strength(w)}/{enemy_ai.STRENGTH_CAP}｜"
                     f"敵方情報：{', '.join(w.enemy_intel) or '無'}"
                     f"{'｜最近決策：' + '；'.join(self.sim.enemy_log) if self.sim.enemy_log else ''}")
        await notify(i, "\n".join(lines)[:1900], seconds=None)

    @app_commands.command(name="ac_squad_reset", description="（GM）重置戰場與所有小隊狀態（保留頻道與隸屬）")
    async def ac_squad_reset(self, i: discord.Interaction):
        if not await self.gm_check(i):
            return
        await i.response.defer(ephemeral=True, thinking=True)
        async with self.sim.lock:
            roster = self.sim.reset()
        for sid, cid, card_id in roster:
            card = load_card(card_id)
            if card and cid:
                await self.sim.deploy_squad(card, cid)
        for sid, _, _ in roster:
            info = self.dstate["squads"].get(sid)
            channel = self.bot.get_channel(info["channel"]) if info else None
            if channel:
                await channel.send("🔁 ───── 戰場已重置 ─────")
        for cid in list(self.sim.world.commanders):
            await self.update_panel(cid, force=True)
        await notify(i, "已重置。", seconds=self.short)


    # ------------------------------------------------------------------ wipe

    def wipe_targets(self, guild: discord.Guild) -> tuple[list, list, discord.Role | None]:
        channel_ids: set[int] = set()
        category_ids: set[int] = set()
        for info in self.dstate["squads"].values():
            if info.get("channel"):
                channel_ids.add(info["channel"])
        for c in self.sim.world.commanders.values():
            if c.hq_channel:
                channel_ids.add(c.hq_channel)
            if c.category:
                category_ids.add(c.category)
        if self.dstate.get("lobby_channel"):
            channel_ids.add(self.dstate["lobby_channel"])
        names = {self.cfg["lobby_category"], *LEGACY_CATEGORIES}
        prefix = f"{self.cfg['commander_category_prefix']} "
        for cat in guild.categories:
            if cat.name in names or cat.name.startswith(prefix):
                category_ids.add(cat.id)
        categories = [x for x in (guild.get_channel(cid) for cid in category_ids) if x is not None]
        for cat in categories:
            channel_ids.update(ch.id for ch in cat.channels)
        channels = [x for x in (guild.get_channel(cid) for cid in channel_ids) if x is not None]
        role = guild.get_role(self.dstate.get("spectator_role") or 0) or \
            discord.utils.get(guild.roles, name=self.cfg["spectator_role"])
        return channels, categories, role

    async def wipe_all(self, guild: discord.Guild) -> str:
        if self.ticker.is_running():
            self.ticker.cancel()
        channels, categories, role = self.wipe_targets(guild)
        deleted, failed = 0, []
        for target in [*channels, *categories]:
            try:
                await target.delete(reason="AC 全部清除（GM）")
                deleted += 1
            except discord.HTTPException as e:
                failed.append(f"{target.name}（{e.__class__.__name__}）")
            await asyncio.sleep(0.5)
        role_text = "無"
        if role:
            try:
                await role.delete(reason="AC 全部清除（GM）")
                role_text = "已刪除"
            except discord.HTTPException as e:
                failed.append(f"身分組 {role.name}（{e.__class__.__name__}）")
                role_text = "刪除失敗"
        async with self.sim.lock:
            self.sim.wipe()
        self.panel_locks.clear()
        self.last_panel_refresh.clear()
        self.layout.locks.clear()
        self.layout.last_repost.clear()
        lines = [f"🧨 全部清除完成：刪除 {deleted} 個頻道／分類，觀戰身分組：{role_text}。",
                 f"存檔已重置（舊存檔備份為 {self.sim.save_path}.bak），背景 tick 已停止。",
                 "重新開始：`/ac_squad_spawn alpha`"]
        if failed:
            lines.append("未能刪除：" + "、".join(failed))
        return "\n".join(lines)

    @app_commands.guild_only()
    @app_commands.command(name="ac_wipe", description="（GM／測試用）刪除所有 AC 頻道、分類、觀戰身分組並重置存檔")
    async def ac_wipe(self, i: discord.Interaction):
        if not await self.gm_check(i):
            return
        channels, categories, role = self.wipe_targets(i.guild)
        cat_names = "、".join(c.name for c in categories) or "（無）"
        embed = discord.Embed(
            title="🧨 確認全部清除？",
            description=(f"將刪除 **{len(channels)}** 個頻道、**{len(categories)}** 個分類"
                         f"{'、觀戰身分組「' + role.name + '」' if role else ''}，並重置整個戰場存檔。\n\n"
                         f"分類：{cat_names}\n\n此操作無法復原（存檔會備份一份）。60 秒內有效。"),
            color=discord.Color.red(),
        )
        view = WipeView(self, i.user.id)
        await i.response.send_message(embed=embed, view=view, ephemeral=True)
        view.interaction = i


async def setup(bot):
    await bot.add_cog(Squad(bot))
