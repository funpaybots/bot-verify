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


class RejectModal(discord.ui.Modal, title="Причина недопуска"):
    reason = discord.ui.TextInput(
        label="Причина",
        placeholder="например: не ответил, подозрительный аккаунт...",
        min_length=1,
        max_length=200,
    )

    def __init__(self, view: "VerifyView"):
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.parent_view._apply(interaction, None, reason=self.reason.value)


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
        reason: str = "",
    ):
        if self.done:
            return
        self.done = True
        self.stop()

        guild = interaction.guild
        male_id, female_id, log_ch_id = _get_ids(guild.id)

        # Определяем метод ответа: из модалки уже сделан defer → followup
        # Из кнопки — ещё не отвечали → edit_message
        responded = interaction.response.is_done()

        async def edit(embed):
            if responded:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.edit_message(embed=embed, view=None)

        if gender is None:
            # Недопуск
            result_embed = discord.Embed(
                title="❌ Недопуск",
                description=f"{self.member.mention} получил отказ от {interaction.user.mention}",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(),
            )
            result_embed.set_thumbnail(url=self.member.display_avatar.url)
            if reason:
                result_embed.add_field(name="Причина", value=reason, inline=False)
            result_embed.add_field(name="ID", value=str(self.member.id), inline=True)
            await edit(result_embed)

            # Бан участника
            try:
                await guild.ban(self.member, reason=f"Недопуск: {reason}", delete_message_days=0)
            except discord.Forbidden:
                pass

            # Лог в канал
            if log_ch_id:
                log_ch = guild.get_channel(int(log_ch_id))
                if log_ch:
                    await log_ch.send(embed=result_embed)
        else:
            role_id = male_id if gender == "male" else female_id
            role = guild.get_role(role_id)
            symbol = "♂ Парень" if gender == "male" else "♀ Девушка"

            if not role:
                if responded:
                    await interaction.followup.send(f"Роль не найдена (id={role_id}).", ephemeral=True)
                else:
                    await interaction.response.edit_message(
                        content=f"Роль не найдена (id={role_id}).", embed=None, view=None
                    )
                return

            try:
                await self.member.add_roles(role, reason=f"Верификация от {interaction.user}")
            except discord.Forbidden:
                if responded:
                    await interaction.followup.send("Нет прав выдавать роль.", ephemeral=True)
                else:
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
            await edit(result_embed)

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
        await interaction.response.send_modal(RejectModal(self))

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


class CallHelperView(discord.ui.View):
    """Persistent кнопка «Вызвать Хелпера» в канале знакомства."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Вызвать Хелпера",
        style=discord.ButtonStyle.primary,
        emoji="🐾",
        custom_id="call_helper",
    )
    async def call_helper(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user

        # Не даём спамить — проверяем уже верифицирован ли
        male_id, female_id, log_ch_id = _get_ids(interaction.guild.id)
        if _already_verified(member, male_id, female_id):
            await interaction.response.send_message(
                "Ты уже верифицирован 🐾", ephemeral=True
            )
            return

        # Найти голосовой канал
        voice_name = "не в войсе"
        if member.voice and member.voice.channel:
            voice_name = member.voice.channel.name

        # Пинг хелперов в канал логов
        cfg = load(SETTINGS_FILE, {}).get(str(interaction.guild.id), {})
        log_ch_id_int = cfg.get("log_channel_id")
        staff_ch_id   = cfg.get("staff_channel_id") or log_ch_id_int

        embed = discord.Embed(
            title="🔔 Нужна верификация",
            description=f"{member.mention} ждёт хелпера",
            color=discord.Color.from_str("#a9c9ff"),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Участник", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Проходка", value=voice_name, inline=True)

        # Найти роль хелпера для пинга
        helper_role = discord.utils.get(interaction.guild.roles, name="Хелпер")
        ping = helper_role.mention if helper_role else "@Хелпер"

        if staff_ch_id:
            ch = interaction.guild.get_channel(int(staff_ch_id))
            if ch:
                await ch.send(content=ping, embed=embed)

        await interaction.response.send_message(
            "Позвал хелпера — жди в проходке 🐾", ephemeral=True
        )


async def setup(bot: commands.Bot):
    bot.add_view(CallHelperView())
    await bot.add_cog(Verify(bot))
