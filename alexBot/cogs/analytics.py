
import logging
import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from typing import Union

from ..database import CommandUsage, async_session
from ..tools import Cog

log = logging.getLogger(__name__)

class Analytics(Cog):
    async def _increment_command(self, name: str):
        try:
            async with async_session() as session:
                stmt = insert(CommandUsage).values(command_name=name, count=1)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[CommandUsage.command_name],
                    set_=dict(count=CommandUsage.count + 1)
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            log.error(f"Failed to increment command usage for {name}: {e}")

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        await self._increment_command(ctx.command.qualified_name)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.application_command:
            if interaction.command:
                await self._increment_command(f"/{interaction.command.qualified_name}")

    @commands.command(name="cmdstats")
    @commands.is_owner()
    async def cmdstats(self, ctx: commands.Context):
        async with async_session() as session:
            stmt = select(CommandUsage).order_by(CommandUsage.count.desc()).limit(20)
            result = await session.execute(stmt)
            stats = result.scalars().all()
            
            if not stats:
                return await ctx.send("No stats yet.")
            
            text = "\n".join([f"{s.command_name}: {s.count}" for s in stats])
            await ctx.send(f"Top 20 commands:\n```\n{text}\n```")

async def setup(bot):
    await bot.add_cog(Analytics(bot))
