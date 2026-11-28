# -*- coding: utf-8 -*-
"""Проверка стафф-логики верифай-бота без Discord (временная папка data)."""

import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="staff_check_")
sys.path.insert(0, r"C:\Users\tomoe\Downloads\bot-verify")

import storage
storage.DATA_DIR = os.path.join(TMP, "data")

from cogs import staff_core as sc

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


class FakeRole:
    def __init__(self, rid):
        self.id = rid


class FakePerms:
    def __init__(self, admin=False):
        self.administrator = admin


class FakeMember:
    def __init__(self, roles=(), admin=False):
        self.roles = [FakeRole(r) for r in roles]
        self.guild_permissions = FakePerms(admin)


class FakeChannel:
    def __init__(self, name):
        self.name = name


# Стафф-роли
check("staff by role", sc.is_staff(FakeMember([sc.STAFF_ROLE_IDS[0]])))
check("staff by admin", sc.is_staff(FakeMember([], admin=True)))
check("guest not staff", not sc.is_staff(FakeMember([123])))

# Проходка по имени
check("passage name", sc.is_passage(FakeChannel("🍑 · Проходка")))
check("passage number", sc.is_passage(FakeChannel("Проходка 2")))
check("not passage", not sc.is_passage(FakeChannel("общий-войс")))

# Настройка монеты
sc.set_settings(777, verify_coin=2, staff_currency="🐾")
check("coin from settings", sc.coin_per_verify(777) == 2)
check("currency from settings", sc.staff_currency(777) == "🐾")
check("coin default", sc.coin_per_verify(999) == sc.DEFAULT_COIN)

# Верификации и лапки
coins, total = sc.add_verify(777, 1)
check("first verify coins", coins == 2 and total == 2)
coins, total = sc.add_verify(777, 1)
check("second verify coins", total == 4)
e = sc.get_entry(777, 1)
check("counters", e["verified_total"] == 2 and e["verified_today"] == 2)

# Сброс дневных счётчиков
e["day"] = "2000-01-01"
e = sc.get_entry(777, 1)
check("daily reset", e["verified_today"] == 0 and e["verified_total"] == 2)

# Время в проходке
sc.voice_start(777, 2)
sc._voice_since[(777, 2)] -= 125  # сделали вид, что сидит 125 секунд
check("live time", 120 <= sc.voice_live(777, 2) <= 130, str(sc.voice_live(777, 2)))
added = sc.voice_stop(777, 2)
e2 = sc.get_entry(777, 2)
check("stop adds time", 120 <= added <= 130 and e2["voice_total"] >= 120, str(added))
check("live after stop", sc.voice_live(777, 2) == 0)
check("double stop safe", sc.voice_stop(777, 2) == 0)

# Статистика за сегодня и за неделю
import datetime as _dt

e2 = sc.get_entry(777, 2)
yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
e2["daily"][yesterday] = {"verified": 2, "voice": 300}

v_today, s_today = sc.day_stat(777, 2, 1)
v_week, s_week = sc.day_stat(777, 2, 7)
check("day_stat today", v_today == 0 and 120 <= s_today <= 130, f"{v_today}/{s_today}")
check("week sums yesterday", v_week == 2 and 420 <= s_week <= 440, f"{v_week}/{s_week}")

old_day = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
e2["daily"][old_day] = {"verified": 50, "voice": 5000}
sc._bump_day(777, 2, "verified")
check("old history pruned", old_day not in e2["daily"])
check("week ignores old", sc.day_stat(777, 2, 7)[0] == 3, str(sc.day_stat(777, 2, 7)[0]))

# Миграция записи старой схемы (без daily)
sc._data().setdefault("888", {})["5"] = {"verified_today": 3, "voice_today": 60}
sc.get_entry(888, 5)
check("legacy migrated", sc.day_stat(888, 5, 1) == (3, 60), str(sc.day_stat(888, 5, 1)))

# Повторный вход не сбрасывает таймер
sc.voice_start(777, 2)
started = sc._voice_since[(777, 2)]
sc.voice_start(777, 2)
check("start idempotent", sc._voice_since[(777, 2)] == started)
sc.voice_stop(777, 2)

