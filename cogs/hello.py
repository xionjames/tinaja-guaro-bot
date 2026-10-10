from discord.ext import commands


class Hello(commands.Cog):
    @commands.command(name='hello')
    async def hello(self, ctx):
        await ctx.send('World')
