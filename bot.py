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

    @bot.event
    async def on_ready():
        logger.info("Залогинились как %s (id=%s)", bot.user, bot.user.id)
        await bot.load_extension("cogs.verify")
        logger.info("Cog verify загружен")

        if guild_id:
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info("Синкнуто %d команд на гильдию %d", len(synced), guild_id)
        else:
            synced = await bot.tree.sync()
            logger.info("Синкнуто %d глобальных команд", len(synced))

    await bot.start(token)
