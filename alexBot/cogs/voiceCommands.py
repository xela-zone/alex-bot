import asyncio
from typing import List, Optional

import discord
from discord import VoiceState, app_commands

from alexBot.database import GuildConfig, UserConfig, async_session, select
from alexBot.tools import Cog, render_voiceState


class VoiceCommands(Cog):
    def __init__(self, bot):
        super().__init__(bot)
        self.current_thatars = []
        self.unmute_noops = {}

    async def cog_load(self):
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="shake",
                description="'shake' a user in voice as a fruitless attempt to get their attention.",
                callback=self.vcShake,
            )
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="disconnect",
                description="Disconnect yourself from the voice channel you're in.",
                callback=self.vc_disconnect,
            )
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="move_me",
                description="Move you to another channel",
                callback=self.vc_move,
            )
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="move",
                description="Moves the current group to another voice channel.",
                callback=self.voice_move,
            )
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="theatre",
                description="create a temporary channel for watching videos with friends",
                callback=self.voice_theatre,
            )
        )

        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="act",
                description="slash command proxy for the Server Mute, Deafen and disconnect commands",
                callback=self.slash_command_proxy,
            )
        )
        self.bot.voiceCommandsGroup.add_command(
            app_commands.Command(
                name="sleep",
                description="deafen everyone in the channel, and mute people who req to be muted via userconfig",
                callback=self.sleep,
            )
        )

    async def cog_unload(self):
        self.bot.voiceCommandsGroup.remove_command("shake")
        self.bot.voiceCommandsGroup.remove_command("disconnect")
        self.bot.voiceCommandsGroup.remove_command("move_me")
        self.bot.voiceCommandsGroup.remove_command("move")
        self.bot.voiceCommandsGroup.remove_command("theatre")
        self.bot.voiceCommandsGroup.remove_command("act")
        self.bot.voiceCommandsGroup.remove_command("sleep")

    async def user_in_same_vc(self, interaction: discord.Interaction, guess: str):
        if interaction.user.voice is None:
            return [app_commands.Choice(name="err: not in a voice channel", value="0")]

        return [
            app_commands.Choice(name=m.display_name, value=str(m.id)) for m in interaction.user.voice.channel.members
        ]

    async def voice_action_autocomplete(self, interaction: discord.Interaction, guess: str):
        if interaction.namespace.target:
            # there's a target, we can do more specific info
            member = interaction.guild.get_member(int(interaction.namespace.target))
            if member is None:
                return [app_commands.Choice(name="err: invalid target", value="0")]
            if member.voice is None:
                return [app_commands.Choice(name="err: target not in voice", value="0")]
            opts = [app_commands.Choice(name="disconnect", value="disconnect")]
            opts.append(app_commands.Choice(name="unmute" if member.voice.mute else "mute", value="mute"))
            opts.append(app_commands.Choice(name="undeafen" if member.voice.deaf else "deafen", value="deafen"))
            return opts
        return [
            app_commands.Choice(name="disconnect", value="disconnect"),
            app_commands.Choice(name="mute", value="mute"),
            app_commands.Choice(name="deafen", value="deafen"),
        ]

    @app_commands.checks.bot_has_permissions(move_members=True, mute_members=True, deafen_members=True)
    @app_commands.checks.has_permissions(move_members=True, mute_members=True, deafen_members=True)
    @app_commands.autocomplete(target=user_in_same_vc, action=voice_action_autocomplete)
    async def slash_command_proxy(self, interaction: discord.Interaction, target: str, action: str):
        member = interaction.guild.get_member(int(target))
        if not member:
            await interaction.response.send_message("invalid target", ephemeral=True)
            return
        if member.voice is None:
            await interaction.response.send_message("target is not in a voice channel", ephemeral=True)
            return

        if action == 'mute':
            await member.edit(mute=not member.voice.mute)
        elif action == 'deafen':
            await member.edit(deafen=not member.voice.deaf)
        elif action == 'disconnect':
            await member.edit(deafen=False, mute=False)
            await member.move_to(None)
        return await interaction.response.send_message(
            f"ok, {member.mention} is now {render_voiceState(member)}",
            ephemeral=member == interaction.user,
            allowed_mentions=discord.AllowedMentions.all(),
        )

    @app_commands.checks.bot_has_permissions(move_members=True)
    @app_commands.checks.has_permissions(move_members=True)
    @app_commands.describe(target="the voice channel to move everyone to")
    async def voice_move(self, interaction: discord.Interaction, target: discord.VoiceChannel):
        if not interaction.user.voice:
            return await interaction.response.send_message("you must be in a voice call!", ephemeral=True)
        await interaction.response.defer()
        for user in interaction.user.voice.channel.members:
            asyncio.get_event_loop().create_task(user.move_to(target, reason=f"as requested by {interaction.user}"))
        await interaction.followup.send(":ok_hand:", ephemeral=True)

    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def voice_theatre(self, interaction: discord.Interaction, name: Optional[str]):
        if name is None:
            name = "Theatre"
        target_catagory = None
        if interaction.user.voice:
            # we have a voice channel, is it in a category?
            if interaction.user.voice.channel.category:
                target_catagory = interaction.user.voice.channel.category
        if target_catagory is None:
            # if the current channel is in a catagory, put it there
            target_catagory = interaction.channel.category

        chan = await interaction.guild.create_voice_channel(name=name, category=target_catagory)
        self.current_thatars.append(chan.id)
        await interaction.response.send_message(f"Created a new theatre channel, {chan.mention}", ephemeral=False)
        await asyncio.sleep(5 * 60)
        try:
            chan: discord.VoiceChannel = await self.bot.fetch_channel(chan.id)
            if chan is not None:
                if len(chan.members) == 0:
                    await chan.delete()
        except discord.NotFound:
            pass

    @Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: VoiceState, after: VoiceState):
        if before.channel and before.channel.id in self.current_thatars:
            if len(before.channel.members) == 0:
                await before.channel.delete(reason="no one left")
                self.current_thatars.remove(before.channel.id)
        guild = member.guild
        if (before.channel and after.channel is None) or (before.channel is None and after.channel is not None):
            # person joined / left
            async with async_session() as session:
                gc = await session.scalar(select(GuildConfig).where(GuildConfig.guildId == guild.id))
                if not gc:
                    gc = GuildConfig(guild.id)
                    session.add(gc)
                    await session.commit()
                if gc.allowUnMuteAndDeafenOnJoin:  # server allows it
                    uc = await session.scalar(select(UserConfig).where(UserConfig.userId == member.id))
                    if not uc:
                        uc = UserConfig(member.id)
                        session.add(uc)
                        await session.commit()
                    if uc.unMuteAndDeafenOnJoin:  # user wants it
                        if before.channel is None and after.channel is not None and member.id not in self.unmute_noops:
                            # initial join, we can just blindly unmute and undeafen
                            # but first we need to wait a moment for the user to be actually connected and accept the mute/deafen
                            await (await guild.fetch_member(member.id)).edit(mute=False, deafen=False)
                        elif before.channel is not None and after.channel is None:
                            # they left, we need to add them to our list of no-ops for 5 minutes
                            self.unmute_noops[member.id] = True
                            await asyncio.sleep(5 * 60)
                            del self.unmute_noops[member.id]

    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(move_members=True)
    async def vc_disconnect(self, interaction: discord.Interaction):
        """Disconnects you from voice"""
        if interaction.guild is None:
            await interaction.response.send_message("this command can only be used in a server", ephemeral=True)
            return
        if interaction.user.voice is None:
            await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
            return
        if interaction.user.voice.channel is None:
            await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
            return
        if interaction.user.voice.channel.guild != interaction.guild:
            await interaction.response.send_message("you're not in this server's voice channel", ephemeral=True)
            return
        try:
            await interaction.user.move_to(None)
        except:
            await interaction.response.send_message("i can't disconnect you from voice", ephemeral=True)
            return
        await interaction.response.send_message("ok, bye", ephemeral=True)

    @app_commands.guild_only()
    async def vc_move(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        """Moves you to another voice channel"""
        if interaction.guild is None:
            await interaction.response.send_message("this command can only be used in a server", ephemeral=True)
            return
        if not interaction.user.voice:
            await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
            return
        if interaction.user.voice.channel is None:
            await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
            return
        if interaction.user.voice.channel.guild != interaction.guild:
            await interaction.response.send_message("you're not in this server's voice channel", ephemeral=True)
            return
        if channel.permissions_for(interaction.user).connect is False:
            await interaction.response.send_message(
                "you don't have permission to connect to that channel", ephemeral=True
            )
            return
        await interaction.user.move_to(channel)
        await interaction.response.send_message(f"ok, moved you to {channel}", ephemeral=True)

    async def target_autocomplete(self, interaction: discord.Interaction, guess: str) -> List[app_commands.Choice]:
        if interaction.user.voice is None:
            return [app_commands.Choice(name="err: not in a voice channel", value="0")]
        channel: discord.VoiceChannel | discord.StageChannel | None = await self._get_channel(interaction)
        if channel is None or interaction.user.voice.channel == channel:
            return [app_commands.Choice(name="err: no suitable shake channel found", value="0")]

        valid_targets = [
            m
            for m in interaction.user.voice.channel.members
            if not m.bot and not m.id == interaction.user.id and not m.voice.self_stream
        ]
        if len(valid_targets) == 0:
            return [app_commands.Choice(name="err: no valid targets", value="0")]
        return [
            app_commands.Choice(name=m.display_name, value=str(m.id))
            for m in valid_targets
            if guess in m.display_name.lower() or guess in m.name.lower()
        ]

    @app_commands.guild_only()
    @app_commands.autocomplete(target=target_autocomplete)
    async def vcShake(self, interaction: discord.Interaction, target: str):
        """'shake' a user in voice as a fruitless attempt to get their attention."""
        target: int = int(target)
        if not interaction.guild.me.guild_permissions.move_members:
            await interaction.response.send_message("I don't have the permissions to do that.")
            return

        if target == 0:
            await interaction.response.send_message("invalid target", ephemeral=True)
            return

        if interaction.user.voice is None:
            await interaction.response.send_message("you are not in a voice channel", ephemeral=True)
            return
        channel: discord.VoiceChannel | discord.StageChannel | None = await self._get_channel(interaction)
        if channel is None:
            return

        valid_targets = [
            m
            for m in interaction.user.voice.channel.members
            if not m.bot and not m.id == interaction.user.id and not m.voice.self_stream
        ]

        user = interaction.guild.get_member(int(target))
        if user is None or user not in valid_targets:
            await interaction.response.send_message("invalid target", ephemeral=True)
            return

        currentChannel = interaction.user.voice.channel
        AFKChannel = channel

        await interaction.response.send_message(
            f"shaking {user.mention}...",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        voiceLog = self.bot.get_cog("VoiceLog")
        if voiceLog:
            voiceLog.beingShaken[user.id] = False
        if interaction.guild.id == 791528974442299412:
            await interaction.guild.get_channel(974472799093661826).send(
                f"shaking {user.display_name} for {interaction.user.display_name}"
            )

        for _ in range(4):
            await user.move_to(AFKChannel, reason=f"shake requested by {interaction.user.display_name}")
            await user.move_to(currentChannel, reason=f"shake requested by {interaction.user.display_name}")
        if voiceLog:
            del voiceLog.beingShaken[user.id]

    async def _get_channel(self, interaction):
        channel: discord.VoiceChannel | discord.StageChannel | None = interaction.guild.afk_channel
        if channel is None:
            if interaction.user.voice.channel.category is not None:
                for chan in interaction.user.voice.channel.category.channels:
                    if (
                        (isinstance(chan, discord.VoiceChannel) or isinstance(chan, discord.StageChannel))
                        and len(chan.members) == 0
                        and chan.permissions_for(interaction.user).view_channel
                    ):
                        channel = chan
                        break
            if channel is None:
                for chan in interaction.guild.voice_channels:
                    if len(chan.members) == 0 and chan.permissions_for(interaction.user).view_channel:
                        channel = chan
                        break
            if channel is None:
                for chan in interaction.guild.stage_channels:
                    if len(chan.members) == 0 and chan.permissions_for(interaction.user).view_channel:
                        channel = chan
                        break
            if channel is None:
                await interaction.response.send_message("No suitable channel to shake into found", ephemeral=True)
                return None

            return None

        return channel

    @app_commands.checks.bot_has_permissions(mute_members=True, deafen_members=True)
    @app_commands.checks.has_permissions(mute_members=True, deafen_members=True)
    async def sleep(self, interaction: discord.Interaction):
        # deafen everyone in the channel, and mute people who req to be muted via userconfig
        if interaction.user.voice is None:
            return await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
        vc = interaction.user.voice.channel
        if vc is None:
            # this should never happen tbh
            return await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
        await interaction.response.defer(thinking=True)
        async with async_session() as session:
            uds = await session.scalars(select(UserConfig).where(UserConfig.userId.in_([z.id for z in vc.members])))
            for user in vc.members:
                # get the user config for this user
                ud = next((x for x in uds if x.userId == user.id), None)
                if not ud:
                    ud = UserConfig(user.id)

                if ud.voiceSleepMute and ud.dontVoiceSleep:
                    await user.edit(reason=f"sleep requested by {interaction.user}", mute=True)
                elif ud.voiceSleepMute and not ud.dontVoiceSleep:
                    await user.edit(reason=f"sleep requested by {interaction.user}", mute=True, deafen=True)
                elif not ud.voiceSleepMute and ud.dontVoiceSleep:
                    await user.edit(reason=f"sleep requested by {interaction.user}", deafen=True)
                elif not ud.voiceSleepMute and not ud.dontVoiceSleep:
                    pass  # do nothing

        await interaction.followup.send("ok, sleep well :zzz:", ephemeral=False)


async def setup(bot):
    await bot.add_cog(VoiceCommands(bot))
