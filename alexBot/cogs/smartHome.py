import asyncio
import json
import logging
import typing
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional

import aiohttp
import aiomqtt
import discord
from aiomqtt.types import PayloadType
from discord.ext import tasks


from ..tools import Cog, get_json, render_voiceState

if TYPE_CHECKING:
    from alexBot.cogs.voicePrivVC import VoicePrivVC
    from alexBot.cogs.mqttDispatcher import HomeAssistantIntigreation
    from bot import Bot


log = logging.getLogger(__name__)


TABLE = defaultdict(lambda: "💨")
TABLE['not_home'] = TABLE['not_home']  # add entry for .items of the default value
TABLE['home'] = "🏠"
TABLE['Walmart'] = "🏪"
TABLE["Tiny Home"] = "🛖"
TABLE["Terry Residence"] = "🧓"
TABLE["Weed Shop"] = "🌿"
TABLE["The UPS Store"] = "📦"
TABLE["Mat-Su Regional"] = "🏥"

GUILD = 384843279042084865
MEMBERS = {  # ha-name: (discord-id, [guild-ids])
    'alex': (108429628560924672, [384843279042084865, 1220224297235251331]),
    'garrett': (326410251546918913, [384843279042084865]),
    'abby': (
        253233185800847361,
        [384843279042084865],
    ),
    'tierra': (270066588638511105, [384843279042084865]),
}

NEWLINE = '\n'
USER_TO_HA_DEVICE = {
    108429628560924672: 'mobile_app_pixel_7_pro',
    326410251546918913: 'mobile_app_game_s_phone',
    253233185800847361: 'mobile_app_entry_plug',
}
#  TODO: put this in the database and MEMBERS too


DEVICE_IDENTIFIER = {
    "device": {
        "name": "AlexBot",
        "manufacturer": "Alex from Alaska",
        "model": "Discord Bot",
        "sw_version": "1.0.0",
    },
}


