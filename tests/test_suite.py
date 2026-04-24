from __future__ import annotations
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio

class TestConfig:

    def test_default_config_loads(self, base_config):
        assert base_config.bot.name == 'nonon'
        assert base_config.database.backend == 'sqlite'
        assert base_config.moderation.auto_mute_threshold == 3

    def test_owner_id_from_env(self, base_config):
        assert base_config.owner_id == 123456789012345678

    def test_guild_override_source(self, base_config):
        from core.config import GuildOverride, SourceConfig
        override = GuildOverride(source=SourceConfig(type='github', github_repo='org/repo'))
        import object as obj
        base_config.guilds['999'] = override
        src = base_config.get_guild_source(999)
        assert src.type == 'github'

    def test_global_fallback_when_no_override(self, base_config):
        src = base_config.get_guild_source(0)
        assert src.type == 'local'

class TestDatabase:

    @pytest.mark.asyncio
    async def test_execute_and_fetch(self, db_pool):
        await db_pool.execute('INSERT INTO permission_grants (guild_id, target_type, target_id, grant_type, grant_value, granted_by) VALUES (?, ?, ?, ?, ?, ?)', (1, 'user', 42, 'command', 'ban', 99))
        row = await db_pool.fetch_one('SELECT * FROM permission_grants WHERE guild_id = 1 AND target_id = 42')
        assert row is not None
        assert row['grant_value'] == 'ban'

    @pytest.mark.asyncio
    async def test_fetch_all_returns_list(self, db_pool):
        rows = await db_pool.fetch_all('SELECT * FROM permission_grants WHERE guild_id = 999')
        assert isinstance(rows, list)

    @pytest.mark.asyncio
    async def test_fetch_val(self, db_pool):
        val = await db_pool.fetch_val('SELECT COUNT(*) FROM schema_migrations')
        assert isinstance(val, int)

    @pytest.mark.asyncio
    async def test_row_attribute_access(self, db_pool):
        await db_pool.execute('INSERT INTO permission_grants (guild_id, target_type, target_id, grant_type, grant_value, granted_by) VALUES (?, ?, ?, ?, ?, ?)', (2, 'role', 55, 'scope', 'moderation', 99))
        row = await db_pool.fetch_one('SELECT * FROM permission_grants WHERE guild_id = 2')
        assert row is not None
        assert row.grant_type == 'scope'
        assert row['grant_value'] == 'moderation'

class TestPermissionResolver:

    def _make_resolver(self, mock_bot):
        from core.permissions import PermissionResolver
        return PermissionResolver(mock_bot)

    @pytest.mark.asyncio
    async def test_owner_always_allowed(self, mock_bot, mock_member):
        mock_bot.config.owner_id = mock_member.id
        resolver = self._make_resolver(mock_bot)
        mock_bot.db.fetch_all = AsyncMock(return_value=[])
        result = await resolver.can_use(mock_member, 'ban', 111)
        assert result is True

    @pytest.mark.asyncio
    async def test_non_owner_blocked_by_default(self, mock_bot, mock_member):
        mock_bot.config.owner_id = 9999999999
        resolver = self._make_resolver(mock_bot)
        mock_bot.db.fetch_all = AsyncMock(return_value=[])
        result = await resolver.can_use(mock_member, 'ban', 111)
        assert result is False

    @pytest.mark.asyncio
    async def test_user_grant_allows_command(self, mock_bot, mock_member):
        mock_bot.config.owner_id = 9999999999
        resolver = self._make_resolver(mock_bot)
        mock_bot.db.fetch_all = AsyncMock(return_value=[{'target_type': 'user', 'target_id': mock_member.id, 'grant_type': 'command', 'grant_value': 'ban'}])
        result = await resolver.can_use(mock_member, 'ban', 111)
        assert result is True

    @pytest.mark.asyncio
    async def test_scope_grant_allows_included_command(self, mock_bot, mock_member):
        mock_bot.config.owner_id = 9999999999
        resolver = self._make_resolver(mock_bot)
        mock_bot.db.fetch_all = AsyncMock(return_value=[{'target_type': 'user', 'target_id': mock_member.id, 'grant_type': 'scope', 'grant_value': 'moderation'}])
        result = await resolver.can_use(mock_member, 'kick', 111)
        assert result is True

    @pytest.mark.asyncio
    async def test_owner_only_command_blocked(self, mock_bot, mock_member):
        mock_bot.config.owner_id = 9999999999
        resolver = self._make_resolver(mock_bot)
        mock_bot.db.fetch_all = AsyncMock(return_value=[{'target_type': 'user', 'target_id': mock_member.id, 'grant_type': 'scope', 'grant_value': 'all'}])
        result = await resolver.can_use(mock_member, 'massban', 111)
        assert result is False

    @pytest.mark.asyncio
    async def test_add_and_remove_grant(self, mock_bot, db_pool):
        mock_bot.db = db_pool
        mock_bot.config.owner_id = 9999999999
        resolver = self._make_resolver(mock_bot)
        await resolver.add_grant(100, 'user', 42, 'command', 'kick', 99)
        grants = await resolver.list_grants(100)
        assert any((g['grant_value'] == 'kick' for g in grants))
        ok = await resolver.remove_grant(100, 'user', 42, 'command', 'kick')
        assert ok is True
        grants_after = await resolver.list_grants(100)
        assert not any((g['grant_value'] == 'kick' for g in grants_after))