# Топ и место
sc.add_verify(777, 3)
sc.add_verify(777, 3)
sc.add_verify(777, 3)
rows = sc.top(777, 10)
ids = [uid for uid, _ in rows]
check("top order", ids[0] == 3 and 1 in ids, str(ids))
check("place of leader", sc.place(777, 3) == 1)
check("place of laggard", sc.place(777, 1) == 2, str(sc.place(777, 1)))

# Выдача/снятие
sc.give(777, 4, 10)
check("give", sc.get_entry(777, 4)["coins"] == 10)
sc.give(777, 4, -100)
check("take floors at zero", sc.get_entry(777, 4)["coins"] == 0)

# ── Магазин ───────────────────────────────────────────────────────────────────
check("role price default", sc.role_price(999) == sc.DEFAULT_ROLE_PRICE)
check("dayoff price default", sc.dayoff_price(999) == sc.DEFAULT_DAYOFF_PRICE)
check("dayoff days default", sc.dayoff_days(999) == sc.DEFAULT_DAYOFF_DAYS)

sc.set_settings(777, staff_role_price=150, staff_dayoff_price=700, staff_dayoff_days=3)
check("role price setting", sc.role_price(777) == 150)
check("dayoff price setting", sc.dayoff_price(777) == 700)
check("dayoff days setting", sc.dayoff_days(777) == 3)

# Списание
sc.give(777, 4, 500)
check("spend ok", sc.spend(777, 4, 200) and sc.get_entry(777, 4)["coins"] == 300)
check("spend too much", not sc.spend(777, 4, 999) and sc.get_entry(777, 4)["coins"] == 300)

# Отгул
check("not on dayoff", not sc.on_dayoff(777, 4))
sc.set_dayoff(777, 4, _dt.datetime.now().timestamp() + 3600)
check("on dayoff", sc.on_dayoff(777, 4))
check("dayoff text", "в" in sc.dayoff_until_str(777, 4), sc.dayoff_until_str(777, 4))

sc.give(777, 5, 10)
sc.set_dayoff(777, 5, _dt.datetime.now().timestamp() - 10)
expired = [uid for uid, _ in sc.expired_dayoffs(777)]
check("expired detected", 5 in expired and 4 not in expired, str(expired))
sc.clear_dayoff(777, 5)
check("clear dayoff", not sc.on_dayoff(777, 5))
check("expired empty after clear", sc.expired_dayoffs(777) == [], str(sc.expired_dayoffs(777)))
sc.clear_dayoff(777, 4)

# voice_drop не пишет время
sc.voice_start(777, 6)
sc._voice_since[(777, 6)] -= 30
sc.voice_drop(777, 6)
check("voice drop no time", sc.voice_live(777, 6) == 0 and sc.get_entry(777, 6)["voice_total"] == 0)

# Сохранение на диск
sc.save_data(force=True)
sc._cache = None
check("persisted coins", sc.get_entry(777, 3)["coins"] == 6, str(sc.get_entry(777, 3)["coins"]))
check("persisted voice", sc.get_entry(777, 2)["voice_total"] >= 120)

# Коги
import cogs.staff as staff_cog
import cogs.verify as verify_cog
import discord

names = {c.name for c in staff_cog.Staff.__cog_app_commands__}
check(
    "staff commands",
    {"staff_stats", "staff_top", "staff_config", "staff_give", "staff_take", "staff_shop"} <= names,
    str(sorted(names)),
)
check("staff setup", callable(staff_cog.setup))
check("verify setup", callable(verify_cog.setup))
check("verify credit imported", verify_cog.add_verify_credit is sc.add_verify)
check("dayoff block helper", callable(verify_cog._dayoff_block))
check("stats guarded", "is_staff" in staff_cog.Staff.staff_stats.callback.__code__.co_names)
check("top guarded", "is_staff" in staff_cog.Staff.staff_top.callback.__code__.co_names)

# Вьюхи магазина собираются
shop_view = staff_cog.ShopView()
check("shop view buttons", len(shop_view.children) == 2, str(len(shop_view.children)))
check("role color view", len(staff_cog.RoleColorView(100).children) == 1)

# Проверка блокировки в отгуле без Discord: подсовываем фейковый interaction
class _FakeUser:
    id = 4
class _FakeGuild:
    id = 777
class _FakeInteraction:
    guild = _FakeGuild()
    user = _FakeUser()

