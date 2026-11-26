# -*- coding: utf-8 -*-
"""Авто-бэкап data/: zip раз в сутки, храним последние 7.

Настройки через env (необязательно):
  BACKUP_INTERVAL_HOURS — как часто делать бэкап, по умолчанию 24
  BACKUP_KEEP           — сколько zip-ов хранить, по умолчанию 7
  BACKUP_CHANNEL_ID     — приватный канал, куда кидать файл бэкапа

Секреты не попадают: config.json и токены лежат вне data/.
"""

import asyncio
import datetime
import logging
import os
import zipfile

import discord

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


KEEP = max(1, _int_env("BACKUP_KEEP", 7))
INTERVAL_HOURS = max(1.0, _float_env("BACKUP_INTERVAL_HOURS", 24))
CHANNEL_ID = _int_env("BACKUP_CHANNEL_ID", 0)


def make_backup() -> str | None:
    """Собрать zip из data/*.json. None — если данных нет."""
    if not os.path.isdir(DATA_DIR):
        return None
    files = [
        name
        for name in sorted(os.listdir(DATA_DIR))
        if os.path.isfile(os.path.join(DATA_DIR, name)) and not name.endswith(".tmp")
    ]
    if not files:
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"data-{stamp}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.write(os.path.join(DATA_DIR, name), arcname=f"data/{name}")
    _prune()
    return path


def _prune() -> None:
    """Оставить KEEP самых свежих архивов (имя начинается с даты — сортируется)."""
    try:
        zips = sorted(
            n for n in os.listdir(BACKUP_DIR)
            if n.startswith("data-") and n.endswith(".zip")
        )
    except OSError:
        return
    for name in zips[:-KEEP] if KEEP else zips:
        try:
            os.remove(os.path.join(BACKUP_DIR, name))
        except OSError:
            pass


def list_backups() -> list:
    """Свежие сначала — для команд/диагностики."""
    try:
        names = [
            n for n in os.listdir(BACKUP_DIR)
            if n.startswith("data-") and n.endswith(".zip")
        ]
    except OSError:
        return []
    return sorted(names, reverse=True)


async def _send_to_channel(bot, path: str) -> None:
    if not CHANNEL_ID:
        return
    ch = bot.get_channel(CHANNEL_ID)
    if ch is None:
        try:
            ch = await bot.fetch_channel(CHANNEL_ID)
        except discord.HTTPException as exc:
            log.warning("Канал бэкапов %s недоступен: %s", CHANNEL_ID, exc)
            return
    try:
        await ch.send(
            content=f"💾 Бэкап data/ — {os.path.basename(path)}",
            file=discord.File(path),
        )
    except discord.HTTPException as exc:
        log.warning("Бэкап не отправлен в канал: %s", exc)


async def backup_loop(bot) -> None:
    """Раз в INTERVAL_HOURS делает zip и (если задан) кидает в канал."""
    while True:
        try:
            path = await bot.loop.run_in_executor(None, make_backup)
            if path:
                log.info("Бэкап data/: %s", path)
                await _send_to_channel(bot, path)
            else:
                log.info("Бэкап пропущен: data/ пуста или отсутствует")
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            log.exception("Бэкап не удался")
        await asyncio.sleep(INTERVAL_HOURS * 3600)
