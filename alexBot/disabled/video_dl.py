import asyncio
from collections import namedtuple
from dataclasses import dataclass
import io
import itertools
import logging
import math
import os
import re
import subprocess
import traceback
from functools import partial
from typing import List, Optional, Tuple

import aiohttp
import discord
from discord.errors import DiscordException
from discord.ext import commands
from discord import app_commands
from slugify import slugify
from sqlalchemy import select

from alexBot import database as db
from alexBot.cobalt import Cobalt, RequestBody

from ..tools import Cog, timing

from mimetypes import guess_extension

log = logging.getLogger(__name__)


DOMAINS = [
    "x.com",
    "twitter.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "reddit.com",
]

MAX_VIDEO_LENGTH = 5 * 60  # 5 Minutes
AUDIO_BITRATE = 64 * 1000  # 64 Kbits
BUFFER_CONSTANT = 20  # Magic number, see https://unix.stackexchange.com/a/598360

FFPROBE_CMD = 'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 in.mp4'
FFMPEG_CMD = 'ffmpeg -i in.mp4 -y -b:v {0} -maxrate:v {0} -b:a {1} -maxrate:a {1} -bufsize:v {2} out.mp4'

# fake headers for firefox
FAKE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "TE": "trailers",
}


#  -loglevel 8
class NotAVideo(Exception):
    pass


@dataclass
class ImageAndExtension:
    image: bytes
    extension: str