sc.set_dayoff(777, 4, _dt.datetime.now().timestamp() + 3600)
block_msg = verify_cog._dayoff_block(_FakeInteraction())
check("dayoff blocks verify", "отгуле" in block_msg, block_msg[:40])
sc.clear_dayoff(777, 4)
check("no block without dayoff", verify_cog._dayoff_block(_FakeInteraction()) == "")

verify_names = {c.name for c in verify_cog.Verify.__cog_app_commands__}
check("verify commands intact", {"verify", "unverify", "verify_stats", "setup_verify", "post_map", "unmute", "unmute_all"} <= verify_names)


# ── Роль новичка и мут в проходке ─────────────────────────────────────────────

class _UVRole:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name


class _UVOverwrite:
    def __init__(self, speak=None):
        self.speak = speak

    def is_empty(self):
        return self.speak is None


class _UVChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.overwrites = {}
        self.calls = 0

    def overwrites_for(self, target):
        return self.overwrites.setdefault(target, _UVOverwrite())

    async def set_permissions(self, target, overwrite=None, reason=None):
        if overwrite is None:
            self.overwrites.pop(target, None)
        else:
            self.overwrites[target] = overwrite
        self.calls += 1


class _UVGuild:
    def __init__(self, roles, voice_channels=None):
        self.id = 777
        self.roles = roles
        self.voice_channels = voice_channels or []
        self.voice_states = {}


class _UVMember:
    def __init__(self, uid, guild, roles, admin=False):
        self.id = uid
        self.guild = guild
        self.roles = roles
        self.bot = False
        self.guild_permissions = FakePerms(admin)
        self.kw = {}
        self.voice = None

    async def edit(self, **kw):
        self.kw = kw


class _UVVoice:
    def __init__(self, mute=False):
        self.mute = mute


class _UVState:
    def __init__(self, member, mute=False):
        self.member = member
        self.mute = mute


uv_role = _UVRole(900, "🔒 Не верифицирован")
male_role = _UVRole(sc.STAFF_ROLE_IDS[0], "♂")
passage_one = _UVChannel(1001, "🍑 · Проходка")
passage_two = _UVChannel(1002, "🍑 · Проходка 2")
lounge = _UVChannel(1003, "общий-войс")
guild_uv = _UVGuild([uv_role, male_role], [passage_one, passage_two, lounge])

novice = _UVMember(101, guild_uv, [uv_role])
verified = _UVMember(102, guild_uv, [male_role])
admin = _UVMember(104, guild_uv, [uv_role], admin=True)

import asyncio

check("unverified role found", verify_cog._find_unverified_role(guild_uv) is uv_role)

# Мут вешается на роль, а не на человека
done = asyncio.run(verify_cog._sync_passage_overwrites(guild_uv))
check("role overwrite set in passages", done == 2, str(done))
check(
    "role cannot speak in passage",
    passage_one.overwrites.get(uv_role) is not None and passage_one.overwrites[uv_role].speak is False,
)
check("role cannot speak in passage 2", passage_two.overwrites[uv_role].speak is False)
check("role untouched in normal voice", uv_role not in lounge.overwrites)
check("no personal mutes created", novice not in passage_one.overwrites and verified not in passage_one.overwrites)
check("no server mute set", novice.kw == {} and verified.kw == {}, str(novice.kw))

asyncio.run(verify_cog._sync_passage_overwrites(guild_uv))
check("second sync is a no-op", passage_one.calls == 1, str(passage_one.calls))

# Личные запреты от прошлой версии снимаются, ролевой остаётся
passage_one.overwrites[novice] = _UVOverwrite(speak=False)
passage_two.overwrites[novice] = _UVOverwrite(speak=False)
passage_one.overwrites[uv_role] = _UVOverwrite(speak=False)
cleared = asyncio.run(verify_cog._clear_member_overwrites(guild_uv))
check(
    "personal mutes cleared",
    novice not in passage_one.overwrites and novice not in passage_two.overwrites,
    str(cleared),
)
check("role mute survives cleanup", passage_one.overwrites[uv_role].speak is False)

# Серверный мьют снимается
novice.voice = _UVVoice(mute=True)
ok = asyncio.run(verify_cog._unmute_guest(novice))
check("unmute clears server mute", novice.kw.get("mute") is False and ok, str(novice.kw))

