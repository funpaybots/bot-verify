# -*- coding: utf-8 -*-
import logging
from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

import storage

logger = logging.getLogger(__name__)

# --- Дефолтные id ролей сервера Roet ---
DEFAULT_ROLE_MALE   = 1573422846485594193
DEFAULT_ROLE_FEMALE = 1573422843574886453

STAFF_ROLE_IDS = [
    1573422779376865280,  # Хелпер
    1573422776919007424,  # Модератор
    1573680792679489556,  # Старший модератор
    1573422768421077063,  # Администратор
    1573680796475195482,  # Главный администратор
    1573422762205257763,  # Владелец
    1573680800233426984,  # Владелица
]


def _settings() -> dict:
    return storage.load("settings.json", {})


def _pending() -> dict:
    return storage.load("pending.json", {})


def _is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    member_role_ids = {r.id for r in member.roles}
    return bool(member_role_ids & set(STAFF_ROLE_IDS))


def _get_role_id(settings: dict, key: str, default: int) -> int:
    return int(settings.get(key, default))


# ---------------------------------------------------------------------------
# Persistent Views
# ---------------------------------------------------------------------------

class ApplyView(discord.ui.View):
    """Вью с кнопкой «Подать заявку» — постоянная, висит в канале."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Подать заявку 📋",
        style=discord.ButtonStyle.primary,
        custom_id="verify_apply",
    )
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = _settings()
        member = interaction.user

        male_id   = _get_role_id(settings, "role_male",   DEFAULT_ROLE_MALE)
        female_id = _get_role_id(settings, "role_female", DEFAULT_ROLE_FEMALE)
        member_role_ids = {r.id for r in member.roles}

        if male_id in member_role_ids or female_id in member_role_ids:
            await interaction.response.send_message(
                "Вы уже верифицированы.", ephemeral=True
            )
            return

        # Канал для заявок — берём из settings или текущий
        verify_channel_id = settings.get("verify_channel_id") or interaction.channel_id
        channel = interaction.guild.get_channel(int(verify_channel_id))
        if channel is None:
            await interaction.response.send_message(
                "Канал верификации не найден. Обратитесь к администратору.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        created_at = member.created_at.replace(tzinfo=timezone.utc)
        created_str = discord.utils.format_dt(created_at, style="D")

        embed = discord.Embed(
            title="Заявка на верификацию",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Ник", value=member.display_name, inline=True)
        embed.add_field(name="Username", value=str(member), inline=True)
        embed.add_field(name="Аккаунт создан", value=created_str, inline=False)
        embed.set_footer(text=f"ID: {member.id}")

        view = ReviewView(user_id=member.id)
        msg = await channel.send(embed=embed, view=view)

        pending = _pending()
        pending[str(msg.id)] = {
            "user_id": member.id,
            "guild_id": interaction.guild_id,
            "timestamp": created_at.isoformat(),
        }
        storage.save("pending.json", pending)

        await interaction.followup.send(
            "Заявка подана! Дождитесь решения хелпера.", ephemeral=True
        )


class ReviewView(discord.ui.View):
    """Вью с кнопками ♂ / ♀ / ❌ — persistent по user_id."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        uid = str(user_id)
        self.add_item(ReviewButton("♂ Парень",  discord.ButtonStyle.primary, f"verify_male_{uid}"))
        self.add_item(ReviewButton("♀ Девушка", discord.ButtonStyle.danger,  f"verify_female_{uid}"))
        self.add_item(ReviewButton("❌ Отклонить", discord.ButtonStyle.secondary, f"verify_reject_{uid}"))


