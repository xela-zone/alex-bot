#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config

cogs = [
    x.stem
    for x in Path('alexBot/cogs').glob('*.py')
    if x.stem
    not in [
        "__init__",
    ]
]
# cogs = ['reminders', 'errors']  # used to test single cog at a time

log = logging.getLogger('alexBot')
log.setLevel(logging.INFO)

handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
handler.setFormatter(
    logging.Formatter('[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
)

logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)

for logPath in ['discord', 'websockets', 'aiosqlite']:
    z = logging.getLogger(logPath)
    z.setLevel(logging.INFO)
for logPath in ['sqlalchemy', 'discord.gateway']:
    z = logging.getLogger(logPath)
    z.setLevel(logging.ERROR)
if os.environ.get("DEBUG_VOICE"):
    logging.getLogger('discord.voice_state').setLevel(logging.DEBUG)

LINKWRAPPERREGEX = re.compile(r'(http[s]?://(?:[a-zA-Z]|[0-9]|[#-_]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)', re.I)


intents = discord.Intents.default()
intents.message_content = True


allowed_mentions = discord.AllowedMentions.none()


class Bot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(command_prefix=config.prefix, intents=intents, allowed_mentions=allowed_mentions, **kwargs)
        self.session = None
        self.config = config

        self.owner = None
        self.setup_hook = self.cogSetup
        self.minecraft = True
        self.handler = handler
        self.log = log
        self.voiceCommandsGroup = app_commands.Group(
            name="voice", description="Voice related commands", guild_only=True
        )
        self.tree.add_command(self.voiceCommandsGroup)

    async def on_ready(self):
        log.info(f'Logged on as {self.user} ({self.user.id}). prefix is {config.prefix}')
        self.owner = (await self.application_info()).owner
        log.info(f'owner is {self.owner} ({self.owner.id})')
        self.session = aiohttp.ClientSession()

    async def cogSetup(self):
        await self.load_extension('jishaku')

        for cog in cogs:
            try:
                await self.load_extension(f"alexBot.cogs.{cog}")
                log.info(f'loaded {cog}')
            except (commands.ExtensionNotFound
                    , commands.ExtensionAlreadyLoaded
                    , commands.NoEntryPointError
                    , commands.ExtensionFailed) as e:
                log.error(f'Could not load extension {cog} due to {e.__class__.__name__}: {e}')
                log.exception(e)

    @staticmethod
    def clean_mentions(content: str) -> str:
        content = content.replace('`', '\'')
        content = content.replace('@', '@\u200b')
        content = content.replace('&', '&\u200b')
        content = content.replace('<#', '<#\u200b')
        return content

    @staticmethod
    def clean_formatting(content: str) -> str:
        content = content.replace('_', '\\_')
        content = content.replace('*', '\\*')
        content = content.replace('`', '\\`')
        return content

    @staticmethod
    def clean_links(content: str) -> str:
        content = LINKWRAPPERREGEX.sub(r'<\1>', content)
        return content

    def clean_clean(self, content):
        content = self.clean_mentions(content)
        content = self.clean_formatting(content)
        content = self.clean_links(content)
        return content

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        else:
            await self.process_commands(message)

    async def on_command(self, ctx):
        # thanks dogbot ur a good
        content = ctx.message.content
        content = self.clean_mentions(content)

        author = ctx.message.author
        guild = ctx.guild
        checks = [c.__qualname__.split('.')[0] for c in ctx.command.checks]
        location = '[DM]' if isinstance(ctx.channel, discord.DMChannel) else f'[Guild {guild.name} {guild.id}]'

        log.info('%s [cmd] %s(%d) "%s" checks=%s', location, author, author.id, content, ','.join(checks) or '(none)')


bot = Bot()

# if we're running on windows:
if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


bot.run(config.discord_token, log_handler=None)
