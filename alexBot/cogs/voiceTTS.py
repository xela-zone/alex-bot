import asyncio
import ctypes
import dataclasses
import io
import logging
import re
from typing import Dict, List, Optional

import discord
from asyncgTTS import AsyncGTTSSession, ServiceAccount, SynthesisInput, TextSynthesizeRequestBody, VoiceSelectionParams
from discord import app_commands

from alexBot.classes import googleVoices
from alexBot.database import UserConfig, async_session, select
from alexBot.tools import Cog

log = logging.getLogger(__name__)


wavenetChoices = [discord.app_commands.Choice(name=f"WaveNet {v[0][-1]} ({v[1]})", value=v[0]) for v in googleVoices]


# TODO:
# - line limit?
# link parsing
# test queue handler
# trademark emoji special case
# custom emojis


# regex to remove spoilers
SPOILERREGEX = re.compile(r"\|\|(.*?)\|\|")
# regex to capture custom emojis (<a?:name:id>)
EMOJIREGEX = re.compile(r"<a?:([a-zA-Z0-9_]+):(\d+)>")

LINKREGEX = re.compile(r"https?://(.+\.[a-z]+)/?[a-zA-Z0-9/\-=+#\?]*")


@dataclasses.dataclass
class TTSUserInstance:
    vsParams: VoiceSelectionParams
    channel: discord.TextChannel


@dataclasses.dataclass
class TTSInstance:
    voiceClient: discord.VoiceClient
    users: Dict[int, TTSUserInstance] = dataclasses.field(default_factory=dict)
    queue: List[discord.AudioSource] = dataclasses.field(default_factory=list)