class Video_DL(Cog):

    def __init__(self, bot):
        self.encode_lock = asyncio.Lock()
        self.mirror_upload_lock = asyncio.Lock()
        self._cobalt: Optional[Cobalt] = None
        super().__init__(bot)

        self.videoDLRequestMenu = app_commands.ContextMenu(
            name='Manual Video Mirror',
            callback=self.video_download_request,
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        )

    async def cog_load(self) -> None:
        commands = [
            self.videoDLRequestMenu,
        ]
        for command in commands:
            self.bot.tree.add_command(command)

    async def cog_unload(self) -> None:
        commands = [
            self.videoDLRequestMenu,
        ]
        for command in commands:
            self.bot.tree.remove_command(command.name, type=command.type)

    async def video_download_request(self, interaction: discord.Interaction, message: discord.Message):
        # check for a valid video
        match = None
        for domain in DOMAINS:
            log.debug(f"Searching for domain: {domain} in message content")
            if match := re.search(rf'(https?://[^ ]*{domain}/[^ \n]*)', message.content):
                if domain in ["youtube.com", "youtu.be"]:
                    if 'shorts' in match.group(1) or 'clip' in match.group(1):
                        log.debug("Match found for youtube SHORTS / CLIPS domain. Breaking loop.")
                        break
                else:
                    log.debug(f"Match found for domain: {domain}. Breaking loop.")
                    break
        if not match:
            return await interaction.response.send_message("No video found in message content.", ephemeral=True)

        await interaction.response.defer(ephemeral=False)
        try:
            attachmentss = await self.download_video(
                match.group(1), interaction.guild.filesize_limit if interaction.guild else 8_000_000
            )
        except Exception as e:
            log.error("Error processing video from direct interaction", e)
            await interaction.followup.send(content=f"Error: {e}", ephemeral=True)
        for attachments in attachmentss:
            await interaction.followup.send(files=attachments)

    @staticmethod
    async def fetch_image(url: str, session: aiohttp.ClientSession, count=0) -> ImageAndExtension:
        if count > 3:
            raise Exception("Too many retries fetching image")
        async with session.get(url) as response:
            if not response.ok:
                await asyncio.sleep(1)
                return await Video_DL.fetch_image(url, session, count + 1)
            if not response.content_type:
                raise Exception("No content type found")
            return ImageAndExtension(await response.read(), guess_extension(response.content_type) or 'jpg')

    async def get_cobalt_instace(self) -> Cobalt:
        if self._cobalt:
            return self._cobalt
        self._cobalt = Cobalt()

        async def test_cobalt():
            while self._cobalt:
                await asyncio.sleep(60 * 15)
                try:
                    await self._cobalt.get_server_info()
                except Exception as e:
                    log.error("Error testing cobalt", e)
                    self._cobalt = None

        self.bot.loop.create_task(test_cobalt())
        return self._cobalt

    async def download_video(self, url: str, size_limit: int) -> List[List[discord.File]]:
        cobalt = await self.get_cobalt_instace()
        rq = RequestBody(url=url, alwaysProxy=True)
        res = await cobalt.process(rq)
        async with aiohttp.ClientSession(headers=cobalt.headers) as session:
            match res.status:
                case "tunnel" | "redirect":
                    # download the stream to reupload to discord
                    log.debug("Status is stream or tunnel. Downloading the stream.")
                    async with session.get(res.url) as response:
                        stuff = await response.read()
                        if not response.content_disposition:
                            raise NotAVideo("No content disposition found.")
                        bytes = io.BytesIO(stuff)
                        if len(bytes.getvalue()) > size_limit:
                            async with self.encode_lock:
                                task = partial(self.transcode_shrink, bytes, size_limit)
                                bytes = await self.bot.loop.run_in_executor(None, task)

                        filename = response.content_disposition.filename
                        if not filename:
                            words = response.url.path.split("/")[-1]
                            words += "." + (guess_extension(response.content_type) or "mp4")
                            filename = words

                        return [[discord.File(bytes, filename)]]

                case "picker":
                    # gotta download the photos to post, batch by 10's
                    log.debug("Status is picker. Downloading the photos.")
                    if not res.picker:
                        raise NotAVideo("No pickers found.")
                    images = await asyncio.gather(*[self.fetch_image(each.url, session) for each in res.picker])

                    return [
                        [
                            discord.File(io.BytesIO(image.image), f"{n+(m*10)}.{image.extension}")
                            for n, image in enumerate(group)
                        ]
                        for m, group in enumerate(itertools.batched(images, 10))
                    ]

                case "error":
                    log.error(f"Error in cobalt with url {rq.url}: {res.error}")
                    raise Exception(f"Error in cobalt with url {rq.url}: {res.error}")

    @Cog.listener()
    async def on_message(self, message: discord.Message, interaction: Optional[discord.Interaction] = None):
        log.debug("on_message function started")
        if message.guild is None:
            log.debug("Message is from a guild or from the bot user. Returning without processing.")
            return
        gc = None
        async with db.async_session() as session:
            log.debug("Starting DB session")
            gc = await session.scalar(select(db.GuildConfig).where(db.GuildConfig.guildId == message.guild.id))
            if not gc:
                # create one
                log.debug("GuildConfig not found. Creating a new one.")
                gc = db.GuildConfig(guildId=message.guild.id)
                session.add(gc)
                await session.commit()
        if not gc.tikTok and not interaction:
            log.debug("tikTok is not enabled for this guild. Returning without processing.")
            return

        # find the link to the video (first only)
        match = None
        for domain in DOMAINS:
            log.debug(f"Searching for domain: {domain} in message content")
            if match := re.search(rf'(https?://[^ ]*{domain}/[^ \n]*)', message.content):
                if domain in ["youtube.com", "youtu.be"]:
                    if 'shorts' in match.group(1) or 'clip' in match.group(1):
                        log.debug("Match found for youtube SHORTS / CLIPS domain. Breaking loop.")
                        break
                else:
                    log.debug(f"Match found for domain: {domain}. Breaking loop.")
                    break

        if not match:
            log.debug("No matching domain found in message content. Returning without processing.")
            return

        async with message.channel.typing():
            log.debug("Typing indicator started")

        # REMOVED FROM HERE
        try:
            Attachment_sets = await self.download_video(match.group(1), message.guild.filesize_limit)
        except NotAVideo:
            log.debug("Not a video. Returning without processing.")
            return
        except Exception as e:
            log.error("Error downloading video", e)
            return
        try:
            messages = [await message.reply(files=attachment_set) for attachment_set in Attachment_sets]
        except Exception as e:
            log.error("Error uploading video", e)
            return
        if messages[-1]:
            uploaded = messages[-1]
            try:
                await uploaded.add_reaction("🗑️")
            except DiscordException:
                return
            # try:
            #     await message.edit(suppress=True)
            # except DiscordException:
            #     pass

            def check(reaction: discord.Reaction, user: discord.User):
                return reaction.emoji == "🗑️" and user.id in [message.author.id] and reaction.message.id == uploaded.id

            try:
                await self.bot.wait_for('reaction_add', timeout=60 * 5, check=check)
            except asyncio.TimeoutError:
                await uploaded.remove_reaction("🗑️", self.bot.user)
            else:
                # if we are here, someone with the power to do so want's to delete the upload
                await uploaded.delete()

    @staticmethod
    @timing(log=log)
    def transcode_shrink(content: io.BytesIO, limit: float) -> io.BytesIO:
        limit = limit * 8
        try:
            with open("in.mp4", "wb") as f:
                f.write(content.getvalue())
            fprobe = subprocess.Popen(FFPROBE_CMD.split(' '), stdout=subprocess.PIPE)
            fprobe.wait()
            video_length = math.ceil(float(fprobe.communicate()[0].decode("utf-8")))
            content.seek(0)  # reset data
            if video_length > MAX_VIDEO_LENGTH:
                raise commands.CommandInvokeError('Video is too large.')

            target_total_bitrate = limit / video_length
            buffer_size = math.floor(limit / BUFFER_CONSTANT)
            target_video_bitrate = target_total_bitrate - AUDIO_BITRATE

            command_formatted = FFMPEG_CMD.format(str(target_video_bitrate), str(AUDIO_BITRATE), str(buffer_size))
            log.debug(f"Transcoding video with command: {command_formatted}")
            ffmpeg = subprocess.Popen(command_formatted.split(' '))

            ffmpeg.communicate()[0]
            ffmpeg.wait()
            with open("out.mp4", "rb") as f:
                return io.BytesIO(f.read())
        except Exception as e:
            raise Exception('Exception occurred transcoding video', traceback.format_exc())
        finally:
            try:
                os.remove("out.mp4")
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(Video_DL(bot))