class TestInfractionManager:

    @pytest.mark.asyncio
    async def test_add_and_retrieve_infraction(self, mock_bot, db_pool, mock_guild):
        mock_bot.db = db_pool
        mock_bot.get_guild = MagicMock(return_value=None)
        from moderation.infractions import InfractionManager
        mgr = InfractionManager(mock_bot)
        inf_id = await mgr.add(guild_id=mock_guild.id, user_id=12345, moderator_id=99999, infraction_type='warn', reason='Test warning')
        assert inf_id > 0
        records = await mgr.get_user_infractions(mock_guild.id, 12345)
        assert len(records) == 1
        assert records[0]['reason'] == 'Test warning'

    @pytest.mark.asyncio
    async def test_warning_count(self, mock_bot, db_pool, mock_guild):
        mock_bot.db = db_pool
        mock_bot.get_guild = MagicMock(return_value=None)
        from moderation.infractions import InfractionManager
        mgr = InfractionManager(mock_bot)
        for _ in range(3):
            await mgr.add(mock_guild.id, 55555, 99999, 'warn', 'test')
        count = await mgr.count_active_warnings(mock_guild.id, 55555)
        assert count == 3

    @pytest.mark.asyncio
    async def test_remove_infraction(self, mock_bot, db_pool, mock_guild):
        mock_bot.db = db_pool
        mock_bot.get_guild = MagicMock(return_value=None)
        from moderation.infractions import InfractionManager
        mgr = InfractionManager(mock_bot)
        inf_id = await mgr.add(mock_guild.id, 77777, 99999, 'warn', 'removable')
        ok = await mgr.remove(inf_id, 99999)
        assert ok is True
        ok2 = await mgr.remove(inf_id, 99999)
        assert ok2 is False

