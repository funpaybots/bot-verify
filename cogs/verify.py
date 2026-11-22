# -*- coding: utf-8 -*-
"""Верификация: /verify @user — хелпер видит ephemeral с кнопками Девушка/Парень/Недопуск."""

import datetime
import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save

SETTINGS_FILE = "settings.json"
LOG_FILE = "verify_log.json"

DEFAULT_MALE_ID   = 1573422846485594193  # ♂
DEFAULT_FEMALE_ID = 1573422843574886453  # ♀

STAFF_ROLE_IDS = [
    1573422779376865280,  # Хелпер
    1573422776919007424,  # Модератор
    1573680792679489556,  # Старший модератор
    1573422768421077063,  # Администратор
    1573680796475195482,  # Главный администратор
    1573422762205257763,  # Владелец
    1573680800233426984,  # Владелица
]


def _is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


def _already_verified(member: discord.Member, male_id: int, female_id: int) -> bool:
    return any(r.id in (male_id, female_id) for r in member.roles)


def _get_ids(guild_id: int):
    cfg = load(SETTINGS_FILE, {}).get(str(guild_id), {})
    return (
        int(cfg.get("role_male",   DEFAULT_MALE_ID)),
        int(cfg.get("role_female", DEFAULT_FEMALE_ID)),
        cfg.get("log_channel_id"),
    )


def _make_embed(member: discord.Member, voice_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Верификация",
        color=discord.Color.from_str("#a9c9ff"),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🐾 Участник", value=f"{member.mention}", inline=False)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Проходка", value=voice_name, inline=True)
    return embed


class VerifyView(discord.ui.View):
    def __init__(self, member: discord.Member, verifier: discord.Member):
        super().__init__(timeout=300)
        self.member = member
        self.verifier = verifier
        self.done = False

    async def _apply(
        self,
        interaction: discord.Interaction,
        gender: str | None,  # 'male', 'female', None=reject
    ):
        if self.done:
            await interaction.response.defer()
            return
        self.done = True
        self.stop()

        guild = interaction.guild
        male_id, female_id, log_ch_id = _get_ids(guild.id)

        if gender is None:
            # Недопуск
            result_embed = discord.Embed(
                title="❌ Недопуск",
                description=f"{self.member.mention} получил отказ от {interaction.user.mention}",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(),
            )
            result_embed.set_thumbnail(url=self.member.display_avatar.url)
            await interaction.response.edit_message(embed=result_embed, view=None)
        else:
            role_id = male_id if gender == "male" else female_id
            role = guild.get_role(role_id)
            symbol = "♂ Парень" if gender == "male" else "♀ Девушка"

            if not role:
                await interaction.response.edit_message(
                    content=f"Роль не найдена (id={role_id}).", embed=None, view=None
                )
                return

            try:
                await self.member.add_roles(role, reason=f"Верификация от {interaction.user}")
            except discord.Forbidden:
                await interaction.response.edit_message(
                    content="Нет прав выдавать роль — проверь позицию роли бота.", embed=None, view=None
                )
                return

            # Лог в файл
            log = load(LOG_FILE, {})
            log[str(self.member.id)] = {
                "verified_by": str(interaction.user),
                "verified_by_id": interaction.user.id,
                "gender": gender,
                "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            }
            save(LOG_FILE, log)

            result_embed = discord.Embed(
                title=f"✅ Верифицирован как {symbol}",
                description=f"{self.member.mention} верифицирован {interaction.user.mention}",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now(),
            )
            result_embed.set_thumbnail(url=self.member.display_avatar.url)
            result_embed.add_field(name="ID", value=str(self.member.id), inline=True)
            result_embed.add_field(name="Роль", value=role.mention, inline=True)
            await interaction.response.edit_message(embed=result_embed, view=None)

            # Лог в канал
            if log_ch_id:
                log_ch = guild.get_channel(int(log_ch_id))
                if log_ch:
                    await log_ch.send(embed=result_embed)

    @discord.ui.button(label="Девушка", style=discord.ButtonStyle.primary, emoji="🐾")
    async def female(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        await self._apply(interaction, "female")

    @discord.ui.button(label="Парень", style=discord.ButtonStyle.primary, emoji="🐾")
    async def male(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        await self._apply(interaction, "male")

    @discord.ui.button(label="Недопуск", style=discord.ButtonStyle.danger, emoji="✖️")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        await self._apply(interaction, None)

    async def on_timeout(self):
        self.done = True


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verify", description="Верифицировать участника (только стафф)")
    @app_commands.describe(member="Участник которого верифицируем")
    async def verify(self, interaction: discord.Interaction, member: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return

        male_id, female_id, _ = _get_ids(interaction.guild.id)

        if _already_verified(member, male_id, female_id):
            await interaction.response.send_message(
                f"{member.mention} уже верифицирован.", ephemeral=True
            )
            return

        # Найти голосовой канал участника
        voice_name = "🔇 · не в войсе"
        if member.voice and member.voice.channel:
            voice_name = f"🔊 · {member.voice.channel.name}"

        embed = _make_embed(member, voice_name)
        view = VerifyView(member=member, verifier=interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="unverify", description="Снять верификацию (только стафф)")
    @app_commands.describe(member="Участник")
    async def unverify(self, interaction: discord.Interaction, member: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return

        male_id, female_id, _ = _get_ids(interaction.guild.id)
        roles_to_remove = [r for r in member.roles if r.id in (male_id, female_id)]
        if not roles_to_remove:
            await interaction.response.send_message(
                f"{member.mention} не верифицирован.", ephemeral=True
            )
            return

        await member.remove_roles(*roles_to_remove, reason=f"Снятие верификации от {interaction.user}")
        await interaction.response.send_message(
            f"✅ Верификация снята с {member.mention}.", ephemeral=True
        )

    @app_commands.command(name="verify_stats", description="Статистика верификаций")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def verify_stats(self, interaction: discord.Interaction):
        log = load(LOG_FILE, {})
        total  = len(log)
        male   = sum(1 for v in log.values() if v.get("gender") == "male")
        female = sum(1 for v in log.values() if v.get("gender") == "female")
        embed = discord.Embed(title="📊 Статистика верификаций", color=discord.Color.from_str("#a9c9ff"))
        embed.add_field(name="Всего", value=str(total),  inline=True)
        embed.add_field(name="♂",     value=str(male),   inline=True)
        embed.add_field(name="♀",     value=str(female), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setup_verify", description="Настроить роли и канал логов верификации")
    @app_commands.describe(
        log_channel="Канал для логов верификаций",
        role_male="Роль ♂",
        role_female="Роль ♀",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel,
        role_male: discord.Role = None,
        role_female: discord.Role = None,
    ):
        data = load(SETTINGS_FILE, {})
        cfg = data.setdefault(str(interaction.guild.id), {})
        cfg["log_channel_id"] = log_channel.id
        if role_male:
            cfg["role_male"] = role_male.id
        if role_female:
            cfg["role_female"] = role_female.id
        save(SETTINGS_FILE, data)

        embed = discord.Embed(title="✅ Верификация настроена", color=discord.Color.green())
        embed.add_field(name="Логи", value=log_channel.mention, inline=True)
        if role_male:
            embed.add_field(name="♂", value=role_male.mention, inline=True)
        if role_female:
            embed.add_field(name="♀", value=role_female.mention, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))