# Разовая чистка серверных мьютов: стафф и ботов не трогаем
muted_user = _UVMember(201, guild_uv, [])
muted_user.voice = _UVVoice(mute=True)
muted_admin = _UVMember(202, guild_uv, [uv_role], admin=True)
muted_admin.voice = _UVVoice(mute=True)
bot_member = _UVMember(203, guild_uv, [])
bot_member.bot = True
bot_member.voice = _UVVoice(mute=True)
quiet = _UVMember(204, guild_uv, [male_role])
quiet.voice = _UVVoice(mute=False)
guild_uv.voice_states = {
    m.id: _UVState(m, m.voice.mute) for m in (muted_user, muted_admin, bot_member, quiet)
}
count = asyncio.run(verify_cog._clear_server_mutes(guild_uv))
check("cleanup unmutes regular members", count == 1 and muted_user.kw.get("mute") is False, str(count))
check("cleanup skips staff", muted_admin.kw == {}, str(muted_admin.kw))
check("cleanup skips bots", bot_member.kw == {}, str(bot_member.kw))
check("cleanup skips non-muted", quiet.kw == {}, str(quiet.kw))

# Окно чистки: открывается само и закрывается по сроку
storage.save("settings.json", {})
check("legacy window gone", not hasattr(verify_cog, "_legacy_mute_window_left"))

# ── Ответ на нажатие, когда интеракция протухла (10062) ───────────────────────

class _RespFake:
    def __init__(self, done=False, notfound=False):
        self._done = done
        self.notfound = notfound
        self.edited = None

    def is_done(self):
        return self._done

    async def edit_message(self, **kw):
        if self.notfound:
            raise discord.NotFound.__new__(discord.NotFound)
        self.edited = kw


class _FollowupFake:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = None

    async def send(self, **kw):
        if self.fail:
            raise discord.HTTPException.__new__(discord.HTTPException)
        self.sent = kw


class _MsgFake:
    def __init__(self):
        self.edited = None

    async def edit(self, **kw):
        self.edited = kw


class _InterFake:
    def __init__(self, resp, followup, message=None):
        self.response = resp
        self.followup = followup
        self.message = message


resp = _RespFake()
asyncio.run(verify_cog._safe_respond(_InterFake(resp, _FollowupFake()), content="ok"))
check("respond edits message", resp.edited is not None and resp.edited.get("content") == "ok")

resp = _RespFake(notfound=True)
fu = _FollowupFake()
asyncio.run(verify_cog._safe_respond(_InterFake(resp, fu), content="ok"))
check("respond falls back to followup on 10062", fu.sent is not None, str(fu.sent))

msg = _MsgFake()
asyncio.run(
    verify_cog._safe_respond(
        _InterFake(_RespFake(notfound=True), _FollowupFake(fail=True), msg), content="ok"
    )
)
check("respond falls back to message edit", msg.edited is not None, str(msg.edited))

check(
    "respond never raises",
    asyncio.run(
        verify_cog._safe_respond(
            _InterFake(_RespFake(notfound=True), _FollowupFake(fail=True)), content="x"
        )
    )
    is False,
)

# Поиск каналов для карты сервера
class _FakeCh:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class _FakeGuildMap:
    def __init__(self, names):
        self.text_channels = [_FakeCh(1000 + i, n) for i, n in enumerate(names)]
        self.voice_channels = []


gm = _FakeGuildMap([
    "📖--котекст", "🏡--нет-грустна", "🎀 | мои-роли", "🧸--наша-команда",
    "💬--общение", "😹--смешинки", "🤳--селфи", "👥--ищу-компанию",
    "💞--знакомства", "🐾--питомцы", "🏞--прогулки", "🎨--рисунки",
    "🥇--достижения", "📅--собираемся", "🎧--плеер", "🧺--корзина",
    "➕ · Создать уголок", "🔴 · Войти", "🌿--незнакомство",
    "🎁--спонсорские-пакеты",
])

taken = set()
found = {}
for _title, entries in verify_cog.MAP_SECTIONS:
    for pattern, _e, _d in entries:
        found[pattern] = verify_cog._find_channel(gm, pattern, taken)
        if found[pattern]:
            taken.add(found[pattern].id)

