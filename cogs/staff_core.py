# -*- coding: utf-8 -*-
"""Данные стаффа: лапки за верификации и время в проходке.

Файл staff.json: {guild_id: {user_id: {...}}}
Файл settings.json: {guild_id: {verify_coin: 1, staff_currency: "🐾"}}
"""

import datetime
import time

from storage import load, save

STAFF_FILE = "staff.json"
SETTINGS_FILE = "settings.json"

DEFAULT_COIN = 1
DEFAULT_CURRENCY = "🐾"

STAFF_ROLE_IDS = [
    1573422779376865280,  # Хелпер
    1573422776919007424,  # Модератор
    1573680792679489556,  # Старший модератор
    1573422768421077063,  # Администратор
    1573680796475195482,  # Главный администратор
    1573422762205257763,  # Владелец
    1573680800233426984,  # Владелица
]

_cache = None
_dirty = False
_voice_since = {}


def _today() -> str:
    return datetime.date.today().isoformat()


def is_staff(member) -> bool:
    """Стафф — по роли или по праву администратора."""
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    return any(getattr(r, "id", 0) in STAFF_ROLE_IDS for r in getattr(member, "roles", []))


def is_passage(channel) -> bool:
    """Проходка — любой войс, в имени которого есть «проходк»."""
    name = getattr(channel, "name", "") or ""
    return "проходк" in name.lower()


# ── Настройки ─────────────────────────────────────────────────────────────────

def _settings(guild_id: int) -> dict:
    return load(SETTINGS_FILE, {}).get(str(guild_id), {})


def coin_per_verify(guild_id: int) -> int:
    return max(0, int(_settings(guild_id).get("verify_coin", DEFAULT_COIN)))


def staff_currency(guild_id: int) -> str:
    return _settings(guild_id).get("staff_currency", DEFAULT_CURRENCY)


def set_settings(guild_id: int, **kwargs) -> dict:
    data = load(SETTINGS_FILE, {})
    cfg = data.setdefault(str(guild_id), {})
    for key, value in kwargs.items():
        if value is not None:
            cfg[key] = value
    save(SETTINGS_FILE, data)
    return cfg


# ── Данные ────────────────────────────────────────────────────────────────────

def _data() -> dict:
    global _cache
    if _cache is None:
        _cache = load(STAFF_FILE, {})
    return _cache


def save_data(force: bool = False) -> None:
    global _dirty
    if _cache is None:
        return
    if not (_dirty or force):
        return
    save(STAFF_FILE, _cache)
    _dirty = False


def get_entry(guild_id: int, user_id: int) -> dict:
    global _dirty
    guild = _data().setdefault(str(guild_id), {})
    e = guild.setdefault(str(user_id), {})
    today = _today()

    e.setdefault("coins", 0)
    e.setdefault("verified_total", 0)
    e.setdefault("verified_today", 0)
    e.setdefault("voice_total", 0)
    e.setdefault("voice_today", 0)
    e.setdefault("day", today)
    e.setdefault("voice_day", today)
    e.setdefault("first_seen", today)

    if "daily" not in e:
        # Миграция со старой схемы: «сегодня» переезжает в историю по дням.
        e["daily"] = {}
        if e.get("verified_today") or e.get("voice_today"):
            e["daily"][today] = {
                "verified": int(e.get("verified_today", 0)),
                "voice": int(e.get("voice_today", 0)),
            }

    if e["day"] != today:
        e["day"] = today
        e["verified_today"] = 0
    if e["voice_day"] != today:
        e["voice_day"] = today
        e["voice_today"] = 0

    _dirty = True
    return e


def _bump_day(guild_id: int, user_id: int, key: str, amount: int = 1) -> None:
    """Плюс к счётчику за сегодня с чисткой старой истории."""
    global _dirty
    e = get_entry(guild_id, user_id)
    daily = e.setdefault("daily", {})
    today = _today()
    day = daily.setdefault(today, {"verified": 0, "voice": 0})
    day[key] = int(day.get(key, 0)) + int(amount)

    cutoff = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    for old in [d for d in daily if d < cutoff]:
        del daily[old]
    _dirty = True


def day_stat(guild_id: int, user_id: int, days: int = 1):
    """Сумма за последние N дней (включая сегодня): (верификации, секунды в проходке)."""
    e = get_entry(guild_id, user_id)
    daily = e.get("daily", {})
    today = datetime.date.today()
    verified = 0
    voice = 0
    for i in range(max(1, days)):
        key = (today - datetime.timedelta(days=i)).isoformat()
        day = daily.get(key) or {}
        verified += int(day.get("verified", 0))
        voice += int(day.get("voice", 0))
    return verified, voice


def add_verify(guild_id: int, user_id: int):
    """Верификация: +монеты и +1 в счётчики. Возвращает (начислено, всего монет)."""
    e = get_entry(guild_id, user_id)
    coins = coin_per_verify(guild_id)
    e["verified_total"] = int(e.get("verified_total", 0)) + 1
    e["verified_today"] = int(e.get("verified_today", 0)) + 1
    e["coins"] = int(e.get("coins", 0)) + coins
    _bump_day(guild_id, user_id, "verified")
    save_data(force=True)
    return coins, e["coins"]


