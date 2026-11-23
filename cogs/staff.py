# -*- coding: utf-8 -*-
"""Статистика стаффа: лапки за верификации и время в проходке."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs import staff_core as sc

COLOR = 0xA9C9FF


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


class Staff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self._flush.start()

    async def cog_unload(self):
        self._flush.cancel()
        sc.save_data(force=True)

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
                    if not member.bot and sc.is_staff(member):
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

    # ── Настройка и выдача ────────────────────────────────────────────────

    @app_commands.command(name="staff_config", description="Настроить лапки: сколько давать за верификацию")
    @app_commands.describe(coin="Сколько лапок за одну верификацию", currency="Название валюты стаффа")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staff_config(
        self,
        interaction: discord.Interaction,
        coin: app_commands.Range[int, 0, 1000] = None,
        currency: str = None,
    ):
        sc.set_settings(
            interaction.guild.id,
            verify_coin=coin,
            staff_currency=(currency[:20] if currency else None),
        )
        cur = sc.staff_currency(interaction.guild.id)
        await interaction.response.send_message(
            embed=_embed(
                "⚙️ Настройки стаффа",
                f"За верификацию: **{_fmt(sc.coin_per_verify(interaction.guild.id))} {cur}**",
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
