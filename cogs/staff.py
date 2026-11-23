# -*- coding: utf-8 -*-
"""Статистика стаффа: лапки за верификации, время в проходке, магазин плюшек."""

import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import staff_core as sc

COLOR = 0xA9C9FF

ROLE_COLORS = [
    ("Розовый", 0xFF9AD5),
    ("Лаванда", 0xB39DDB),
    ("Голубой", 0x8ECAE6),
    ("Мятный", 0x9BE7C4),
    ("Персиковый", 0xFFB38C),
    ("Золотой", 0xFFD166),
    ("Красный", 0xED4245),
    ("Белый", 0xF2F3F5),
]

DAYOFF_ROLE_NAME = "🌴 Отгул"


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _span(seconds) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


def _embed(title: str, description: str = None) -> discord.Embed:
    e = discord.Embed(title=title, color=COLOR, timestamp=discord.utils.utcnow())
    if description:
        e.description = description
    return e


# ── Магазин: своя роль ────────────────────────────────────────────────────────

class RoleNameModal(discord.ui.Modal, title="Своя роль"):
    name_input = discord.ui.TextInput(
        label="Название роли",
        placeholder="например: ✨ Звёздочка",
        min_length=2,
        max_length=32,
    )

    def __init__(self, color: discord.Color, price: int):
        super().__init__()
        self.color = color
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        name = str(self.name_input.value).strip()
        if len(name) < 2:
            await interaction.response.send_message("Слишком короткое название.", ephemeral=True)
            return
        if not sc.spend(guild.id, interaction.user.id, self.price):
            await interaction.response.send_message("Не хватает лапок.", ephemeral=True)
            return
        try:
            role = await guild.create_role(
                name=name[:32],
                color=self.color,
                reason=f"Своя роль из магазина стаффа ({interaction.user})",
            )
            await interaction.user.add_roles(role, reason="Своя роль из магазина стаффа")
        except discord.Forbidden:
            sc.give(guild.id, interaction.user.id, self.price)
            await interaction.response.send_message(
                "Не могу создать роль: нужно право «Управлять ролями» и чтобы роль бота была выше.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Готово: {role.mention}. Списано **{_fmt(self.price)} {sc.staff_currency(guild.id)}**.",
            ephemeral=True,
        )


class RoleColorView(discord.ui.View):
    def __init__(self, price: int):
        super().__init__(timeout=180)
        self.price = price
        select = discord.ui.Select(
            placeholder="Выбери цвет роли",
            options=[
                discord.SelectOption(label=name, value=str(value))
                for name, value in ROLE_COLORS
            ],
        )
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        color = discord.Color(int(interaction.data["values"][0]))
        await interaction.response.send_modal(RoleNameModal(color, self.price))


# ── Магазин: отгул ────────────────────────────────────────────────────────────

async def _ensure_dayoff_role(guild: discord.Guild):
    """Роль-маркер отгула. Создаём при первой покупке, id запоминаем в настройках."""
    role_id = sc.dayoff_role_id(guild.id)
    role = guild.get_role(role_id) if role_id else None
    if role is not None:
        return role

    role = discord.utils.get(guild.roles, name=DAYOFF_ROLE_NAME)
    if role is not None:
        sc.set_settings(guild.id, dayoff_role_id=role.id)
        return role

    try:
        role = await guild.create_role(
            name=DAYOFF_ROLE_NAME,
            color=discord.Color.dark_green(),
            permissions=discord.Permissions.none(),
            mentionable=False,
            reason="Роль для отгулов стаффа",
        )
    except discord.Forbidden:
        return None
    sc.set_settings(guild.id, dayoff_role_id=role.id)
    return role


class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Своя роль", style=discord.ButtonStyle.primary, emoji="🎨")
    async def role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        price = sc.role_price(guild.id)
        cur = sc.staff_currency(guild.id)
        if int(sc.get_entry(guild.id, interaction.user.id).get("coins", 0)) < price:
            await interaction.response.send_message(
                f"Не хватает лапок: нужно **{_fmt(price)} {cur}**.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Роль стоит **{_fmt(price)} {cur}**. Выбери цвет:",
            view=RoleColorView(price),
            ephemeral=True,
        )

    @discord.ui.button(label="Отгул", style=discord.ButtonStyle.success, emoji="🌴")
    async def dayoff_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        cur = sc.staff_currency(guild.id)
        price = sc.dayoff_price(guild.id)
        days = sc.dayoff_days(guild.id)

        current = sc.dayoff_until_str(guild.id, interaction.user.id)
        if current:
            await interaction.response.send_message(
                f"Ты уже в отгуле до **{current}**.", ephemeral=True
            )
            return

        if not sc.spend(guild.id, interaction.user.id, price):
            await interaction.response.send_message(
                f"Не хватает лапок: отгул стоит **{_fmt(price)} {cur}**.", ephemeral=True
            )
            return

        role = await _ensure_dayoff_role(guild)
        if role is None:
            sc.give(guild.id, interaction.user.id, price)
            await interaction.response.send_message(
                "Не могу выдать отгул: нужно право «Управлять ролями» и роль бота выше.",
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason=f"Отгул на {days} дн. из магазина")
        except discord.Forbidden:
            sc.give(guild.id, interaction.user.id, price)
            await interaction.response.send_message(
                "Не могу выдать роль отгула — проверь позицию роли бота.", ephemeral=True
            )
            return

        until_ts = time.time() + days * 86400
        sc.set_dayoff(guild.id, interaction.user.id, until_ts)
        # Смена больше не капает, даже если человек уже сидит в проходке.
        sc.voice_drop(guild.id, interaction.user.id)

        await interaction.response.send_message(
            f"🌴 Отгул на **{days} дн.** до **{sc.dayoff_until_str(guild.id, interaction.user.id)}**.\n"
            f"Списано **{_fmt(price)} {cur}**. Верификации закрыты, но сидеть в проходке можно.",
            ephemeral=True,
        )


