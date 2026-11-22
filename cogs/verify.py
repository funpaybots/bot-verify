# -*- coding: utf-8 -*-
"""Верификация: хелпер вызывает /verify @user male/female — бот выдаёт роль."""

import datetime
import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save

SETTINGS_FILE = "settings.json"
LOG_FILE = "verify_log.json"

# Роли по умолчанию (сервер Roet)
DEFAULT_MALE_ID   = 1573422846485594193  # ♂
DEFAULT_FEMALE_ID = 1573422843574886453  # ♀

# Роли, которым разрешено верифицировать
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


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="verify", description="Верифицировать участника (только стафф)")
    @app_commands.describe(
        member="Участник которого верифицируем",
        gender="Пол: male (♂) или female (♀)",
    )
    @app_commands.choices(gender=[
        app_commands.Choice(name="♂ Парень", value="male"),
        app_commands.Choice(name="♀ Девушка", value="female"),
    ])
    async def verify(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        gender: app_commands.Choice[str],
    ):
        # Проверка прав
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return

        # Получить id ролей из настроек или дефолт
        cfg = load(SETTINGS_FILE, {}).get(str(interaction.guild.id), {})
        male_id   = int(cfg.get("role_male",   DEFAULT_MALE_ID))
        female_id = int(cfg.get("role_female", DEFAULT_FEMALE_ID))
        log_ch_id = cfg.get("log_channel_id")

        # Уже верифицирован?
        if _already_verified(member, male_id, female_id):
            await interaction.response.send_message(
                f"{member.mention} уже верифицирован.", ephemeral=True
            )
            return

        # Выдать роль
        role_id = male_id if gender.value == "male" else female_id
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(
                f"Роль не найдена (id={role_id}). Проверь /setup_verify.", ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason=f"Верификация от {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Нет прав выдавать роль. Проверь позицию роли бота.", ephemeral=True
            )
            return

        symbol = "♂" if gender.value == "male" else "♀"

        # Лог в файл
        log = load(LOG_FILE, {})
        log[str(member.id)] = {
            "verified_by": str(interaction.user),
            "verified_by_id": interaction.user.id,
            "gender": gender.value,
            "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        save(LOG_FILE, log)

        # Ответ хелперу
        embed = discord.Embed(
            title="✅ Верификация",
            description=f"{member.mention} верифицирован как **{symbol}**",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Хелпер", value=interaction.user.mention, inline=True)
        embed.add_field(name="Роль", value=role.mention, inline=True)
        await interaction.response.send_message(embed=embed)

        # Лог в канал
        if log_ch_id:
            log_ch = interaction.guild.get_channel(int(log_ch_id))
            if log_ch:
                await log_ch.send(embed=embed)

    @app_commands.command(name="unverify", description="Снять верификацию (только стафф)")
    @app_commands.describe(member="Участник")
    async def unverify(self, interaction: discord.Interaction, member: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return

        cfg = load(SETTINGS_FILE, {}).get(str(interaction.guild.id), {})
        male_id   = int(cfg.get("role_male",   DEFAULT_MALE_ID))
        female_id = int(cfg.get("role_female", DEFAULT_FEMALE_ID))

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
        total = len(log)
        male   = sum(1 for v in log.values() if v.get("gender") == "male")
        female = sum(1 for v in log.values() if v.get("gender") == "female")
        embed = discord.Embed(
            title="📊 Статистика верификаций",
            color=discord.Color.from_str("#a9c9ff"),
        )
        embed.add_field(name="Всего", value=str(total), inline=True)
        embed.add_field(name="♂", value=str(male), inline=True)
        embed.add_field(name="♀", value=str(female), inline=True)
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
