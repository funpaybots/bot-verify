# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import os

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


def _load_config():
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    guild_id = os.environ.get("GUILD_ID")

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not token:
                token = cfg.get("token")
            if not guild_id:
                guild_id = cfg.get("guild_id")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Не удалось прочитать config.json: %s", e)

    if not token:
        raise RuntimeError(
            "Токен не найден. Укажи DISCORD_BOT_TOKEN / BOT_TOKEN в env или token в config.json"
        )

    guild_id = int(guild_id) if guild_id else None
    return token, guild_id


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token, guild_id = _load_config()

    # Если GUILD_ID не задан через env/config — используем дефолтный
    if not guild_id:
        guild_id = 1573386089467748482

    intents = discord.Intents.default()

    bot = commands.Bot(command_prefix="!", intents=intents)
    bot.guild_id = guild_id
    synced_once = False

    @bot.event
    async def on_ready():
        nonlocal synced_once
        logger.info("Залогинились как %s (id=%s)", bot.user, bot.user.id)
        if synced_once:
            return
        synced_once = True

        try:
            if guild_id:
                guild = discord.Object(id=guild_id)
                bot.tree.copy_global_to(guild=guild)
                # Чистим глобальные копии: иначе Discord показывает по две одинаковые команды
                # (одна глобальная от старых запусков, одна гильдейная).
                bot.tree.clear_commands(guild=None)
                try:
                    await bot.tree.sync()
                except Exception as e:
                    logger.warning("Не смог очистить глобальные команды: %s", e)
                synced = await bot.tree.sync(guild=guild)
                logger.info(
                    "Синкнуто %d команд на гильдию %d: %s",
                    len(synced), guild_id,
                    ", ".join(sorted(c["name"] for c in synced)),
                )
            else:
                synced = await bot.tree.sync()
                logger.info("Синкнуто %d глобальных команд", len(synced))
        except discord.Forbidden:
            logger.error(
                "Синк команд запрещён (Missing Access): проверь, что бот приглашён на гильдию %s "
                "со scope bot+applications.commands.",
                guild_id,
            )
        except Exception as e:
            logger.exception("Не удалось синкнуть команды: %s", e)

    # Коги (и persistent-вьюхи из их setup) грузим ДО подключения к гейтвею.
    for ext in ("cogs.verify", "cogs.staff"):
        await bot.load_extension(ext)
        logger.info("Cog %s загружен", ext.split(".")[-1])
    logger.info("Загружены коги: %s", ", ".join(sorted(bot.cogs)))

    await bot.start(token)