class PhoneMonitor(Cog):
    def __init__(self, bot: "Bot"):
        super().__init__(bot)
        self.notifiable: List[int] = list(USER_TO_HA_DEVICE.keys())

    @discord.app_commands.command(name="ha-vc-notifs", description="Toggle voice channel notifications for your phone")
    @discord.app_commands.guilds(GUILD)
    async def ha_vc_notifs(self, interaction: discord.Interaction):
        if interaction.user.id not in USER_TO_HA_DEVICE:
            await interaction.response.send_message("You can not use this command", ephemeral=True)
            return
        if interaction.user.id in self.notifiable:
            self.notifiable.remove(interaction.user.id)
            await interaction.response.send_message(
                "You will no longer be notified of voice channel changes", ephemeral=True
            )
            log.debug(f"Removing {interaction.user.id} from notifiable")
        else:
            self.notifiable.append(interaction.user.id)
            log.debug(f"Adding {interaction.user.id} to notifiable")
            await interaction.response.send_message("You will now be notified of voice channel changes", ephemeral=True)

    @Cog.listener()
    async def on_ha_update_location(self, name: str, location: PayloadType):
        log.info(f"HA update: {name} -> {location}")
        await self.bot.wait_until_ready()

        if name in MEMBERS:
            for gid in MEMBERS[name][1]:
                log.debug(f"Checking {name} in {gid}")
                g = self.bot.get_guild(gid)
                if not g:
                    log.debug(f"Skipping {name} because {gid} is not a valid guild")
                    continue
                member = g.get_member(MEMBERS[name][0])
                if not member:
                    log.debug(f"Skipping {name} because {MEMBERS[name][0]} is not a valid member in {g}")
                    continue
                g_name = member.display_name
                for _, locator in TABLE.items():
                    g_name = g_name.rstrip(locator)

                g_name += TABLE[location]
                log.info(f"Changing {member.display_name} in {g} to {g_name}")
                try:
                    await member.edit(nick=g_name)
                except discord.errors.Forbidden as e:
                    log.debug(e)
                    continue  # permission fault, probably because server owner
            if MEMBERS[name][0] not in self.notifiable and location == "Walmart":
                self.notifiable.append(MEMBERS[name][0])
                log.info(f"Adding {member.display_name} to notifiable for being at walmart")

    @Cog.listener()
    async def on_ha_vc_control(self, userId: int, command: str):
        log.info(f"HA vc control: {userId} -> {command}")
        await self.bot.wait_until_ready()
        user = self.bot.get_user(userId)
        if not user:
            return
        targets = [g for g in user.mutual_guilds if g.get_member(user.id).voice]
        if not targets:
            return

        member = targets[0].get_member(user.id)
        assert member
        assert member.voice
        try:
            if command == 'mute':
                await member.edit(mute=not member.voice.mute)
            elif command == 'deafen':
                await member.edit(deafen=not member.voice.deaf, mute=not member.voice.deaf)
            elif command == 'disconnect':
                await member.edit(deafen=False, mute=False)
                await member.move_to(None)
        except discord.errors.Forbidden as e:
            return

    async def update_mqtt_state(self, member: discord.Member, after: discord.VoiceState):
        mqtt: HomeAssistantIntigreation = self.bot.get_cog("HomeAssistantIntigreation")  # type: ignore
        jsonblob = {"state_str": render_voiceState(member)}

        for key in ['self_deaf', 'self_mute', 'self_stream', 'self_video', 'mute', 'deaf']:
            jsonblob[key] = getattr(member.voice, key)
        await mqtt.mqttPublish(f"discord/{member.id}/voice", json.dumps(jsonblob))

        states_templates = [
            ('self_deaf', 'Self Deafened'),
            ('self_mute', 'Self Muted'),
            ('mute', 'Server Muted'),
            ('deaf', 'Server Deafened'),
            ('self_stream', 'Streaming'),
            ('self_video', 'Video Active'),
        ]
        device = self.get_device(member)
        for key, name in states_templates:
            payload = {
                "device": {**device},
                "name": name,
                "state_topic": f"discord/{member.id}/voice",
                "value_template": f"{{{{ value_json.{key} }}}}",
                "unique_id": f"discord-{member.id}-voice-{key}",
                "payload_on": True,
                "payload_off": False,
            }
            await mqtt.mqttPublish(
                f"homeassistant/binary_sensor/alexBot/{member.id}-voice-{key}/config", json.dumps(payload)
            )

        payload = {
            "device": {**device},
            "name": "Voice State",
            "state_topic": f"discord/{member.id}/voice",
            "value_template": "{{ value_json.state_str }}",
            "unique_id": f"discord-{member.id}-voice-state",
        }
        await mqtt.mqttPublish(f"homeassistant/sensor/alexBot/{member.id}-voice-state/config", json.dumps(payload))

    @Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        channel: discord.VoiceChannel = before.channel or after.channel  # type: ignore
        if before.channel and after.channel and before.channel == after.channel:
            log.debug(f"no one moved in {channel.name}")
            # no one moved, check if user acted on is notifiable
            if member.id in self.notifiable:
                log.debug(
                    f"checking {member.display_name} in {channel.guild.name} ({channel.guild.id}) for self_voice changes"
                )
                await self.update_mqtt_state(member, after)
                log.debug(f"{before=} {after=}")
                # find the differences between before.voice and after.voice
                # if there is a difference, send a notification
                # if there is no difference, ignore
                message = ""
                if before.self_mute != after.self_mute:
                    message += f"you were {'' if after.self_mute else 'un'}muted\n"
                if before.self_deaf != after.self_deaf:
                    message += f"you were {'' if after.self_deaf else 'un'}deafened\n"
                if before.mute != after.mute:
                    message += f"you were {'' if after.mute else 'un'}server muted\n"
                if before.deaf != after.deaf:
                    message += f"you were {'' if after.deaf else 'un'}server deafened\n"
                # if before.self_video != after.self_video:
                #     message += f"you {'started' if after.self_video else 'ended'} video\n"
                # if before.self_stream != after.self_stream:
                #     message += f"you {'started' if after.self_stream else 'ended'} streaming\n"
                log.debug(f"message: {message}")

                if message == "":
                    return

                message = render_voiceState(member) + message
                await self.send_notification(member.id, message, channel.members)
                return

            else:
                return

        voiceLog: VoicePrivVC = self.bot.get_cog('VoiceLog')
        if voiceLog:
            if voiceLog.beingShaken.get(member.id) is not None:
                return  # ignore person being shaken

        for user in self.notifiable:
            oldAfter = after.channel
            log.debug(f"checking {user}")
            SELF_MOVED = user == member.id
            message = None
            memberList: List[discord.Member] = []
            if not (targetMember := channel.guild.get_member(user)):
                return  # user not in server
            if after.channel and not after.channel.permissions_for(targetMember).view_channel:
                after.channel = None
            log.debug(f"checking {member.display_name} in {channel.guild.name} ({channel.guild.id})")
            log.debug(f"before: {before.channel} after: {after.channel}")
            log.debug(
                f"before.member: {before.channel.members if before.channel else None} after.members: {after.channel.members if after.channel else None}"
            )

            if before.channel and not after.channel and user == member.id:
                # our current person left chat
                # because channel.members on before is after change members, we need to insert the user into before
                # we're just going to manually assign the message and memberList
                log.debug(f"{member.display_name} left {before.channel.name}")
                message = f"{member.display_name} left {before.channel.name}"
                memberList = before.channel.members

            message, memberList = self._handle_different_channels(member, before, after, user, SELF_MOVED, message, memberList)

            message, memberList = self._handle_leave_chat(member, before, after, user, message, memberList)
            if not before.channel and after.channel and user in [user.id for user in after.channel.members]:
                # person joined chat
                log.debug(f"{member.display_name} joined {after.channel.name}")
                message = f"{member.display_name} joined {after.channel.name}"
                memberList = after.channel.members

            if message:
                message = render_voiceState(targetMember) + message
                await self.send_notification(user, message, memberList)

            after.channel = oldAfter

    def _handle_different_channels(self, member, before, after, user, SELF_MOVED, message, memberList):
        if before.channel and after.channel and (before.channel != after.channel):
            if user in [user.id for user in before.channel.members]:
                    # person left chat to another channel in server
                log.debug(f"{member.display_name} moved from {before.channel.name} to {after.channel.name}")
                message = f"{member.display_name} was moved to {after.channel.name}"
                memberList = before.channel.members
            if user in [user.id for user in after.channel.members]:
                    # person joined chat from another channel in server
                if SELF_MOVED:
                    log.debug(f"Self moved from {before.channel.name} to {after.channel.name}")
                    message = f"you were moved to {after.channel.name}"
                else:
                    log.debug(f"{member.display_name} moved from {before.channel.name} to {after.channel.name}")
                    message = f"{member.display_name} joined {after.channel.name}"

                memberList = after.channel.members
        return message, memberList

    def _handle_leave_chat(self, member, before, after, user, message, memberList):
        if before.channel and not after.channel and user in [user.id for user in before.channel.members]:
                # person left chat
            log.debug(f"{member.display_name} left {before.channel.name}")
            message = f"{member.display_name} left {before.channel.name}"
            memberList = before.channel.members
        
        return message, memberList

    async def send_notification(self, user_id: int, title: str, members: List[discord.Member]):
        log.debug(f"title: {title}")
        content = f"Current members in your channel are:\n{NEWLINE.join([f'{m.display_name} {render_voiceState(m)}' for m in members])}"

        log.debug(f"message content: {content}")
        webhook_target = self.bot.config.ha_webhook_notifs
        if webhook_target:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_target,
                    json={
                        "content": content,
                        "title": title,
                        "discord_id": user_id,
                        "channel": "vcNotifs",
                        "group": "vcNotifs",
                    },
                ) as r:
                    log.debug(f"webhook response: {r.status}")


    @staticmethod
    def get_device(user: discord.Member) -> Dict:
        return {
            "name": f"Discord: {user.global_name}",
            "identifiers": f"discord-{user.id}",
            "manufacturer": "Alex from Alaska",
            "model": "Discord Bot",
            "sw_version": "1.0.0",
            "via_device": "alexBot",
        }


async def setup(bot: "Bot"):
    await bot.add_cog(PhoneMonitor(bot))
