import datetime
import os
import uuid

import discord

# from alexBot.classes import
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Interval, String, select
from sqlalchemy.dialects.postgresql import BIGINT, UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column, relationship

import config
from alexBot.classes import SugeryZone
from alexBot.tools import time_cache

from pytz import tzinfo


class Base(
    MappedAsDataclass,
    DeclarativeBase,
):
    pass


from typing import Optional, Union

# HOWTO: make database changes

#  make sure you have the database up (docker-compose up -d db)
# 1. make changes inside this file to match the new schema
# 2. Add a new table or column to the database, using alembic:
#  `alembic revision --autogenerate --head head -m "MESSAGE"`
#  edit the generated file in alembic/versions
#  `alembic upgrade head`


class VoiceStat(Base):
    __tablename__ = "voiceStats"
    id: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    longest_session: Mapped[datetime.timedelta] = mapped_column(Interval(), default=datetime.timedelta(seconds=0))
    currently_running: Mapped[bool] = mapped_column(Boolean(), default=False)
    average_duration: Mapped[datetime.timedelta] = mapped_column(Interval(), default=datetime.timedelta(seconds=1))
    total_sessions: Mapped[int] = mapped_column(Integer(), default=0)
    recently_ended: Mapped[bool] = mapped_column(Boolean(), default=False)
    last_started: Mapped[datetime.datetime] = mapped_column(DateTime(), default_factory=datetime.datetime.now)


class Reminder(Base):
    """a reminder. can be recurring (set frequency) or one time (no frequency) target is the id of the channel that the reminder is sent in."""

    __tablename__ = "reminders"
    target: Mapped[int] = mapped_column(BIGINT(), nullable=False)
    owner: Mapped[int] = mapped_column(BIGINT(), nullable=False)
    guildId: Mapped[Optional[int]] = mapped_column(BIGINT(), nullable=True)
    message: Mapped[str] = mapped_column(String(), nullable=False)
    next_remind: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    frequency: Mapped[Optional[datetime.timedelta]] = mapped_column(Interval(), nullable=True)
    require_clearing: Mapped[bool] = mapped_column(Boolean(), default=False)
    auto_react: Mapped[bool] = mapped_column(Boolean(), default=False)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)

    @time_cache(300)
    def user_can_manage_reminder(self, user: Union[discord.Member, discord.User]):
        if self.owner == user.id:
            return True
        if isinstance(user, discord.Member) and user.guild.id == self.guildId:
            return user.guild_permissions.manage_guild
        return False


class GuildConfig(Base):
    __tablename__ = "guildconfigs"
    __config_keys__ = [
        "ayy",
        "tikTok",
        "veryCool",
        "collectVoiceData",
        "firstAmendment",
        "privateOnePersonVCs",
        "transcribeVoiceMessages",
        "minecraft",
        "allowUnMuteAndDeafenOnJoin",
    ]
    __config_docs__ = {
        "ayy": "sending `ayy` responds with  `lmao` is enabled",
        "veryCool": "starting a message with `thank you ` responds with `very cool`",
        "tikTok": "the video reposter is enabled",
        "collectVoiceData": "voice data is collected",
        "firstAmendment": "saying `free speech` or `first amendment` responds with the XKCD comic # 1357",
        "privateOnePersonVCs": "joining a VC with a usercap=1 gives you perms to move people into it",
        "transcribeVoiceMessages": "voice messages are transcribed in chat",
        "minecraft": "the minecraft server ip for the default to /minecraft",
        "allowUnMuteAndDeafenOnJoin": "users can be un-server-muted and deafened when joining a VC",
    }
    guildId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    ayy: Mapped[bool] = mapped_column(Boolean(), default=False)
    veryCool: Mapped[bool] = mapped_column(Boolean(), default=False)
    tikTok: Mapped[bool] = mapped_column(Boolean(), default=False)
    collectVoiceData: Mapped[bool] = mapped_column(Boolean(), default=True)
    firstAmendment: Mapped[bool] = mapped_column(Boolean(), default=False)
    privateOnePersonVCs: Mapped[bool] = mapped_column(Boolean(), default=False)
    transcribeVoiceMessages: Mapped[bool] = mapped_column(Boolean(), default=False)
    minecraft: Mapped[str] = mapped_column(String(), default="")
    allowUnMuteAndDeafenOnJoin: Mapped[bool] = mapped_column(Boolean(), server_default="false", default=False)


