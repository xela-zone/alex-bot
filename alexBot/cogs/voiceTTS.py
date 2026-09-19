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
    queue: asyncio.Queue = dataclasses.field(default_factory=asyncio.Queue)
    play_event: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    worker_task: Optional[asyncio.Task] = None
    disconnect_task: Optional[asyncio.Task] = None


class VoiceTTS(Cog):
    def __init__(self, bot):
        super().__init__(bot)
        self.runningTTS: Dict[int, TTSInstance] = {}
        self.gtts: Optional[AsyncGTTSSession] = None

    async def _delayed_disconnect(self, guild_id: int):
        try:
            await asyncio.sleep(15)
            instance = self.runningTTS.get(guild_id)
            if not instance:
                return
            if not instance.voiceClient.is_connected():
                log.info(f"Bot remained disconnected from voice in guild {guild_id} after debounce; cleaning up.")
                await self._cleanup_guild(guild_id)
            else:
                log.info(f"Bot reconnected to voice in guild {guild_id}; keeping session alive.")
        except asyncio.CancelledError:
            log.info(f"Delayed disconnect task cancelled for guild {guild_id}.")
            raise
        finally:
            instance = self.runningTTS.get(guild_id)
            if instance and instance.disconnect_task == asyncio.current_task():
                instance.disconnect_task = None

    async def _cleanup_guild(self, guild_id: int):
        instance = self.runningTTS.pop(guild_id, None)
        if instance:
            if (
                instance.disconnect_task
                and not instance.disconnect_task.done()
                and instance.disconnect_task is not asyncio.current_task()
            ):
                instance.disconnect_task.cancel()
                instance.disconnect_task = None
            if instance.worker_task and not instance.worker_task.done():
                instance.worker_task.cancel()
            while not instance.queue.empty():
                try:
                    item = instance.queue.get_nowait()
                    if hasattr(item, "cleanup"):
                        item.cleanup()
                    instance.queue.task_done()
                except Exception:
                    break
            if instance.voiceClient:
                conn = getattr(instance.voiceClient, "_connection", None)
                runner = getattr(conn, "_runner", None)
                if runner and not runner.done():
                    runner.cancel()
                try:
                    await asyncio.wait_for(instance.voiceClient.disconnect(force=True), timeout=5.0)
                except Exception as e:
                    log.warning(f"Error disconnecting voice client in guild {guild_id}: {e}")
                    try:
                        instance.voiceClient.cleanup()
                    except Exception:
                        pass

        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client:
            conn = getattr(guild.voice_client, "_connection", None)
            runner = getattr(conn, "_runner", None)
            if runner and not runner.done():
                runner.cancel()
            try:
                await asyncio.wait_for(guild.voice_client.disconnect(force=True), timeout=5.0)
            except Exception as e:
                log.warning(f"Error disconnecting orphaned guild voice client in {guild_id}: {e}")
                try:
                    guild.voice_client.cleanup()
                except Exception:
                    pass

    async def _queue_worker(self, guild_id: int, instance: TTSInstance):
        try:
            while True:
                item = await instance.queue.get()
                try:
                    if not instance.voiceClient.is_connected():
                        log.warning(f"Voice client for guild {guild_id} not connected; waiting for reconnection...")
                        for _ in range(30):
                            if instance.voiceClient.is_connected():
                                break
                            await asyncio.sleep(0.5)
                        if not instance.voiceClient.is_connected():
                            log.error(f"Voice client for guild {guild_id} failed to reconnect. Dropping audio.")
                            if hasattr(item, "cleanup"):
                                item.cleanup()
                            continue

                    if isinstance(item, (bytes, bytearray)):
                        buff_sound = io.BytesIO(item)
                        try:
                            sound = discord.FFmpegOpusAudio(buff_sound, pipe=True)
                        except Exception as e:
                            log.exception(f"Failed to create FFmpegOpusAudio in guild {guild_id}: {e}")
                            continue
                    elif isinstance(item, io.BytesIO):
                        try:
                            sound = discord.FFmpegOpusAudio(item, pipe=True)
                        except Exception as e:
                            log.exception(f"Failed to create FFmpegOpusAudio in guild {guild_id}: {e}")
                            continue
                    else:
                        sound = item

                    instance.play_event.clear()

                    def after_playback(error: Optional[Exception]):
                        if error:
                            log.exception(f"TTS audio playback error in guild {guild_id}: {error}")
                        self.bot.loop.call_soon_threadsafe(instance.play_event.set)

                    # Defuse Root Cause B: stop latched player if still marked playing
                    if instance.voiceClient.is_playing():
                        try:
                            instance.voiceClient.stop()
                        except Exception:
                            pass

                    try:
                        instance.voiceClient.play(sound, after=after_playback)
                    except discord.ClientException:
                        if instance.voiceClient.is_connected():
                            log.warning(
                                f"Voice client in guild {guild_id} latched during play; forcing stop and retrying."
                            )
                            try:
                                instance.voiceClient.stop()
                            except Exception:
                                pass
                            try:
                                instance.voiceClient.play(sound, after=after_playback)
                            except Exception:
                                if hasattr(sound, "cleanup"):
                                    sound.cleanup()
                                raise
                        else:
                            if hasattr(sound, "cleanup"):
                                sound.cleanup()
                            raise
                    except Exception:
                        if hasattr(sound, "cleanup"):
                            sound.cleanup()
                        raise

                    try:
                        await asyncio.wait_for(instance.play_event.wait(), timeout=60.0)
                    except asyncio.TimeoutError:
                        log.warning(f"TTS playback timed out in guild {guild_id}; stopping player.")
                        try:
                            instance.voiceClient.stop()
                        except Exception:
                            pass
                except Exception as e:
                    log.exception(f"Error during TTS playback in guild {guild_id}: {e}")
                finally:
                    instance.queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception(f"TTS queue worker crashed unexpectedly for guild {guild_id}: {e}")

    async def cog_load(self):
        if not self.bot.config.google_service_account:
            log.error("No google service account found. voiceTTS will not be loaded")
            return

        self.gtts = AsyncGTTSSession.from_service_account(
            ServiceAccount.from_service_account_dict(self.bot.config.google_service_account),
        )

        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(name="tts", description="setup text to speech", callback=self.vc_tts)
        )

        await self.gtts.__aenter__()

    async def cog_unload(self) -> None:
        self.bot.voiceCommandsGroup.remove_command("tts")
        for guild_id in list(self.runningTTS.keys()):
            await self._cleanup_guild(guild_id)
        if self.gtts:
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
            content = LINKREGEX.sub(r"Link to \1", content)
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
        guild = member.guild
        if not guild or guild.id not in self.runningTTS:
            return

        instance = self.runningTTS[guild.id]

        # 1. If the bot itself was disconnected from voice
        if member.id == self.bot.user.id:
            if after.channel is None:
                log.info(f"Bot voice state channel is None in {guild.name}; scheduling debounce check.")
                if instance.disconnect_task is None or instance.disconnect_task.done():
                    instance.disconnect_task = self.bot.loop.create_task(
                        self._delayed_disconnect(guild.id)
                    )
            return

        # 2. If a registered TTS user changed voice state
        if member.id in instance.users:
            if after.channel != instance.voiceClient.channel:
                log.info(f"TTS user {member.display_name} left bot's channel in {guild.name}.")
                del instance.users[member.id]

                # Check if all registered TTS users have left
                if len(instance.users) == 0:
                    log.info(f"All TTS users left in {guild.name}. Disconnecting voice client.")
                    await self._cleanup_guild(guild.id)
                    return

    async def sendTTS(self, text: str, ttsInstance: TTSInstance, ttsUser: TTSUserInstance):
        if not self.gtts or not ttsInstance.voiceClient:
            return
        log.debug(f"Sending TTS: {text=}")
        try:
            synth_bytes = await self.gtts.synthesize(
                TextSynthesizeRequestBody(SynthesisInput(text), voice_input=ttsUser.vsParams)
            )
        except Exception as e:
            log.exception(e)
            return

        await ttsInstance.queue.put(synth_bytes)

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
        if not await self._vc_tts_validation(interaction, model):
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

        tts_user = TTSUserInstance(
            VoiceSelectionParams(language_code=model[:5], name=model),
            interaction.channel,
        )

        if interaction.guild.id not in self.runningTTS:
            await interaction.response.defer(ephemeral=False)
            vc = interaction.guild.voice_client
            if vc is not None:
                if vc.is_connected() and vc.channel == interaction.user.voice.channel:
                    log.info(f"Reusing existing connected voice client in guild {interaction.guild.id}")
                    try:
                        vc.stop()
                    except Exception:
                        pass
                else:
                    log.warning(f"Cleaning up orphaned voice client in guild {interaction.guild.id}")
                    conn = getattr(vc, "_connection", None)
                    runner = getattr(conn, "_runner", None)
                    if runner and not runner.done():
                        runner.cancel()
                    try:
                        await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
                    except Exception as e:
                        log.warning(f"Error disconnecting orphaned voice client: {e}")
                        try:
                            vc.cleanup()
                        except Exception:
                            pass
                    try:
                        vc = await interaction.user.voice.channel.connect()
                    except discord.ClientException as e:
                        log.warning(f"ClientException after orphan disconnect: {e}; forcing cleanup and retrying")
                        if interaction.guild.voice_client:
                            interaction.guild.voice_client.cleanup()
                        vc = await interaction.user.voice.channel.connect()
            else:
                try:
                    vc = await interaction.user.voice.channel.connect()
                except discord.ClientException as e:
                    log.warning(f"ClientException connecting to voice channel: {e}; forcing cleanup and retrying")
                    if interaction.guild.voice_client:
                        interaction.guild.voice_client.cleanup()
                    vc = await interaction.user.voice.channel.connect()

            instance = TTSInstance(vc, users={interaction.user.id: tts_user})
            instance.worker_task = self.bot.loop.create_task(
                self._queue_worker(interaction.guild.id, instance)
            )
            self.runningTTS[interaction.guild.id] = instance
            await interaction.followup.send(
                "TTS is now enabled for you. leaving the voice channel will end your tts.\n\nIf you start a message with //, it will be ignored."
            )
        else:
            # theres already a vc running, we need to make sure someone doesn't want us in two places at once
            if interaction.user.id in self.runningTTS[interaction.guild.id].users:
                await interaction.response.send_message(
                    "You already have tts enabled. leaving the voice channel will end your tts.", ephemeral=True
                )
                return
            if (
                self.runningTTS[interaction.guild.id].voiceClient.channel
                and interaction.user.voice.channel.id != self.runningTTS[interaction.guild.id].voiceClient.channel.id
            ):
                await interaction.response.send_message(
                    "You are not in the same voice channel as the existing session. can not start.", ephemeral=True
                )
                return
            self.runningTTS[interaction.guild.id].users[interaction.user.id] = tts_user
            await interaction.response.send_message(
                "TTS is now enabled for you. leaving the voice channel will end your tts.\n\nIf you start a message with //, it will be ignored.",
                ephemeral=False,
            )

    async def _vc_tts_validation(self, interaction, model):

        valid = True

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a guild", ephemeral=True)
            valid = False

        elif interaction.user.voice is None:
            await interaction.response.send_message("You are not in a voice channel", ephemeral=True)
            valid = False

        elif interaction.guild_id in self.runningTTS:
            instance = self.runningTTS[interaction.guild_id]
            if interaction.user.id in instance.users and model == "QUIT":
                del instance.users[interaction.user.id]
                await interaction.response.send_message("ended your voice tts session.", ephemeral=True)
                if len(instance.users) == 0:
                    await self._cleanup_guild(interaction.guild_id)
                valid = False
            elif (
                instance.voiceClient.channel
                and interaction.user.voice.channel.id != instance.voiceClient.channel.id
            ):
                await interaction.response.send_message(
                    "You are not in the same voice channel as the existing session. can not start.", ephemeral=True
                )
                valid = False

        return valid


async def setup(bot):
    if not discord.opus.is_loaded():
        for name in [
            ctypes.util.find_library("opus"),
            "libopus.so.0",
            "libopus.so",
            "libopus.0.dylib",
            "opus",
        ]:
            if not name:
                continue
            try:
                discord.opus.load_opus(name)
                break
            except Exception:
                pass

        if not discord.opus.is_loaded():
            import glob

            for match in glob.glob("/nix/store/*libopus*/lib/libopus.so.0"):
                try:
                    discord.opus.load_opus(match)
                    break
                except Exception:
                    pass

    if not discord.opus.is_loaded():
        log.error("Could not load opus library; not loading voiceTTS module")
        return

    if not getattr(bot.config, "google_service_account", None):
        log.error("No google service account found. voiceTTS will not be loaded")
        return

    try:
        cog = VoiceTTS(bot)
        await bot.add_cog(cog)
    except Exception as e:
        log.exception(e)
        log.error("not loading voiceTTS module")
        return