class Staff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self._flush.start()
        self._expire_dayoffs.start()

    async def cog_unload(self):
        self._flush.cancel()
        self._expire_dayoffs.cancel()
        sc.save_data(force=True)

    @tasks.loop(minutes=10)
    async def _expire_dayoffs(self):
        for guild in self.bot.guilds:
            role_id = sc.dayoff_role_id(guild.id)
            role = guild.get_role(role_id) if role_id else None
            if role is None:
                role = discord.utils.get(guild.roles, name=DAYOFF_ROLE_NAME)

            for user_id, _until in sc.expired_dayoffs(guild.id):
                member = guild.get_member(user_id)
                if member and role:
                    try:
                        await member.remove_roles(role, reason="Отгул закончился")
                    except discord.HTTPException:
                        continue
                sc.clear_dayoff(guild.id, user_id)
                # Если человек всё это время сидел в проходке — начинаем считать снова.
                if member and member.voice and member.voice.channel and sc.is_passage(member.voice.channel):
                    sc.voice_start(guild.id, member.id)

    @_expire_dayoffs.before_loop
    async def _before_expire(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def _flush(self):
        sc.save_data()

    @_flush.before_loop
    async def _before_flush(self):
        await self.bot.wait_until_ready()

    # ── Учёт времени в проходке ───────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        # После рестарта подхватываем тех, кто уже сидит в проходке.
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                if not sc.is_passage(channel):
                    continue
                for member in channel.members:
                    if not member.bot and sc.is_staff(member) and not sc.on_dayoff(guild.id, member.id):
                        sc.voice_start(guild.id, member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot or not sc.is_staff(member):
            return
        guild = member.guild
        was_in = before.channel is not None and sc.is_passage(before.channel)
        now_in = after.channel is not None and sc.is_passage(after.channel)

        if now_in and not was_in:
            # В отгуле смена не капает: сидеть можно, часы — нет.
            if not sc.on_dayoff(guild.id, member.id):
                sc.voice_start(guild.id, member.id)
        elif was_in and not now_in:
            sc.voice_stop(guild.id, member.id)

    # ── Статистика ────────────────────────────────────────────────────────

    @app_commands.command(name="staff_stats", description="Статистика стаффа: лапки, верификации, время в проходке")
    @app_commands.describe(user="Чья статистика (по умолчанию твоя)")
    async def staff_stats(self, interaction: discord.Interaction, user: discord.Member = None):
        member = user or interaction.user
        guild_id = interaction.guild.id
        e_data = sc.get_entry(guild_id, member.id)
        cur = sc.staff_currency(guild_id)

        verified_today, voice_today = sc.day_stat(guild_id, member.id, 1)
        verified_week, voice_week = sc.day_stat(guild_id, member.id, 7)
        live = sc.voice_live(guild_id, member.id)
        voice_today += live
        voice_week += live
        place = sc.place(guild_id, member.id)

        embed = _embed(f"🐾 Стафф-профиль — {member.display_name}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name=f"Лапки {cur}",
            value=f"**{_fmt(e_data.get('coins', 0))}** · #{place} в топе",
            inline=False,
        )
        embed.add_field(
            name="Верификаций",
            value=f"сегодня **{_fmt(verified_today)}**\nза 7 дней **{_fmt(verified_week)}**",
            inline=True,
        )
        embed.add_field(
            name="В проходке",
            value=(
                f"сегодня **{_span(voice_today)}**{' 🎧' if live else ''}\n"
                f"за 7 дней **{_span(voice_week)}**"
            ),
            inline=True,
        )
        embed.add_field(name="С нами с", value=e_data.get("first_seen", "—"), inline=True)
        until = sc.dayoff_until_str(guild_id, member.id)
        if until:
            embed.add_field(
                name="🌴 Отгул",
                value=f"до **{until}**\nверификации закрыты",
                inline=True,
            )
        embed.set_footer(text=f"1 верификация = {_fmt(sc.coin_per_verify(guild_id))} {cur}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="staff_top", description="Топ стаффа по лапкам")
    async def staff_top(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        rows = sc.top(guild_id, 10)
        if not rows:
            await interaction.response.send_message(
                "Пока пусто — лапки капают за верификации.", ephemeral=True
            )
            return

        cur = sc.staff_currency(guild_id)
        medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
        lines = []
        for i, (uid, info) in enumerate(rows):
            lines.append(
                f"{medals[i]} <@{uid}> — **{_fmt(info.get('coins', 0))} {cur}**"
                f" · {_fmt(info.get('verified_total', 0))} вериф."
                f" · {_span(info.get('voice_total', 0))}"
            )
        embed = _embed("🏆 Топ стаффа", "\n".join(lines))
        embed.set_footer(text=f"1 верификация = {_fmt(sc.coin_per_verify(guild_id))} {cur}")
        await interaction.response.send_message(embed=embed)

    # ── Магазин ───────────────────────────────────────────────────────────

    @app_commands.command(name="staff_shop", description="Магазин стаффа: своя роль и отгул")
    async def staff_shop(self, interaction: discord.Interaction):
        if not sc.is_staff(interaction.user):
            await interaction.response.send_message("Магазин только для стаффа.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        cur = sc.staff_currency(guild_id)
        balance = int(sc.get_entry(guild_id, interaction.user.id).get("coins", 0))
        role_p = sc.role_price(guild_id)
        off_p = sc.dayoff_price(guild_id)
        days = sc.dayoff_days(guild_id)
        until = sc.dayoff_until_str(guild_id, interaction.user.id)

        desc = (
            f"Твой баланс: **{_fmt(balance)} {cur}**\n\n"
            f"🎨 **Своя роль** — **{_fmt(role_p)} {cur}**\n"
            f"> Цвет и название на выбор. Только для красоты, прав не даёт.\n\n"
            f"🌴 **Отгул на {days} дн.** — **{_fmt(off_p)} {cur}**\n"
            f"> Верификации закрыты, смена не капает. Сидеть в проходке можно."
        )
        if until:
            desc += f"\n\n_Сейчас ты в отгуле до **{until}**._"

        await interaction.response.send_message(
            embed=_embed("🛒 Магазин стаффа", desc), view=ShopView(), ephemeral=True
        )

    # ── Настройка и выдача ────────────────────────────────────────────────

    @app_commands.command(name="staff_config", description="Настроить лапки и магазин стаффа")
    @app_commands.describe(
        coin="Сколько лапок за одну верификацию",
        currency="Название валюты стаффа",
        role_price="Цена своей роли",
        dayoff_price="Цена отгула",
        dayoff_days="Сколько дней длится отгул",
        dayoff_role="Роль-маркер отгула (по умолчанию создам сам)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staff_config(
        self,
        interaction: discord.Interaction,
        coin: app_commands.Range[int, 0, 1000] = None,
        currency: str = None,
        role_price: app_commands.Range[int, 0, 100000] = None,
        dayoff_price: app_commands.Range[int, 0, 100000] = None,
        dayoff_days: app_commands.Range[int, 1, 30] = None,
        dayoff_role: discord.Role = None,
    ):
        sc.set_settings(
            interaction.guild.id,
            verify_coin=coin,
            staff_currency=(currency[:20] if currency else None),
            staff_role_price=role_price,
            staff_dayoff_price=dayoff_price,
            staff_dayoff_days=dayoff_days,
            dayoff_role_id=(dayoff_role.id if dayoff_role else None),
        )
        cur = sc.staff_currency(interaction.guild.id)
        await interaction.response.send_message(
            embed=_embed(
                "⚙️ Настройки стаффа",
                f"За верификацию: **{_fmt(sc.coin_per_verify(interaction.guild.id))} {cur}**\n"
                f"Своя роль: **{_fmt(sc.role_price(interaction.guild.id))} {cur}**\n"
                f"Отгул на {sc.dayoff_days(interaction.guild.id)} дн.: "
                f"**{_fmt(sc.dayoff_price(interaction.guild.id))} {cur}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="staff_give", description="Начислить лапки участнику")
    @app_commands.describe(user="Кому", amount="Сколько")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_give(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        total = sc.give(interaction.guild.id, user.id, amount)
        cur = sc.staff_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ {user.mention} +{_fmt(amount)} {cur}. Теперь {_fmt(total)} {cur}.", ephemeral=True
        )

    @app_commands.command(name="staff_take", description="Снять лапки у участника")
    @app_commands.describe(user="У кого", amount="Сколько")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_take(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        total = sc.give(interaction.guild.id, user.id, -amount)
        cur = sc.staff_currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ У {user.mention} снял {_fmt(amount)} {cur}. Осталось {_fmt(total)} {cur}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
