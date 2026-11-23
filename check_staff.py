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

# Сохранение на диск
sc.save_data(force=True)
sc._cache = None
check("persisted coins", sc.get_entry(777, 3)["coins"] == 6, str(sc.get_entry(777, 3)["coins"]))
check("persisted voice", sc.get_entry(777, 2)["voice_total"] >= 120)

# Коги
import cogs.staff as staff_cog
import cogs.verify as verify_cog

names = {c.name for c in staff_cog.Staff.__cog_app_commands__}
check("staff commands", {"staff_stats", "staff_top", "staff_config", "staff_give", "staff_take"} <= names, str(sorted(names)))
check("staff setup", callable(staff_cog.setup))
check("verify setup", callable(verify_cog.setup))
check("verify credit imported", verify_cog.add_verify_credit is sc.add_verify)

verify_names = {c.name for c in verify_cog.Verify.__cog_app_commands__}
check("verify commands intact", {"verify", "unverify", "verify_stats", "setup_verify"} <= verify_names)

# Кириллица
if "стафф" not in verify_cog.Verify.__cog_app_commands__[0].description.lower() and False:
    pass
check("cyrillic ok", "Статистика стаффа" in staff_cog.Staff.staff_stats.description)

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