class ReviewButton(discord.ui.Button):
    def __init__(self, label: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        cid: str = self.custom_id  # verify_male_123 / verify_female_123 / verify_reject_123

        parts = cid.split("_", 2)  # ["verify", "male"|"female"|"reject", "user_id"]
        if len(parts) != 3:
            await interaction.response.send_message("Неверный custom_id.", ephemeral=True)
            return

        action  = parts[1]   # "male" | "female" | "reject"
        user_id = int(parts[2])

        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "У вас недостаточно прав для этого действия.", ephemeral=True
            )
            return

        await interaction.response.defer()

        settings  = _settings()
        guild     = interaction.guild
        member    = guild.get_member(user_id)

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        disabled_view = discord.ui.View()  # пустая вью — убирает кнопки

        if action == "reject":
            embed.color = discord.Color.red()
            embed.add_field(
                name="Решение",
                value=f"❌ Заявка отклонена хелпером {interaction.user.mention}",
                inline=False,
            )
            await interaction.message.edit(embed=embed, view=disabled_view)
        else:
            if member is None:
                embed.color = discord.Color.orange()
                embed.add_field(
                    name="Ошибка",
                    value="Пользователь покинул сервер.",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=disabled_view)
            else:
                if action == "male":
                    role_id  = _get_role_id(settings, "role_male",   DEFAULT_ROLE_MALE)
                    gender_label = "♂"
                else:
                    role_id  = _get_role_id(settings, "role_female", DEFAULT_ROLE_FEMALE)
                    gender_label = "♀"

                role = guild.get_role(role_id)
                if role is None:
                    # Fallback: ищем по имени
                    name_map = {"♂": "♂", "♀": "♀"}
                    role = discord.utils.get(guild.roles, name=name_map[gender_label])

                if role:
                    try:
                        await member.add_roles(role, reason=f"Верификация хелпером {interaction.user}")
                    except discord.Forbidden:
                        logger.warning("Нет прав выдать роль %s участнику %s", role, member)

                embed.color = discord.Color.green()
                embed.add_field(
                    name="Решение",
                    value=f"✅ Верифицирован как {gender_label} хелпером {interaction.user.mention}",
                    inline=False,
                )
                await interaction.message.edit(embed=embed, view=disabled_view)

        # Удаляем из pending
        pending = _pending()
        msg_id  = str(interaction.message.id)
        if msg_id in pending:
            del pending[msg_id]
            storage.save("pending.json", pending)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Регистрируем persistent views при создании кога
        self.bot.add_view(ApplyView())
        # Восстанавливаем ReviewView для всех pending заявок
        for msg_id, info in _pending().items():
            self.bot.add_view(ReviewView(user_id=info["user_id"]))

    @app_commands.command(
        name="setup_verify",
        description="Настройка верификации — постит embed с кнопкой в текущий канал",
    )
    @app_commands.describe(
        verify_channel="Канал куда приходят заявки (по умолчанию — текущий)",
        role_male="ID роли ♂",
        role_female="ID роли ♀",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: discord.Interaction,
        verify_channel: discord.TextChannel = None,
        role_male: str = None,
        role_female: str = None,
    ):
        settings = _settings()
        settings["apply_channel_id"] = interaction.channel_id

        if verify_channel:
            settings["verify_channel_id"] = verify_channel.id
        else:
            settings.setdefault("verify_channel_id", interaction.channel_id)

        if role_male:
            settings["role_male"] = int(role_male)
        if role_female:
            settings["role_female"] = int(role_female)

        storage.save("settings.json", settings)

        embed = discord.Embed(
            title="Верификация",
            description="Для получения доступа к серверу нажмите кнопку ниже и дождитесь хелпера.",
            color=discord.Color.blurple(),
        )
        view = ApplyView()
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            "Embed верификации опубликован.", ephemeral=True
        )

    @setup_verify.error
    async def setup_verify_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Нет прав. Нужны права администратора.", ephemeral=True
            )
        else:
            raise error

    @app_commands.command(
        name="verify_stats",
        description="Показывает количество ожидающих верификации",
    )
    async def verify_stats(self, interaction: discord.Interaction):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "У вас недостаточно прав.", ephemeral=True
            )
            return

        pending = _pending()
        count   = len(pending)
        await interaction.response.send_message(
            f"⏳ Ожидают верификации: **{count}**", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))