missed = [p for p, ch in found.items() if ch is None]
check("map resolves all", not missed, "missed: " + ", ".join(missed))
check("map korzina", found["корзина"].name == "🧺--корзина")
check("map unique", len(taken) == len(found))

# Кириллица
if "стафф" not in verify_cog.Verify.__cog_app_commands__[0].description.lower() and False:
    pass
check("cyrillic ok", "Статистика стаффа" in staff_cog.Staff.staff_stats.description)

# ── Подсказка хелперу при верификации ────────────────────────────────────────

class _FakeAvatar:
    def __init__(self, default):
        self._default = default
        self.url = "https://example.com/avatar.png"

    def is_default(self):
        return self._default


class _FakeHintMember:
    def __init__(self, days_old, name="котик", default_avatar=False):
        self.id = 555
        self.mention = "<@555>"
        self.display_name = name
        self.name = name
        self.global_name = None
        self.created_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_old)
        self.display_avatar = _FakeAvatar(default_avatar)


now = _dt.datetime.now(_dt.timezone.utc)

verdict, reasons = verify_cog._risk_hint(_FakeHintMember(400, "мурзик"))
check("hint ok for old clean account", verdict == verify_cog.HINT_OK, verdict)

verdict, _ = verify_cog._risk_hint(_FakeHintMember(10, "мурзик"))
check("hint ok for 10-day account alone", verdict == verify_cog.HINT_OK, verdict)

verdict, _ = verify_cog._risk_hint(_FakeHintMember(10, "мурзик", default_avatar=True))
check("hint warns for 10-day + no avatar", verdict == verify_cog.HINT_WARN, verdict)

verdict, reasons = verify_cog._risk_hint(
    _FakeHintMember(1, "12345678", default_avatar=True)
)
check("hint risk for fresh no-avatar digits", verdict == verify_cog.HINT_RISK, verdict)
check("hint lists reasons", len(reasons) >= 3, str(reasons))

verdict, reasons = verify_cog._risk_hint(_FakeHintMember(400, "discord_support"))
check("hint catches suspicious nick", verdict == verify_cog.HINT_WARN, verdict)
check("hint names the word", any("discord" in r for r in reasons), str(reasons))

# Правила Discord: impersonation, скам-слова, ссылки
verdict, reasons = verify_cog._risk_hint(_FakeHintMember(400, "free_nitro"))
check("hint catches scam words", verdict == verify_cog.HINT_WARN, verdict)
check("hint names scam", any("nitro" in r for r in reasons), str(reasons))

verdict, reasons = verify_cog._risk_hint(_FakeHintMember(400, "discord.gift/abc"))
check("hint risk for link in nick", verdict == verify_cog.HINT_RISK, verdict)
check("hint names link", any("ссылк" in r for r in reasons), str(reasons))

verdict, _ = verify_cog._risk_hint(_FakeHintMember(400, "steam_gamer"))
check("normal gamer nick stays ok", verdict == verify_cog.HINT_OK, verdict)

check("checklist mentions 13+", "13+" in verify_cog.HINT_CHECKLIST)
check("checklist mentions impersonation", "стафф" in verify_cog.HINT_CHECKLIST)

check("age str minutes", verify_cog._age_str(_dt.timedelta(minutes=12)) == "12 мин.")
check("age str hours", verify_cog._age_str(_dt.timedelta(hours=5)) == "5 ч.")
check("age str days", verify_cog._age_str(_dt.timedelta(days=5)) == "5 дн.")
check("age str months", verify_cog._age_str(_dt.timedelta(days=65)) == "2 мес.")
check("age str years", verify_cog._age_str(_dt.timedelta(days=800)) == "2 г. 2 мес.")
check("age str whole years", verify_cog._age_str(_dt.timedelta(days=730)) == "2 г.")

# В карточке верификации подсказка реально появляется
embed = verify_cog._make_embed(_FakeHintMember(1, "12345678", default_avatar=True), "🔇 · не в войсе")
field_names = [f.name for f in embed.fields]
check("hint field in card", "🧭 Подсказка по правилам Discord" in field_names, str(field_names))
check("account age field in card", "🗓 Аккаунт" in field_names, str(field_names))
check("build in card footer", "сборка" in (embed.footer.text or ""), str(embed.footer.text))

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
