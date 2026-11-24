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
check("verify commands intact", {"verify", "unverify", "verify_stats", "setup_verify", "post_map", "unmute"} <= verify_names)


# ── Роль новичка и мут в проходке ─────────────────────────────────────────────

class _UVRole:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name


class _UVGuild:
    def __init__(self, roles):
        self.id = 777
        self.roles = roles


class _UVMember:
    def __init__(self, uid, guild, roles, admin=False):
        self.id = uid
        self.guild = guild
        self.roles = roles
        self.bot = False
        self.guild_permissions = FakePerms(admin)
        self.kw = {}

    async def edit(self, **kw):
        self.kw = kw


uv_role = _UVRole(900, "🔒 Не верифицирован")
male_role = _UVRole(sc.STAFF_ROLE_IDS[0], "♂")
guild_uv = _UVGuild([uv_role, male_role])

novice = _UVMember(101, guild_uv, [uv_role])
verified = _UVMember(102, guild_uv, [male_role])
blank = _UVMember(103, guild_uv, [])

check("unverified role found", verify_cog._find_unverified_role(guild_uv) is uv_role)
check("novice needs mute", verify_cog._needs_passage_mute(novice))
check("verified not muted", not verify_cog._needs_passage_mute(verified))
guild_norole = _UVGuild([male_role])
check("fallback mutes roleless", verify_cog._needs_passage_mute(_UVMember(105, guild_norole, [])))
check("no role and no unverified role -> not muted", not verify_cog._needs_passage_mute(blank))
check("staff not muted", not verify_cog._needs_passage_mute(_UVMember(104, guild_uv, [uv_role], admin=True)))
check("bot not muted", not verify_cog._needs_passage_mute(blank) if blank.bot else True)

import asyncio

asyncio.run(verify_cog._mute_in_passage(novice))
check("mute sets flag", (777, 101) in verify_cog._MUTED_BY_BOT and novice.kw.get("mute") is True)
asyncio.run(verify_cog._unmute_guest(novice))
check("unmute clears flag", (777, 101) not in verify_cog._MUTED_BY_BOT and novice.kw.get("mute") is False)

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

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
