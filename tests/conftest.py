from __future__ import annotations
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
os.environ.setdefault('BOT_TOKEN', 'test_token_' + 'x' * 50)
os.environ.setdefault('BOT_OWNER_ID', '123456789012345678')

@pytest.fixture(scope='session')
def event_loop():
    _a = asyncio.get_event_loop_policy().new_event_loop()
    yield _a
    _a.close()

@pytest.fixture
def base_config():
    from core.config import NonobotConfig
    return NonobotConfig()

@pytest_asyncio.fixture
async def db_pool(_c):
    from database.connection import DatabasePool
    from core.config import DatabaseConfig
    _a = DatabaseConfig(backend='sqlite', sqlite_path=str(_c / 'test.db'))
    _b = DatabasePool(_a)
    await _b.initialise()
    yield _b
    await _b.close()

@pytest.fixture
def mock_bot(base_config, db_pool):
    _a = MagicMock()
    _a.config = base_config
    _a.db = db_pool
    _a.guilds = []
    _a.user = MagicMock(id=999999999)
    _a.user.id = 999999999
    _a.get_channel = MagicMock(return_value=None)
    _a.get_guild = MagicMock(return_value=None)
    _a.latency = 0.05
    return _a

@pytest.fixture
def mock_guild():
    _a = MagicMock()
    _a.id = 111111111111111111
    _a.name = 'Test Guild'
    _a.member_count = 42
    _a.default_role = MagicMock()
    _a.roles = []
    _a.text_channels = []
    _a.voice_channels = []
    _a.categories = []
    return _a

@pytest.fixture
def mock_member(mock_guild):
    _a = MagicMock()
    _a.id = 222222222222222222
    _a.name = 'testuser'
    _a.display_name = 'testuser'
    _a.bot = False
    _a.guild = mock_guild
    _a.roles = []
    _a.guild_permissions = MagicMock(administrator=False)
    _a.display_avatar = MagicMock(url='https://example.com/avatar.png')
    _a.mention = f'<@{_a.id}>'
    return _a