class UserConfig(Base):
    __tablename__ = "userconfigs"
    __config_keys__ = [
        "ringable",
        "collectVoiceData",
        "voiceModel",
        "voiceSleepMute",
        "dontVoiceSleep",
        "unMuteAndDeafenOnJoin",
        "timeZone",
    ]
    __config_docs__ = {
        "ringable": "you can be rung using /ring",
        "collectVoiceData": "your voice data is collected",
        "voiceModel": "the model used to transcribe your voice using /voice tts",
        "voiceSleepMute": "you are muted when /voice sleep is used",
        "dontVoiceSleep": "you are deafened when /voice sleep is used",
        "unMuteAndDeafenOnJoin": "if you are un-server-muted and deafened when you join a voice channel",
        "timeZone": "your timezone for time related features",
    }
    userId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    ringable: Mapped[bool] = mapped_column(Boolean(), default=True)
    hasBeenRung: Mapped[bool] = mapped_column(Boolean(), default=False)
    collectVoiceData: Mapped[bool] = mapped_column(Boolean(), default=True)
    voiceModel: Mapped[str] = mapped_column(String(), server_default="", default="")
    voiceSleepMute: Mapped[bool] = mapped_column(Boolean(), default=False)
    dontVoiceSleep: Mapped[bool] = mapped_column(Boolean(), default=False)
    unMuteAndDeafenOnJoin: Mapped[bool] = mapped_column(Boolean(), server_default="false", default=False)
    timeZone: Mapped[str] = mapped_column(String(), server_default="UTC", default="UTC")
    use24HourTime: Mapped[bool] = mapped_column(Boolean(), default=False)


class VoiceName(Base):
    __tablename__ = "voicenames"
    userId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    channelId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    name: Mapped[str] = mapped_column(String())


class SugeryZoneNames(Base):
    __tablename__ = "sugeryzonenames"
    VERYLOW: Mapped[str] = mapped_column(String())
    LOW: Mapped[str] = mapped_column(String())
    NORMAL: Mapped[str] = mapped_column(String())
    HIGH: Mapped[str] = mapped_column(String())
    VERYHIGH: Mapped[str] = mapped_column(String())
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)

    def __getitem__(self, item):
        if isinstance(item, SugeryZone):
            return getattr(self, item.name)
        elif isinstance(item, str):
            return getattr(self, item)
        else:
            raise TypeError(f"expected SugeryZone or str, got {type(item)}")


class Thresholds(Base):
    __tablename__ = "suthresholds"
    veryHigh: Mapped[int] = mapped_column(Integer())
    high: Mapped[int] = mapped_column(Integer())
    low: Mapped[int] = mapped_column(Integer())
    veryLow: Mapped[int] = mapped_column(Integer())
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)


class SugeryUser(Base):
    __tablename__ = "sugeryusers"
    guildId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    userId: Mapped[int] = mapped_column(BIGINT(), primary_key=True)
    baseURL: Mapped[str]
    names: Mapped[SugeryZoneNames] = relationship(foreign_keys='SugeryUser.namesId', lazy="selectin")
    namesId: Mapped[uuid.UUID] = mapped_column(ForeignKey('sugeryzonenames.id'), init=False)
    constantAlerts: Mapped[Optional[int]] = mapped_column(BIGINT(), nullable=True, init=False)
    alertsTranslationsId: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey(SugeryZoneNames.id), init=False)
    alertsTranslations: Mapped[Optional[SugeryZoneNames]] = relationship(
        foreign_keys=[alertsTranslationsId], init=False, lazy="selectin"
    )
    lastGroup: Mapped[SugeryZone] = mapped_column(default=SugeryZone.NORMAL, init=False)
    thresholdsId: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey(Thresholds.id), init=False)
    thresholds: Mapped[Optional[Thresholds]] = relationship(
        foreign_keys='SugeryUser.thresholdsId', init=False, lazy="selectin"
    )


class MudaeSeriesRequest(Base):
    __tablename__ = "mudaeSeriesRequests"
    series: Mapped[str] = mapped_column(String(), primary_key=True)
    requestedBy: Mapped[int] = mapped_column(BIGINT(), nullable=False, primary_key=True)


class CommandUsage(Base):
    __tablename__ = "commandusage"
    command_name: Mapped[str] = mapped_column(String(), primary_key=True)
    count: Mapped[int] = mapped_column(Integer(), default=0)


user = config.db_user
pw = config.db_pw
db = config.db_name
db_host = config.db_host
db_port = config.db_port
database_url = config.db_full_url or f"postgresql+asyncpg://{user}:{pw}@{db_host}:{db_port}/{db}"
if database_url and database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif config.db_full_url is None:
    if None in (user, pw, db, db_host, db_port):
        raise ValueError("Missing database environment variable")

engine = create_async_engine(database_url)
async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