def give(guild_id: int, user_id: int, amount: int) -> int:
    """Начислить (amount<0 — забрать, ниже нуля не уходит). Возвращает новый баланс."""
    e = get_entry(guild_id, user_id)
    e["coins"] = max(0, int(e.get("coins", 0)) + int(amount))
    save_data(force=True)
    return e["coins"]


# ── Магазин ───────────────────────────────────────────────────────────────────

DEFAULT_ROLE_PRICE = 200
DEFAULT_DAYOFF_PRICE = 800
DEFAULT_DAYOFF_DAYS = 2


def role_price(guild_id: int) -> int:
    return max(0, int(_settings(guild_id).get("staff_role_price", DEFAULT_ROLE_PRICE)))


def dayoff_price(guild_id: int) -> int:
    return max(0, int(_settings(guild_id).get("staff_dayoff_price", DEFAULT_DAYOFF_PRICE)))


def dayoff_days(guild_id: int) -> int:
    return max(1, int(_settings(guild_id).get("staff_dayoff_days", DEFAULT_DAYOFF_DAYS)))


def dayoff_role_id(guild_id: int) -> int:
    return int(_settings(guild_id).get("dayoff_role_id", 0) or 0)


def spend(guild_id: int, user_id: int, amount: int) -> bool:
    """Списать лапки. False — не хватило."""
    e = get_entry(guild_id, user_id)
    if int(e.get("coins", 0)) < amount:
        return False
    e["coins"] = int(e.get("coins", 0)) - int(amount)
    save_data(force=True)
    return True


def dayoff_until(guild_id: int, user_id: int) -> float:
    return float(get_entry(guild_id, user_id).get("dayoff_until", 0) or 0)


def on_dayoff(guild_id: int, user_id: int) -> bool:
    return dayoff_until(guild_id, user_id) > time.time()


def dayoff_until_str(guild_id: int, user_id: int) -> str:
    """«25.11 в 14:30» или пустая строка, если отгула нет."""
    until = dayoff_until(guild_id, user_id)
    if until <= time.time():
        return ""
    return datetime.datetime.fromtimestamp(until).strftime("%d.%m в %H:%M")


def set_dayoff(guild_id: int, user_id: int, until_ts: float) -> None:
    e = get_entry(guild_id, user_id)
    e["dayoff_until"] = float(until_ts)
    save_data(force=True)


def clear_dayoff(guild_id: int, user_id: int) -> None:
    e = get_entry(guild_id, user_id)
    e.pop("dayoff_until", None)
    save_data(force=True)


def expired_dayoffs(guild_id: int, now: float = None):
    """[(user_id, until)] — у кого отгул уже кончился."""
    now = time.time() if now is None else now
    guild = _data().get(str(guild_id), {})
    result = []
    for uid, info in guild.items():
        until = float(info.get("dayoff_until", 0) or 0)
        if until and until <= now:
            result.append((int(uid), until))
    return result


# ── Время в проходке ──────────────────────────────────────────────────────────

def voice_start(guild_id: int, user_id: int) -> None:
    _voice_since.setdefault((guild_id, user_id), time.time())


def voice_stop(guild_id: int, user_id: int) -> int:
    """Остановить счётчик и записать секунды. Возвращает добавленное время."""
    started = _voice_since.pop((guild_id, user_id), None)
    if not started:
        return 0
    global _dirty
    seconds = int(time.time() - started)
    if seconds <= 0:
        return 0
    e = get_entry(guild_id, user_id)
    e["voice_total"] = int(e.get("voice_total", 0)) + seconds
    e["voice_today"] = int(e.get("voice_today", 0)) + seconds
    _bump_day(guild_id, user_id, "voice", seconds)
    _dirty = True
    return seconds


def voice_live(guild_id: int, user_id: int) -> int:
    """Сколько уже сидит в проходке прямо сейчас (0 — не сидит)."""
    started = _voice_since.get((guild_id, user_id))
    if not started:
        return 0
    return max(0, int(time.time() - started))


def voice_drop(guild_id: int, user_id: int) -> None:
    """Перестать считать время без записи (например, ушёл в отгул)."""
    _voice_since.pop((guild_id, user_id), None)


def voice_reset_tracking() -> None:
    _voice_since.clear()


# ── Топ и место ───────────────────────────────────────────────────────────────

def top(guild_id: int, limit: int = 10):
    """[(user_id, entry)] по монетам."""
    guild = _data().get(str(guild_id), {})
    rows = []
    for uid, info in guild.items():
        get_entry(guild_id, int(uid))
        rows.append((int(uid), info))
    rows.sort(key=lambda kv: (int(kv[1].get("coins", 0)), int(kv[1].get("verified_total", 0))), reverse=True)
    return rows[:limit]


def place(guild_id: int, user_id: int) -> int:
    """Место по монетам, 0 — если не в списке."""
    guild = _data().get(str(guild_id), {})
    mine = int(get_entry(guild_id, user_id).get("coins", 0))
    better = 0
    for uid, info in guild.items():
        if int(uid) == user_id:
            continue
        if int(info.get("coins", 0)) > mine:
            better += 1
    return better + 1