class VoiceTTS(Cog):
    def __init__(self, bot):
        super().__init__(bot)
        self.runningTTS: Dict[int, TTSInstance] = {}
        self.gtts: AsyncGTTSSession = None  # type: ignore
        self.running_queue_handlers: Dict[int, bool] = {}

    async def cog_load(self):
        if not self.bot.config.google_service_account:
            log.error("No google service account found. voiceTTS will not be loaded")

        self.gtts = AsyncGTTSSession.from_service_account(
            ServiceAccount.from_service_account_dict(self.bot.config.google_service_account),  # type: ignore ; can not be None, checked above
        )

        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(name="tts", description="setup text to speech", callback=self.vc_tts)
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(name="tts_reset", description="force quit the server's tts setup", callback=self.reset_server)
            
        )

        await self.gtts.__aenter__()

    async def reset_server(self,interaction: discord.Interaction):
        if interaction.guild.id in self.runningTTS:
            rtts = self.runningTTS[interaction.guild.id]
            
            if rtts.voiceClient.is_connected():
                await rtts.voiceClient.disconnect()
            del self.runningTTS[interaction.guild.id]
            return await interaction.response.send_message("voice tts has been reset.")
        await interaction.response.send_message("voice tts not running right now.")
        

    async def cog_unload(self) -> None:
        self.bot.voiceCommandsGroup.remove_command("tts")
        self.bot.voiceCommandsGroup.reove_command('tts_reset')
        for vc in self.runningTTS.values():
            await vc.voiceClient.disconnect()
        await self.gtts.__aexit__(None, None, None)

    @Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.guild
            and message.guild.id in self.runningTTS
            and message.author.id in self.runningTTS[message.guild.id].users
            and self.runningTTS[message.guild.id].users[message.author.id].channel.id == message.channel.id
        ):
            if message.content.startswith("//"):
                return
            content = message.clean_content
            content = SPOILERREGEX.sub("", content)
            content = EMOJIREGEX.sub(r"\1", content)
            content = LINKREGEX.sub("Link to \1", content)
            if content == "":
                return
            await self.sendTTS(
                content,
                self.runningTTS[message.guild.id],
                self.runningTTS[message.guild.id].users[message.author.id],
            )

    @Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        ttsInstance = self.runningTTS.get(member.guild.id)
        if not ttsInstance:
            return
        if after.channel is not None:
            return
        # someone left a voice channel. do we care?
        if member.guild and member.guild.id in self.runningTTS and member.id in ttsInstance.users:
            del ttsInstance.users[member.id]

        if self.bot.user.id == member.id and after.channel is None and member.guild.id in self.runningTTS:
            del self.runningTTS[member.guild.id]

        if len(ttsInstance.users) == 0:
            await ttsInstance.voiceClient.disconnect()
            del self.runningTTS[member.guild.id]

    async def sendTTS(self, text: str, ttsInstance: TTSInstance, ttsUser: TTSUserInstance):
        if not ttsInstance.voiceClient.is_connected():
            return
        log.debug(f"Sending TTS: {text=}")
        try:
            synth_bytes = await self.gtts.synthesize(
                TextSynthesizeRequestBody(SynthesisInput(text), voice_input=ttsUser.vsParams)
            )
        except Exception as e:
            log.exception(e)
            return
        buff_sound = io.BytesIO(synth_bytes)

        sound = discord.FFmpegOpusAudio(buff_sound, pipe=True)
        ttsInstance.queue.append(sound)
        if not self.running_queue_handlers.get(ttsInstance.voiceClient.guild.id):
            await self.queue_handler(ttsInstance)

    async def queue_handler(self, instance: TTSInstance):
        self.running_queue_handlers[instance.voiceClient.guild.id] = True
        # make sure we still have a good voice client
        if not instance.voiceClient.is_connected():
            instance.voiceClient = await instance.voiceClient.channel.connect()
        try:
            if instance.voiceClient.is_playing():
                # wait, did we get stuck, or are we just waiting for the next thing?
                await asyncio.sleep(0.3)  # more than .25 to allow for next audio to start
                if instance.voiceClient.is_playing():
                    return
            while len(instance.queue) > 0:
                if instance.voiceClient.is_playing():
                    await asyncio.sleep(0.25)
                    continue
                next = instance.queue.pop(0)
                instance.voiceClient.play(next, after=self.after)
                await asyncio.sleep(0.25)
        finally:
            self.running_queue_handlers[instance.voiceClient.guild.id] = False

    async def model_autocomplete(
        self, interaction: discord.Interaction, guess: str
    ) -> List[discord.app_commands.Choice]:
        # control mode for connected tts users:
        if interaction.guild_id in self.runningTTS:
            if interaction.user.id in self.runningTTS[interaction.guild_id].users:
                return [
                    discord.app_commands.Choice(name="End your Voice TTS", value="QUIT"),
                ]
        chc = []
        async with async_session() as db_session:
            userData = await db_session.scalar(select(UserConfig).where(UserConfig.userId == interaction.user.id))
            if userData and userData.voiceModel and interaction.guild_id:
                if self.runningTTS.get(interaction.guild_id) and userData.voiceModel not in [
                    z[1].vsParams.name for z in self.runningTTS[interaction.guild_id].users.items()
                ]:
                    chc.append(discord.app_commands.Choice(name=f"SAVED ({userData.voiceModel})", value="SAVED"))
        if interaction.guild_id and interaction.guild_id in self.runningTTS:
            instance = self.runningTTS[interaction.guild_id]
            existing = [z.vsParams.name for z in instance.users.values()]
            for choice in wavenetChoices:
                if choice.name not in existing:
                    chc.append(discord.app_commands.Choice(name=choice.name, value=choice.value))
        else:
            chc = [z for z in wavenetChoices]

        return chc

    @app_commands.autocomplete(model=model_autocomplete)
    async def vc_tts(self, interaction: discord.Interaction, model: str):
        if not self._vc_tts_validation(interaction, model):
            return
        
        if model == "SAVED":
            # we pull from database, and use that
            async with async_session() as session:
                userData = await session.scalar(select(UserConfig).where(UserConfig.userId == interaction.user.id))
                if not userData:
                    userData = UserConfig(interaction.user.id)
                    session.add(userData)
                    await session.commit()
                    await interaction.response.send_message(
                        "You have not set a voice preference. use `/config user set` to set one", ephemeral=True
                    )
                    return
                if not userData.voiceModel:
                    await interaction.response.send_message(
                        "You have not set a voice preference. use `/config user set` to set one", ephemeral=True
                    )
                    return
                model = userData.voiceModel

        if model not in [z[0] for z in googleVoices]:
            # check if it's a valid voice overall
            voice_raw = await self.gtts.get_voices()
            names = [z['name'] for z in voice_raw]
            if model not in names:
                await interaction.response.send_message("Invalid voice model", ephemeral=True)
            return

        if interaction.guild.id not in self.runningTTS:
            vc = await interaction.user.voice.channel.connect()
            self.runningTTS[interaction.guild.id] = TTSInstance(vc)
        else:
            # theres already a vc running, we need to make sure someone doesn't want us in two places at once
            if interaction.user.id in self.runningTTS[interaction.guild.id].users:
                await interaction.response.send_message(
                    "You already have tts enabled. leaving the voice channel will end your tts.", ephemeral=True
                )
                return
            if interaction.user.voice.channel.id != self.runningTTS[interaction.guild.id].voiceClient.channel.id:
                await interaction.response.send_message(
                    "You are not in the same voice channel as the existing session. can not start.", ephemeral=True
                )
                return
        self.runningTTS[interaction.guild.id].users[interaction.user.id] = TTSUserInstance(
            VoiceSelectionParams(language_code=model[:5], name=model),
            interaction.channel,
        )

        await interaction.response.send_message(
            "TTS is now enabled for you. leaving the voice channel will end your tts.\n\nIf you start a message with //, it will be ignored.",
            ephemeral=False,
        )

    def after(self, error: Optional[Exception]):
        if error:
            log.exception(error)

    async def _vc_tts_validation(self, interaction, model):

        valid = True

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a guild", ephemeral=True)
            valid = False

        elif interaction.user.voice is None:
            await interaction.response.send_message("You are not in a voice channel", ephemeral=True)
            valid = False

        elif interaction.guild_id in self.runningTTS:
            if interaction.user.id in self.runningTTS[interaction.guild_id].users and model == "QUIT":
                del self.runningTTS[interaction.guild_id].users[interaction.user.id]
                await interaction.response.send_message("ended your voice tts session.", ephemeral=True)
                if len(self.runningTTS[interaction.guild_id].users) == 0:
                    await self.runningTTS[interaction.guild_id].voiceClient.disconnect()
                valid = False
            elif interaction.user.voice.channel.id != self.runningTTS[interaction.guild_id].voiceClient.channel.id:
                await interaction.response.send_message(
                    "You are not in the same voice channel as the existing session. can not start.", ephemeral=True
                )
                valid = False

        return valid


async def setup(bot):
    try:
        discord.opus.load_opus(ctypes.util.find_library("opus"))
    except Exception as e:
        log.exception(e)
        log.error("Could not load opus library")
        log.error('not loading voiceTTS module')
        return
    try:
        cog = VoiceTTS(bot)
        await bot.add_cog(cog)
    except Exception as e:
        log.exception(e)
        log.error('not loading voiceTTS module')
        return
