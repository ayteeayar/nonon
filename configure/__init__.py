from configure.cog import ConfigureCog

async def setup(_a) -> None:
    await _a.add_cog(ConfigureCog(_a))