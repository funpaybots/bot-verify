# -*- coding: utf-8 -*-
"""Верификация: /verify @user — хелпер видит ephemeral с кнопками Девушка/Парень/Недопуск."""

import datetime
import difflib
import re

import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save
from version import BUILD
from cogs.staff_core import (
    STAFF_ROLE_IDS,
    add_verify as add_verify_credit,
    dayoff_until_str,
    is_passage as is_passage_channel,
    on_dayoff,
    staff_currency,
    top as staff_top,
)

SETTINGS_FILE = "settings.json"
LOG_FILE = "verify_log.json"

DEFAULT_MALE_ID   = 1573422846485594193  # ♂
DEFAULT_FEMALE_ID = 1573422843574886453  # ♀


def _is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


def _already_verified(member: discord.Member, male_id: int, female_id: int) -> bool:
    return any(r.id in (male_id, female_id) for r in member.roles)


def _dayoff_block(interaction: discord.Interaction) -> str:
    """Текст отказа, если хелпер в отгуле. Пустая строка — можно работать."""
    guild = interaction.guild
    if guild is None or interaction.user is None:
        return ""
    until = dayoff_until_str(guild.id, interaction.user.id)
    if until:
        return (
            f"🌴 Ты в отгуле до **{until}** — верификации закрыты.\n"
            "Сидеть в проходке можно, но смена не капает."
        )
    return ""


def _get_ids(guild_id: int):
    cfg = load(SETTINGS_FILE, {}).get(str(guild_id), {})
    return (
        int(cfg.get("role_male",   DEFAULT_MALE_ID)),
        int(cfg.get("role_female", DEFAULT_FEMALE_ID)),
        cfg.get("log_channel_id"),
    )


# ── Неверифицированные и мут в проходке ───────────────────────────────────────

UNVERIFIED_ROLE_NAME = "🔒 Не верифицирован"
UNVERIFIED_NAME_HINTS = ("не верифицирован", "неверифицирован", "unverified", "анвереф")

# Кого бот сам замутил: {(guild_id, user_id)} — чтобы снимать мут только за собой.
# Храним на диске: после рестарта память не должна теряться, иначе старые
# глобальные мьюты снять уже нечем.
MUTED_FILE = "muted.json"