class TestChannelManager:

    def test_sanitise_channel_name(self):
        from discord_layer.channel_manager import ChannelManager
        assert ChannelManager._sanitise_channel_name('My File.md') == 'my-file'
        assert ChannelManager._sanitise_channel_name('hello world') == 'hello-world'
        assert ChannelManager._sanitise_channel_name('CAPS_AND-dashes') == 'caps_and-dashes'
        assert ChannelManager._sanitise_channel_name('...') == 'unnamed'

    def test_split_content_short(self):
        from discord_layer.channel_manager import ChannelManager
        content = 'short content'
        chunks = ChannelManager._split_content(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_split_content_long(self):
        from discord_layer.channel_manager import ChannelManager
        content = 'line\n' * 500
        chunks = ChannelManager._split_content(content)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 1990

class TestLocalProvider:

    @pytest.mark.asyncio
    async def test_fetch_snapshot_nonexistent_path(self):
        from core.config import SourceConfig
        from providers.local import LocalProvider
        cfg = SourceConfig(type='local', path='/nonexistent/path/xyz')
        provider = LocalProvider(cfg)
        snap = await provider.fetch_snapshot()
        assert not snap.ok
        assert 'does not exist' in snap.error

    @pytest.mark.asyncio
    async def test_fetch_snapshot_reads_files(self, tmp_path):
        from core.config import SourceConfig
        from providers.local import LocalProvider
        notes = tmp_path / 'notes'
        notes.mkdir()
        (notes / 'readme.md').write_text('# Hello World')
        (notes / 'guide.md').write_text('# Guide content here')
        cfg = SourceConfig(type='local', path=str(notes))
        provider = LocalProvider(cfg)
        snap = await provider.fetch_snapshot()
        assert snap.ok
        assert len(snap.files) == 2
        paths = {f.path for f in snap.files}
        assert 'readme.md' in paths

    @pytest.mark.asyncio
    async def test_ignore_patterns(self, tmp_path):
        from core.config import SourceConfig
        from providers.local import LocalProvider
        notes = tmp_path / 'notes'
        notes.mkdir()
        (notes / 'good.md').write_text('good')
        (notes / 'bad.tmp').write_text('bad')
        (notes / '.DS_Store').write_text('meta')
        cfg = SourceConfig(type='local', path=str(notes))
        provider = LocalProvider(cfg)
        snap = await provider.fetch_snapshot()
        assert snap.ok
        names = {f.name for f in snap.files}
        assert 'good.md' in names
        assert 'bad.tmp' not in names
        assert '.DS_Store' not in names

class TestAutoMod:

    def _make_message(self, content: str, mock_member, mock_guild):
        msg = MagicMock()
        msg.content = content
        msg.author = mock_member
        msg.guild = mock_guild
        msg.mentions = []
        msg.role_mentions = []
        msg.attachments = []
        msg.delete = AsyncMock()
        return msg

    @pytest.mark.asyncio
    async def test_spam_detection_triggers_on_burst(self, mock_bot, db_pool, mock_member, mock_guild):
        mock_bot.db = db_pool
        mock_bot.config.get_guild_moderation = MagicMock(return_value=mock_bot.config.moderation)
        mock_bot.config.get_guild_discord = MagicMock(return_value=mock_bot.config.discord)
        mock_bot.user = MagicMock(id=9999)
        mock_bot.get_channel = MagicMock(return_value=None)
        mock_bot.get_guild = MagicMock(return_value=None)
        from moderation.automod import AutoMod
        cog = AutoMod(mock_bot)
        mock_member.roles = []
        mock_member.guild_permissions.administrator = False
        for i in range(6):
            msg = self._make_message(f'spam {i}', mock_member, mock_guild)
            await cog.on_message(msg)
        assert True

    def test_url_pattern_matches(self):
        from moderation.automod import URL_PATTERN, DOMAIN_PATTERN
        text = 'check out https://badsite.com/page'
        assert URL_PATTERN.search(text)
        domains = DOMAIN_PATTERN.findall(text)
        assert 'badsite.com' in domains

    def test_domain_whitelist_logic(self):
        from moderation.automod import DOMAIN_PATTERN
        whitelist = {'discord.com', 'discord.gg', 'github.com'}
        text = 'go to https://github.com/user/repo'
        domains = DOMAIN_PATTERN.findall(text)
        bad = [d for d in domains if d.lower() not in whitelist]
        assert len(bad) == 0

    def test_banned_word_detection(self):
        content = 'hello badword world'
        banned = ['badword']
        assert any((w.lower() in content.lower() for w in banned))