def _load_muted() -> set:
    raw = load(MUTED_FILE, {})
    pairs = set()
    items = raw.get("pairs", []) if isinstance(raw, dict) else []
    for item in items or []:
        try:
            pairs.add((int(item[0]), int(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return pairs


def _save_muted() -> None:
    save(MUTED_FILE, {"pairs": [list(key) for key in sorted(_MUTED_BY_BOT)]})


_MUTED_BY_BOT = _load_muted()


def _unverified_role_id(guild_id: int) -> int:
    return int(load(SETTINGS_FILE, {}).get(str(guild_id), {}).get("unverified_role_id", 0) or 0)


def _find_unverified_role(guild: discord.Guild):
    role_id = _unverified_role_id(guild.id)
    if role_id:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    for role in guild.roles:
        name = role.name.lower()
        if any(h in name for h in UNVERIFIED_NAME_HINTS):
            return role
    return None


async def _ensure_unverified_role(guild: discord.Guild):
    """Роль новичка. Нет — создаём и запоминаем в настройках."""
    role = _find_unverified_role(guild)
    if role is not None:
        return role
    try:
        role = await guild.create_role(
            name=UNVERIFIED_ROLE_NAME,
            colour=discord.Colour.dark_gray(),
            permissions=discord.Permissions.none(),
            hoist=False,
            mentionable=False,
            reason="Роль новичка до верификации",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None
    data = load(SETTINGS_FILE, {})
    data.setdefault(str(guild.id), {})["unverified_role_id"] = role.id
    save(SETTINGS_FILE, data)
    return role


def _has_unverified(member: discord.Member) -> bool:
    role = _find_unverified_role(member.guild)
    if role is not None:
        return role in member.roles
    male_id, female_id, _ = _get_ids(member.guild.id)
    return not _already_verified(member, male_id, female_id)


def _needs_passage_mute(member: discord.Member) -> bool:
    """Новичкам в проходке закрываем микрофон. Стафф и боты — не трогаем."""
    if member.bot or _is_staff(member):
        return False
    return _has_unverified(member)


def _passage_voice_channels(guild):
    """Все войсы-проходки сервера."""
    return [ch for ch in getattr(guild, "voice_channels", []) if is_passage_channel(ch)]


async def _set_passage_speak(member: discord.Member, allow: bool, reason: str) -> bool:
    """Закрывает (allow=False) или открывает микрофон только в проходках.

    Права остальных войсов не трогаем: человек сидит в проходке — молчит,
    перешёл в обычный войс — говорит как все. True, если хоть один канал обработан.
    """
    target = None if allow else False
    applied = False
    for channel in _passage_voice_channels(member.guild):
        try:
            overwrite = channel.overwrites_for(member)
            if overwrite.speak != target:
                overwrite.speak = target
                await channel.set_permissions(member, overwrite=overwrite, reason=reason)
            applied = True
        except (discord.Forbidden, discord.HTTPException):
            continue
    return applied


async def _clear_global_mute(
    member: discord.Member,
    reason: str = "Глобальный мьют снят: мут только в проходке",
) -> bool:
    """Снимает серверный мут. Работает и когда человек не сидит в войсе."""
    try:
        await member.edit(mute=False, reason=reason)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def _mute_in_passage(member: discord.Member, reason: str = "Проходка: микрофон закрыт до верификации"):
    if not await _set_passage_speak(member, allow=False, reason=reason):
        return False
    _MUTED_BY_BOT.add((member.guild.id, member.id))
    _save_muted()
    return True


async def _unmute_guest(member: discord.Member, reason: str = "Мут в проходке снят"):
    key = (member.guild.id, member.id)
    applied = await _set_passage_speak(member, allow=True, reason=reason)
    # Совместимость: если человека успели глобально замутить старой версией — снимаем только за собой
    if key in _MUTED_BY_BOT:
        if await _clear_global_mute(member, reason):
            applied = True
        _MUTED_BY_BOT.discard(key)
        _save_muted()
    return applied


# ── Карта сервера ─────────────────────────────────────────────────────────────

MAP_SECTIONS = [
    (
        "Твой уютный маршрут",
        [
            ("котекст", "📖", "бережно друг к другу"),
            ("нет-грустна", "🏡", "знакомится"),
            ("мои-роли", "🎀", "интересы и настроение"),
            ("наша-команда", "🧸", "кто заботится о доме"),
        ],
    ),
    (
        "Поболтаем?",
        [
            ("общение", "💬", "обо всём"),
            ("смешинки", "😹", "улыбнуться"),
            ("селфи", "🤳", "твой сельфи"),
            ("ищу-компанию", "👥", "найти компанию"),
            ("знакомства", "💞", "узнать друг друга"),
            ("питомцы", "🐾", "твои питомцы"),
            ("прогулки", "🏞", "прогулки и впечатления"),
            ("рисунки", "🎨", "твои рисунки"),
            ("достижения", "🥇", "твои достижения"),
        ],
    ),
    (
        "Планы на вечер",
        [
            ("собираемся", "📅", "собираемся на игры и кино"),
            ("плеер", "🎧", "любимые треки"),
            ("корзина", "🧺", "печеньки и покупки"),
            ("Создать уголок", "➕", "свой уголок"),
            ("Войти", "🔴", "комната для пары"),
        ],
    ),
    (
        "Печеньки и маленькие радости",
        [
            ("незнакомство", "🌿", "личные роли и комнаты за печеньки"),
            ("спонсорские-пакеты", "🎁", "спонсорские пакеты"),
        ],
    ),
]

MAP_HELPER_LINE = (
    "С верификацией поможет <@&1573422779376865280>. "
    "Другие обращения — `/help` у Mod."
)


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]", "", s.lower())


# ── Подсказка хелперу: стоит ли впускать ─────────────────────────────────────
# Совет, а не запрет: последнее слово за хелпером. Опираемся на то, что
# реально видно через API, и на правила Discord:
#   • аккаунт слишком свежий — типичный обход бана или спам-волна;
#   • ник выдаёт себя за Discord/стафф — impersonation;
#   • ник рекламирует нитро/подарки/промокоды — скам;
#   • ссылка в нике — почти всегда развод.
# Возраст 13+ и содержимое аватарки через API не проверить — это на хелпере.

HINT_OK = "✅ Красных флагов не видно — можно впускать"
HINT_WARN = "⚠️ Есть вопросы — поговори в проходке"
HINT_RISK = "🛑 Похоже на нарушение правил — проверь внимательно"

HINT_CHECKLIST = "13+, не выдаёт себя за Discord/стафф, без скам-ссылок и NSFW."

# Выдача себя за Discord/стафф — нарушение ToS (impersonation).
IMPERSONATION_WORDS = (
    "discord", "дискорд", "support", "moder", "admin", "админ", "staff",
    "official", "официальн", "верификац",
)
# Скам-реклама в нике: нитро, подарки, промокоды.
SCAM_WORDS = (
    "nitro", "нитро", "free", "бесплат", "gift", "подар", "promo",
    "промокод", "airdrop", "giveaway", "розыгрыш",
)
# Ссылки в нике — почти всегда скам.
LINK_MARKERS = ("http", "www.", "discord.gg", "discord.gift", "t.me", ".ru/", ".com/", ".net/")


def _age_str(delta: datetime.timedelta) -> str:
    """Человеческий возраст: 12 мин. / 3 ч. / 5 дн. / 2 мес. / 1 г. 3 мес."""
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} мин."
    if seconds < 86400:
        return f"{seconds // 3600} ч."
    days = seconds // 86400
    if days < 30:
        return f"{days} дн."
    months = days // 30
    if months < 12:
        return f"{months} мес."
    years, rest = divmod(months, 12)
    return f"{years} г. {rest} мес." if rest else f"{years} г."


def _risk_hint(member, now: datetime.datetime | None = None):
    """(вердикт, причины) — подсказка по правилам Discord, без Discord-зависимостей."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    reasons = []
    score = 0

    created = getattr(member, "created_at", None)
    if created:
        days = max(0, (now - created).days)
        if days < 3:
            score += 2
            reasons.append(f"аккаунт совсем новый ({days} дн.) — возможен обход бана")
        elif days < 14:
            score += 1
            reasons.append(f"аккаунт младше двух недель ({days} дн.)")

    avatar = getattr(member, "display_avatar", None)
    try:
        no_avatar = bool(avatar is not None and avatar.is_default())
    except Exception:  # noqa: BLE001 - странный объект аватара не должен ронять карточку
        no_avatar = False
    if no_avatar:
        score += 1
        reasons.append("нет аватарки")

    # Смотрим и ник на сервере, и username: скам часто прячется в одном из них.
    names = []
    for attr in ("display_name", "name", "global_name"):
        value = str(getattr(member, attr, "") or "").strip().lower()
        if value and value not in names:
            names.append(value)
    joined = " ".join(names)

    if any(n.isdigit() for n in names):
        score += 1
        reasons.append("ник из одних цифр")

    impersonation = next((w for w in IMPERSONATION_WORDS if w in joined), "")
    if impersonation:
        score += 2
        reasons.append(f"ник косит под Discord/стафф: «{impersonation}»")

    scam = next((w for w in SCAM_WORDS if w in joined), "")
    if scam:
        score += 2
        reasons.append(f"скам-слово в нике: «{scam}»")

    link = next((m for m in LINK_MARKERS if m in joined), "")
    if link:
        score += 4
        reasons.append(f"ссылка в нике: «{link}»")

    if score >= 4:
        verdict = HINT_RISK
    elif score >= 2:
        verdict = HINT_WARN
    else:
        verdict = HINT_OK
        if not reasons:
            reasons.append("красных флагов не видно")
    return verdict, reasons


def _find_channel(guild: discord.Guild, pattern: str, taken: set):
    """Найти канал по примерному имени: точное → вхождение → похожесть."""
    target = _norm_name(pattern)
    if not target:
        return None
    channels = list(guild.text_channels) + list(guild.voice_channels)

    for ch in channels:
        if ch.id not in taken and _norm_name(ch.name) == target:
            return ch
    for ch in channels:
        if ch.id not in taken and target in _norm_name(ch.name):
            return ch
    best, best_ratio = None, 0.0
    for ch in channels:
        if ch.id in taken:
            continue
        ratio = difflib.SequenceMatcher(None, target, _norm_name(ch.name)).ratio()
        if ratio > best_ratio:
            best, best_ratio = ch, ratio
    return best if best_ratio >= 0.85 else None


def _make_embed(member: discord.Member, voice_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Верификация",
        color=discord.Color.from_str("#a9c9ff"),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🐾 Участник", value=f"{member.mention}", inline=False)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Проходка", value=voice_name, inline=True)

    created = getattr(member, "created_at", None)
    if created:
        embed.add_field(
            name="🗓 Аккаунт",
            value=_age_str(datetime.datetime.now(datetime.timezone.utc) - created),
            inline=True,
        )

    verdict, reasons = _risk_hint(member)
    hint = verdict + "\n" + "\n".join(f"• {r}" for r in reasons[:4])
    hint += f"\n\n_Сверить: {HINT_CHECKLIST}_"
    embed.add_field(name="🧭 Подсказка по правилам Discord", value=hint, inline=False)
    embed.set_footer(text=f"сборка {BUILD}")
    return embed


async def _safe_respond(interaction: discord.Interaction, embed=None, content=None) -> bool:
    """Ответ на нажатие даже если токен интеракции уже протух (10062)."""
    if not interaction.response.is_done():
        try:
            await interaction.response.edit_message(embed=embed, content=content, view=None)
            return True
        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass
    try:
        await interaction.followup.send(embed=embed, content=content, ephemeral=True)
        return True
    except discord.HTTPException:
        pass
    message = getattr(interaction, "message", None)
    if message is not None:
        try:
            await message.edit(embed=embed, content=content, view=None)
            return True
        except discord.HTTPException:
            pass
    return False


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
        # 15 минут — столько живёт токен интеракции; раньше кнопки умирали через 5
        super().__init__(timeout=900)
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
            # Кто-то уже нажал (или двойной клик) — отвечаем, чтобы у человека не висело «interaction failed»
            await _safe_respond(interaction, content="Заявка уже обработана.")
            return
        self.done = True
        self.stop()

        guild = interaction.guild
        male_id, female_id, log_ch_id = _get_ids(guild.id)

        async def edit(embed):
            await _safe_respond(interaction, embed=embed)

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
                await _safe_respond(interaction, content=f"Роль не найдена (id={role_id}).")
                return

            try:
                await self.member.add_roles(role, reason=f"Верификация от {interaction.user}")
            except discord.Forbidden:
                await _safe_respond(
                    interaction, content="Нет прав выдавать роль — проверь позицию роли бота."
                )
                return

            # Снимаем роль новичка и мут в проходке
            uv_role = _find_unverified_role(guild)
            if uv_role is not None and uv_role in self.member.roles:
                try:
                    await self.member.remove_roles(uv_role, reason="Верифицирован")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if (guild.id, self.member.id) in _MUTED_BY_BOT:
                await _unmute_guest(self.member, "Верифицирован")

            # Лог в файл
            log = load(LOG_FILE, {})
            log[str(self.member.id)] = {
                "verified_by": str(interaction.user),
                "verified_by_id": interaction.user.id,
                "gender": gender,
                "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            }
            save(LOG_FILE, log)

            # Лапки стаффу за верификацию
            coins, coins_total = add_verify_credit(guild.id, interaction.user.id)
            cur = staff_currency(guild.id)

            result_embed = discord.Embed(
                title=f"✅ Верифицирован как {symbol}",
                description=(
                    f"{self.member.mention} верифицирован {interaction.user.mention}\n\n"
                    f"🐾 **+{coins}** {cur} за верификацию · всего **{coins_total}**"
                ),
                color=discord.Color.green(),
                timestamp=datetime.datetime.now(),
            )
            result_embed.set_thumbnail(url=self.member.display_avatar.url)
            result_embed.add_field(name="ID", value=str(self.member.id), inline=True)
            result_embed.add_field(name="Роль", value=role.mention, inline=True)
            result_embed.add_field(name="Хелпер", value=interaction.user.mention, inline=True)
            await edit(result_embed)

            # Лог в канал с кнопкой изменить роль
            if log_ch_id:
                log_ch = guild.get_channel(int(log_ch_id))
                if log_ch:
                    await log_ch.send(
                        embed=result_embed,
                        view=ChangeRoleView(self.member, male_id, female_id),
                    )

    @discord.ui.button(label="Девушка", style=discord.ButtonStyle.primary, emoji="🐾")
    async def female(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return
        await self._apply(interaction, "female")

    @discord.ui.button(label="Парень", style=discord.ButtonStyle.primary, emoji="🐾")
    async def male(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return
        await self._apply(interaction, "male")

    @discord.ui.button(label="Недопуск", style=discord.ButtonStyle.danger, emoji="✖️")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return
        await interaction.response.send_modal(RejectModal(self))

    async def on_timeout(self):
        self.done = True


class BanModal(discord.ui.Modal, title="Причина бана"):
    reason = discord.ui.TextInput(
        label="Причина",
        placeholder="например: спам, нарушение правил...",
        min_length=1,
        max_length=200,
    )

    def __init__(self, member: discord.Member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = guild.get_member(self.member.id)
        try:
            target = member or discord.Object(id=self.member.id)
            await guild.ban(target, reason=f"Бан от {interaction.user}: {self.reason.value}", delete_message_days=0)
        except discord.Forbidden:
            await interaction.response.send_message("Нет прав для бана.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🔨 Забанен",
            description=f"<@{self.member.id}> забанен {interaction.user.mention}",
            color=discord.Color.dark_red(),
            timestamp=datetime.datetime.now(),
        )
        embed.add_field(name="Причина", value=self.reason.value, inline=False)
        embed.add_field(name="ID", value=str(self.member.id), inline=True)
        await interaction.response.send_message(embed=embed)


class ChangeRoleView(discord.ui.View):
    """Кнопки изменения роли в лог-канале."""

    def __init__(self, member: discord.Member, male_id: int, female_id: int):
        super().__init__(timeout=None)
        self.member = member
        self.male_id = male_id
        self.female_id = female_id

    @discord.ui.button(label="Сменить на ♀", style=discord.ButtonStyle.primary, emoji="🐾")
    async def set_female(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        await self._change(interaction, self.female_id, self.male_id, "♀")

    @discord.ui.button(label="Сменить на ♂", style=discord.ButtonStyle.primary, emoji="🐾")
    async def set_male(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        await self._change(interaction, self.male_id, self.female_id, "♂")

    @discord.ui.button(label="🔨 Бан", style=discord.ButtonStyle.danger)
    async def ban_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Только администраторы и выше
        admin_ids = [
            1573422768421077063,  # Администратор
            1573680796475195482,  # Главный администратор
            1573422762205257763,  # Владелец
            1573680800233426984,  # Владелица
        ]
        is_admin = interaction.user.guild_permissions.administrator or any(
            r.id in admin_ids for r in interaction.user.roles
        )
        if not is_admin:
            await interaction.response.send_message("Только администраторы могут банить.", ephemeral=True)
            return
        await interaction.response.send_modal(BanModal(self.member))

    async def _change(self, interaction: discord.Interaction, give_id: int, remove_id: int, symbol: str):
        guild = interaction.guild
        member = guild.get_member(self.member.id)
        if not member:
            await interaction.response.send_message("Участник покинул сервер.", ephemeral=True)
            return
        give_role   = guild.get_role(give_id)
        remove_role = guild.get_role(remove_id)
        try:
            if remove_role and remove_role in member.roles:
                await member.remove_roles(remove_role, reason=f"Смена роли от {interaction.user}")
            if give_role:
                await member.add_roles(give_role, reason=f"Смена роли от {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("Нет прав изменять роли.", ephemeral=True)
            return
        # Обновить embed
        old_embed = interaction.message.embeds[0] if interaction.message.embeds else None
        new_embed = discord.Embed(
            title=f"✅ Верифицирован как {symbol}",
            description=old_embed.description if old_embed else member.mention,
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(),
        )
        new_embed.set_thumbnail(url=member.display_avatar.url)
        new_embed.add_field(name="ID", value=str(member.id), inline=True)
        new_embed.add_field(name="Роль", value=give_role.mention if give_role else symbol, inline=True)
        new_embed.add_field(name="Изменил", value=interaction.user.mention, inline=True)
        await interaction.response.edit_message(embed=new_embed, view=self)


class PickMemberView(discord.ui.View):
    """Временный дропдаун с актуальным списком гостей."""

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Выбери гостя из проходки...",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        member_id = int(interaction.data["values"][0])
        member = interaction.guild.get_member(member_id)
        if not member:
            await interaction.response.send_message("Участник не найден.", ephemeral=True)
            return
        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return
        male_id, female_id, _ = _get_ids(interaction.guild.id)
        if _already_verified(member, male_id, female_id):
            await interaction.response.send_message(f"{member.mention} уже верифицирован.", ephemeral=True)
            return
        voice_name = "🔇 · не в войсе"
        if member.voice and member.voice.channel:
            voice_name = f"🔊 · {member.voice.channel.name}"
        embed = _make_embed(member, voice_name)
        view = VerifyView(member=member, verifier=interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        self.stop()


class VerifyPanelView(discord.ui.View):
    """Persistent панель верификации для стаффа: дропдаун + кнопка Пинг."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Пинг",
        style=discord.ButtonStyle.secondary,
        emoji="🔔",
        custom_id="verify_panel_ping",
    )
    async def ping(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        male_id, female_id, _ = _get_ids(interaction.guild.id)
        waiting = []
        for vc in interaction.guild.voice_channels:
            if "роходка" in vc.name or "проходка" in vc.name.lower():
                for m in vc.members:
                    if not _already_verified(m, male_id, female_id):
                        waiting.append(f"{m.mention} — {vc.name}")
        if not waiting:
            await interaction.response.send_message("Нет гостей в проходках.", ephemeral=True)
            return
        await interaction.response.send_message(
            "🔔 Гости в проходках:\n" + "\n".join(waiting), ephemeral=True
        )

    @discord.ui.button(
        label="Выбрать гостя",
        style=discord.ButtonStyle.primary,
        emoji="🐾",
        custom_id="verify_panel_pick",
    )
    async def pick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return
        male_id, female_id, _ = _get_ids(interaction.guild.id)
        options = []
        for vc in interaction.guild.voice_channels:
            if "роходка" in vc.name or "проходка" in vc.name.lower():
                for m in vc.members:
                    if not _already_verified(m, male_id, female_id):
                        options.append(discord.SelectOption(
                            label=m.display_name[:25],
                            description=vc.name[:50],
                            value=str(m.id),
                        ))
        if not options:
            await interaction.response.send_message("Нет гостей в проходках.", ephemeral=True)
            return
        options = options[:25]
        view = PickMemberView(options)
        await interaction.response.send_message("Выбери гостя:", view=view, ephemeral=True)


class Verify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_scan_done = False

    @commands.Cog.listener()
    async def on_ready(self):
        # После рестарта догоняем тех, кто уже сидит в проходке
        if self._ready_scan_done:
            return
        self._ready_scan_done = True
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                in_passage = is_passage_channel(channel)
                for member in channel.members:
                    if not _needs_passage_mute(member):
                        continue
                    voice = getattr(member, "voice", None)
                    if voice is not None and getattr(voice, "mute", False):
                        # Хвост от старой версии: глобальный мьют снимаем, оставляем только проходку
                        await _clear_global_mute(member)
                    if in_passage:
                        await _mute_in_passage(member, "Проходка: мут после рестарта")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        role = await _ensure_unverified_role(member.guild)
        if role is None:
            return
        try:
            await member.add_roles(role, reason="Новичок: до верификации")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        was_in = before.channel is not None and is_passage_channel(before.channel)
        now_in = after.channel is not None and is_passage_channel(after.channel)
        key = (member.guild.id, member.id)

        if now_in and not was_in:
            if _needs_passage_mute(member):
                await _mute_in_passage(member)
        elif was_in and not now_in:
            # Снимаем только свой мут: чужой не трогаем
            if key in _MUTED_BY_BOT:
                await _unmute_guest(member, "Вышел из проходки")

        # Глобальный мьют у неверифицированного — это хвост старой версии.
        # Политика бота: глушим только в проходке, в обычных войсах человек говорит.
        if (
            after.channel is not None
            and getattr(after, "mute", False)
            and _needs_passage_mute(member)
        ):
            await _clear_global_mute(member)

    @app_commands.command(name="verify", description="Верифицировать участника (только стафф)")
    @app_commands.describe(member="Участник которого верифицируем")
    async def verify(self, interaction: discord.Interaction, member: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return

        block = _dayoff_block(interaction)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
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

        # Возвращаем роль новичка и, если сидит в проходке, снова закрываем микрофон
        uv_role = await _ensure_unverified_role(interaction.guild)
        if uv_role is not None:
            try:
                await member.add_roles(uv_role, reason="Снятие верификации")
            except (discord.Forbidden, discord.HTTPException):
                pass
        if member.voice and is_passage_channel(member.voice.channel) and _needs_passage_mute(member):
            await _mute_in_passage(member, "Проходка: верификация снята")

        await interaction.response.send_message(
            f"✅ Верификация снята с {member.mention}.", ephemeral=True
        )

    @app_commands.command(name="unmute", description="Снять мут с гостя в проходке (только стафф)")
    @app_commands.describe(member="Кого размутить")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        if not _is_staff(interaction.user):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Ботам мут не нужен.", ephemeral=True)
            return

        ok = await _unmute_guest(member, f"Размутил {interaction.user}")
        if not ok:
            await interaction.response.send_message(
                "Не смог снять мут: проверь право «Управление каналами» и позицию роли бота.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🔊 {member.mention} снова может говорить. Не забывай: он всё ещё ждёт верификацию.",
            ephemeral=True,
        )

    @app_commands.command(name="verify_stats", description="Статистика верификаций")
    @app_commands.default_permissions(manage_guild=True)
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

        rows = staff_top(interaction.guild.id, 5)
        if rows:
            medals = ["🥇", "🥈", "🥉", "▫️", "▫️"]
            embed.add_field(
                name="Топ хелперов",
                value="\n".join(
                    f"{medals[i]} <@{uid}> — {info.get('verified_total', 0)} вериф."
                    for i, (uid, info) in enumerate(rows)
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="post_team", description="Запостить состав команды в канал")
    @app_commands.describe(channel="Канал куда постить (обычно 🧸--наша-команда)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def post_team(self, interaction: discord.Interaction, channel: discord.TextChannel):
        embed = discord.Embed(
            color=discord.Color.from_str("#a9c9ff"),
        )
        embed.add_field(
            name="Лапки, которые заботятся о доме",
            value=(
                "<@&1573422762205257763> — хозяин дома.\n"
                "<@&1573680800233426984> — хозяйка дома.\n"
                "<@&1573680796475195482> — комьюнити менеджер.\n"
                "<@&1574078485075333130> — главный администратор.\n"
                "<@&1573422768421077063> — порядок на сервере."
            ),
            inline=False,
        )
        embed.add_field(name="\u3000", value="\u3000", inline=False)
        embed.add_field(
            name="Чтобы всем было спокойно",
            value=(
                "<@&1573680792679489556> — порядок в чатах и голосе.\n"
                "<@&1573422776919007424> — порядок в чатах и голосе.\n"
                "<@&1573422779376865280> — знакомство с новыми участниками."
            ),
            inline=False,
        )
        embed.add_field(name="\u3000", value="\u3000", inline=False)
        embed.add_field(
            name="Чтобы было интересно",
            value="Хочешь быть рядом? <#1573422995811344466>",
            inline=False,
        )
        await channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Запощено в {channel.mention}", ephemeral=True)

    @app_commands.command(name="post_welcome", description="Запостить embed с кнопкой в канал знакомства")
    @app_commands.describe(channel="Канал куда постить (обычно 👋--знакомство)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def post_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel):
        embed = discord.Embed(
            title="Добро пожаловать домой",
            description=(
                "Заходи в 🍑 · Проходка и жди — добрая лапка поможет освоиться.\n\n"
                "Нажми кнопку ниже, чтобы позвать хелпера 🐾"
            ),
            color=discord.Color.from_str("#a9c9ff"),
        )
        await channel.send(embed=embed, view=CallHelperView())
        await interaction.response.send_message(f"✅ Запощено в {channel.mention}", ephemeral=True)

    @app_commands.command(name="post_rules", description="Запостить правила сервера в канал")
    @app_commands.describe(channel="Канал куда постить правила (обычно 📜--правила)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def post_rules(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        COLOR = 0xa9c9ff
        FOOTER = "ROET DELUXE • ПРАВИЛА СООБЩЕСТВА"

        def emb(title, fields, n):
            e = discord.Embed(title=title, color=COLOR)
            e.set_footer(text=f"{FOOTER}  /  {n}")
            for f in fields:
                e.add_field(name=f[0], value=f[1], inline=False)
            return e

        await channel.send(embed=discord.Embed(
            title="ROET DELUXE • ПРАВИЛА СЕРВЕРА",
            description=(
                "Мы придерживаемся [Discord ToS](https://discord.com/terms) и "
                "[Community Guidelines](https://discord.com/guidelines).\n\n"
                "**Разделы:** 01 Общение · 02 Текстовые каналы · 03 Голосовые комнаты\n"
                "04 Профили · 05–06 Сразу бан · 07 Наказания"
            ),
            color=COLOR,
        ))
        import asyncio
        await asyncio.sleep(0.5)
        embeds = [
            emb("01 · ОБЩЕНИЕ И УВАЖЕНИЕ", [
                ("1.1 · Оскорбления", "Запрещены унижения, оскорбления близких и провокации.\n> **Наказание:** Предупреждение → мут 1 час."),
                ("1.2 · Личные границы", "Не навязывайте общение и не преследуйте участников.\n> **Наказание:** Предупреждение → мут 2 часа."),
                ("1.3 · Провокационные споры", "Не разжигайте конфликты на политической и религиозной почве.\n> **Наказание:** Предупреждение → мут 2 часа."),
            ], "02 из 08"),
            emb("02 · ТЕКСТОВЫЕ КАНАЛЫ", [
                ("2.1 · Спам и флуд", "Не засоряйте чат повторяющимися сообщениями и капсом.\n> **Наказание:** Предупреждение → мут 30 минут."),
                ("2.2 · Назначение канала", "Пишите по теме канала. Команды ботов — в 🧺 корзина.\n> **Наказание:** Предупреждение → мут 30 минут."),
                ("2.3 · Упоминания", "Не отмечайте участников многократно без причины.\n> **Наказание:** Предупреждение → мут 1 час."),
            ], "03 из 08"),
            emb("03 · ГОЛОСОВЫЕ КОМНАТЫ", [
                ("3.1 · Микрофон", "Запрещены крики, помехи и усиление микрофона.\n> **Наказание:** Предупреждение → мут 1 час."),
                ("3.2 · Помехи общению", "Не перебивайте намеренно и не мешайте беседе.\n> **Наказание:** Предупреждение → мут 1 час."),
                ("3.3 · Трансляции", "На трансляции распространяются те же запреты на контент.\n> **Наказание:** Предупреждение → мут 1 час."),
            ], "04 из 08"),
            emb("04 · ПРОФИЛИ И ОБРАЩЕНИЯ", [
                ("4.1 · Ник и профиль", "Не используйте оскорбительные ники и не копируйте профили стаффа.\n> **Наказание:** Требование исправить → мут 1 час при отказе."),
                ("4.2 · Жалобы", "Не подделывайте доказательства и не спамьте обращениями.\n> **Наказание:** Предупреждение → мут 1 час."),
            ], "05 из 08"),
            emb("05 · СРАЗУ БАН — БЕЗОПАСНОСТЬ", [
                ("\u200b", "**За любой из пунктов — бессрочный бан без предупреждения.**"),
                ("5.1 · Личные данные и угрозы", "Запрещены доксинг, шантаж, угрозы и публикация чужих данных."),
                ("5.2 · Запрещённый контент", "Запрещены порнография, жестокость и сексуализация несовершеннолетних."),
                ("5.3 · Дискриминация", "Запрещены призывы к насилию и унижение по любым признакам."),
                ("5.4 · Травля", "Запрещены рейды, организованная травля и атаки на сервер."),
            ], "06 из 08"),
            emb("06 · СРАЗУ БАН — ОБМАН", [
                ("\u200b", "**За любой из пунктов — бессрочный бан без предупреждения.**"),
                ("6.1 · Мошенничество", "Запрещены фишинг, кража аккаунтов и вредоносные файлы."),
                ("6.2 · Реклама", "Без согласования запрещена реклама серверов и услуг."),
                ("6.3 · Обход наказаний", "Запрещено обходить мут и бан через другие аккаунты."),
            ], "07 из 08"),
            emb("07 · НАКАЗАНИЯ И ОБРАЩЕНИЯ", [
                ("Градация", "Первое нарушение — предупреждение. Повтор в 48 часов — мут."),
                ("Повторные нарушения", "3 мута за 48 часов → 1 варн. 3 варна → бан на 30 дней."),
                ("Куда обратиться", "Создай тикет в 🎫 поддержка. Укажи пункт и приложи доказательства."),
            ], "08 из 08"),
        ]
        for e in embeds:
            await channel.send(embed=e)
            await asyncio.sleep(0.5)

        await interaction.followup.send(f"✅ Правила запощены в {channel.mention}", ephemeral=True)

    @app_commands.command(name="post_map", description="Запостить карту сервера: маршрут, чаты, планы")
    @app_commands.describe(channel="Канал куда постить карту")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def post_map(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        taken: set = set()
        unresolved = []
        embeds = []
        for title, entries in MAP_SECTIONS:
            e = discord.Embed(title=title, color=discord.Color.from_str("#a9c9ff"))
            lines = []
            for pattern, emoji, desc in entries:
                ch = _find_channel(interaction.guild, pattern, taken)
                if ch:
                    taken.add(ch.id)
                    lines.append(f"{emoji} {ch.mention} — {desc}")
                else:
                    unresolved.append(pattern)
                    lines.append(f"{emoji} `{pattern}` — {desc}")
            if title == "Планы на вечер":
                lines.append(MAP_HELPER_LINE)
            e.description = "\n".join(lines)
            embeds.append(e)

        await channel.send(embeds=embeds)
        if unresolved:
            await interaction.followup.send(
                f"✅ Запощено в {channel.mention}. Не нашёл каналы: "
                + ", ".join(f"`{p}`" for p in unresolved)
                + " — пришли верные имена, поправлю.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Запощено в {channel.mention}, все каналы нашлись.", ephemeral=True
            )

    @app_commands.command(name="setup_verify", description="Настроить верификацию и поставить панель")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        log_channel="Канал для логов верификаций",
        panel_channel="Канал стаффа где ставится панель с дропдауном",
        role_male="Роль ♂",
        role_female="Роль ♀",
        unverified_role="Роль новичка до верификации (по умолчанию создам сам)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel,
        panel_channel: discord.TextChannel = None,
        role_male: discord.Role = None,
        role_female: discord.Role = None,
        unverified_role: discord.Role = None,
    ):
        data = load(SETTINGS_FILE, {})
        cfg = data.setdefault(str(interaction.guild.id), {})
        cfg["log_channel_id"] = log_channel.id
        if role_male:
            cfg["role_male"] = role_male.id
        if role_female:
            cfg["role_female"] = role_female.id
        if unverified_role:
            cfg["unverified_role_id"] = unverified_role.id
        save(SETTINGS_FILE, data)

        if unverified_role is None:
            # Роль новичка создаём сразу, чтобы гости получали её с первой секунды
            made = await _ensure_unverified_role(interaction.guild)
            if made is not None:
                unverified_role = made

        if panel_channel:
            panel_embed = discord.Embed(
                title="🔑 Доступ",
                description="Участники ожидающие верификации. Выбери из списка.",
                color=discord.Color.from_str("#a9c9ff"),
            )
            await panel_channel.send(embed=panel_embed, view=VerifyPanelView())

        embed = discord.Embed(title="✅ Верификация настроена", color=discord.Color.green())
        embed.add_field(name="Логи", value=log_channel.mention, inline=True)
        if panel_channel:
            embed.add_field(name="Панель", value=panel_channel.mention, inline=True)
        if role_male:
            embed.add_field(name="♂", value=role_male.mention, inline=True)
        if role_female:
            embed.add_field(name="♀", value=role_female.mention, inline=True)
        if unverified_role:
            embed.add_field(name="Новичок", value=unverified_role.mention, inline=True)
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
    bot.add_view(VerifyPanelView())
    await bot.add_cog(Verify(bot))
