from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

class BotConfig(BaseModel):
    name: str = 'nonon'
    owner_id_env: str = 'BOT_OWNER_ID'
    command_prefix: str = '!'
    status_text: str = 'the symphony'

class SourceConfig(BaseModel):
    type: str = 'local'
    path: str = './notes'
    poll_interval_seconds: int = Field(default=60, ge=10)
    debounce_seconds: int = Field(default=15, ge=1)
    ignore_patterns: list[str] = ['.git', '__pycache__', '*.tmp', '.DS_Store', '_permissions.yml']
    github_repo: str | None = None
    github_branch: str = 'main'
    github_token_env: str = 'GITHUB_TOKEN'
    ftp_host: str | None = None
    ftp_port: int = 21
    ftp_user_env: str = 'FTP_USER'
    ftp_pass_env: str = 'FTP_PASS'
    ftp_path: str = '/'
    gdrive_folder_id: str | None = None
    gdrive_credentials_env: str = 'GDRIVE_CREDENTIALS_JSON'
    onedrive_drive_id: str | None = None
    onedrive_folder_path: str = '/'
    onedrive_client_id_env: str = 'ONEDRIVE_CLIENT_ID'
    onedrive_client_secret_env: str = 'ONEDRIVE_CLIENT_SECRET'
    onedrive_tenant_id_env: str = 'ONEDRIVE_TENANT_ID'
    sync_allow_replies: bool = False
    sync_reply_delete_after: int = 5

class DiscordConfig(BaseModel):
    guild_id: int = 0
    log_channel_id: int | None = None
    audit_channel_id: int | None = None
    mod_log_channel_id: int | None = None
    archive_channel_id: int | None = None
    voice_log_channel_id: int | None = None
    status_channel_id: int | None = None
    console_channel_id: int | None = None
    command_prefix: str = '!'
    sync_category_prefix: str = ''
    delete_orphaned_channels: bool = False
    dry_run: bool = False
    log_to_guild_id: int | None = None

class PermissionsConfig(BaseModel):
    model_config = {'extra': 'allow'}
    default: dict[str, Any] = Field(default_factory=lambda: {'roles': ['@everyone']})
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

class LoggingConfig(BaseModel):
    level: str = 'INFO'
    retention_days: int = 365
    compress_after_days: int = 30
    log_file: str = './logs/nonon.log'
    json_logs: bool = True
    log_message_edits: bool = True
    log_message_deletes: bool = True
    log_bulk_deletes: bool = True
    log_member_joins: bool = True
    log_member_leaves: bool = True
    log_role_changes: bool = True
    log_nickname_changes: bool = True
    log_avatar_changes: bool = True
    log_username_changes: bool = True
    log_channel_changes: bool = True
    log_role_structure_changes: bool = True
    log_voice_state: bool = True
    log_voice_mute_deafen: bool = True
    log_invite_changes: bool = True
    log_webhook_changes: bool = True
    log_emoji_changes: bool = True
    log_boost_events: bool = True
    reupload_deleted_attachments: bool = True
    max_attachment_size_mb: int = 25
    log_presence_updates: bool = True
    presence_min_interval_seconds: int = 30
    console_relay_enabled: bool = False
    console_relay_level: str = 'INFO'
    console_relay_flush_seconds: float = 1.5
    console_relay_max_lines: int = 30

class ModerationConfig(BaseModel):
    auto_mute_threshold: int = 3
    auto_ban_threshold: int = 5
    mute_duration_minutes: int = 60
    raid_join_threshold: int = 10
    raid_join_window_seconds: int = 10
    max_mentions_per_message: int = 5
    max_lines_per_message: int = 50
    link_whitelist: list[str] = ['discord.com', 'discord.gg', 'github.com']
    banned_words: list[str] = []
    banned_patterns: list[str] = []
    spam_message_threshold: int = 5
    spam_window_seconds: int = 5
    automod_spam_enabled: bool = True
    automod_banned_words_enabled: bool = True
    automod_banned_patterns_enabled: bool = True
    automod_mention_spam_enabled: bool = True
    automod_link_filter_enabled: bool = True
    automod_line_count_enabled: bool = True
    automod_raid_detection_enabled: bool = True
    muted_role_name: str = 'Muted'
    mod_roles: list[str] = ['Moderator', 'Senior Mod']
    admin_roles: list[str] = ['Admin', 'Owner']

class DatabaseConfig(BaseModel):
    backend: str = 'sqlite'
    sqlite_path: str = './data/nonon.db'
    pg_dsn_env: str = 'DATABASE_URL'
    pool_min_size: int = 5
    pool_max_size: int = 20

    @field_validator('backend')
    @classmethod
    def validate_backend(cls, v: str) -> str:
        if v not in ('sqlite', 'postgresql'):
            raise ValueError("database.backend must be 'sqlite' or 'postgresql'")
        return v

class AnalyticsConfig(BaseModel):
    enabled: bool = True
    snapshot_interval_minutes: int = 60
    weekly_summary_day: int = Field(default=0, ge=0, le=6)
    weekly_summary_hour: int = Field(default=9, ge=0, le=23)
    export_path: str = './exports'

class HealthConfig(BaseModel):
    enabled: bool = True
    host: str = '0.0.0.0'
    port: int = 8080

class MediaConfig(BaseModel):
    reel_enabled: bool = True
    reel_max_mb: int = 100
    song_enabled: bool = True
    song_max_mb: int = 10
    song_tmp_root: str = './tmp/songs'
    song_playlist_channel_prefix: str = 'playlist-'
    song_lyrics_enabled: bool = True

class MarkovConfig(BaseModel):
    enabled: bool = True
    model_dir: str = 'data/markov'
    max_models_per_guild: int = 20
    max_generate_count: int = 5
    min_training_messages: int = 50

class CaptchaConfig(BaseModel):
    enabled: bool = True
    category_name: str = 'captcha'
    count_min: int = 15
    count_max: int = 40
    phrase_min_words: int = 6
    phrase_max_words: int = 10

class SetupConfig(BaseModel):
    enabled: bool = True
    preset_dir: str = 'config/presets'

class CasinoConfig(BaseModel):
    enabled: bool = True
    min_bet: int = Field(default=10, ge=1)
    max_bet: int = Field(default=50000, ge=1)
    earn_attachment: int = Field(default=5, ge=0)
    earn_link: int = Field(default=3, ge=0)
    earn_message: int = Field(default=1, ge=0)
    jackpot_seed: int = Field(default=10000, ge=100)
    jackpot_contribution_pct: float = Field(default=0.01, ge=0.0, le=1.0)
    slots_free_spins_award: int = Field(default=10, ge=1, le=100)

class GuildOverride(BaseModel):
    source: SourceConfig | None = None
    discord: DiscordConfig | None = None
    moderation: ModerationConfig | None = None
    logging: LoggingConfig | None = None
    analytics: AnalyticsConfig | None = None
    permissions: PermissionsConfig | None = None
    media: MediaConfig | None = None
    markov: MarkovConfig | None = None
    captcha: CaptchaConfig | None = None
    casino: CasinoConfig | None = None

class NonobotConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    moderation: ModerationConfig = Field(default_factory=ModerationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    markov: MarkovConfig = Field(default_factory=MarkovConfig)
    captcha: CaptchaConfig = Field(default_factory=CaptchaConfig)
    setup: SetupConfig = Field(default_factory=SetupConfig)
    casino: CasinoConfig = Field(default_factory=CasinoConfig)
    guilds: dict[str, GuildOverride] = Field(default_factory=dict)
    _owner_id: int = 0
    _bot_token: str = ''

    @model_validator(mode='after')
    def resolve_secrets(self) -> 'NonobotConfig':
        token = os.environ.get('BOT_TOKEN', '')
        if not token:
            raise ValueError('BOT_TOKEN environment variable is required.')
        object.__setattr__(self, '_bot_token', token)
        owner_env = self.bot.owner_id_env
        raw_owner = os.environ.get(owner_env, '')
        if not raw_owner:
            raise ValueError(f'{owner_env} environment variable is required.')
        try:
            object.__setattr__(self, '_owner_id', int(raw_owner))
        except ValueError:
            raise ValueError(f'{owner_env} must be a valid integer Discord user ID.')
        return self

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def owner_id(self) -> int:
        return self._owner_id

    def get_guild_source(self, guild_id: int) -> SourceConfig:
        o = self.guilds.get(str(guild_id))
        return o.source if o and o.source else self.source

    def get_guild_discord(self, guild_id: int) -> DiscordConfig:
        o = self.guilds.get(str(guild_id))
        return o.discord if o and o.discord else self.discord

    def get_guild_moderation(self, guild_id: int) -> ModerationConfig:
        o = self.guilds.get(str(guild_id))
        return o.moderation if o and o.moderation else self.moderation

    def get_guild_logging(self, guild_id: int) -> LoggingConfig:
        o = self.guilds.get(str(guild_id))
        return o.logging if o and o.logging else self.logging

    def get_guild_analytics(self, guild_id: int) -> AnalyticsConfig:
        o = self.guilds.get(str(guild_id))
        return o.analytics if o and o.analytics else self.analytics

    def get_guild_permissions(self, guild_id: int) -> PermissionsConfig:
        o = self.guilds.get(str(guild_id))
        return o.permissions if o and o.permissions else self.permissions

    def get_guild_media(self, guild_id: int) -> MediaConfig:
        o = self.guilds.get(str(guild_id))
        return o.media if o and o.media else self.media

    def get_guild_markov(self, guild_id: int) -> MarkovConfig:
        o = self.guilds.get(str(guild_id))
        return o.markov if o and o.markov else self.markov

    def get_guild_captcha(self, guild_id: int) -> CaptchaConfig:
        o = self.guilds.get(str(guild_id))
        return o.captcha if o and o.captcha else self.captcha

    def get_guild_casino(self, guild_id: int) -> CasinoConfig:
        o = self.guilds.get(str(guild_id))
        return o.casino if o and o.casino else self.casino
_config_instance: NonobotConfig | None = None

def load_config(_b: str | Path='config/config.yml') -> NonobotConfig:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(_b).parent.parent / '.env', override=False)
    except ModuleNotFoundError:
        pass
    _h = Path(_b)
    if not _h.exists():
        return NonobotConfig()
    with _h.open('r', encoding='utf-8') as _c:
        _i: dict[str, Any] = yaml.safe_load(_c) or {}
    _f: dict[str, Any] = _i.pop('guilds', {}) or {}
    _g: dict[str, GuildOverride] = {str(_e): GuildOverride(**_d) for _e, _d in _f.items() if _d}
    _a = NonobotConfig(**_i)
    object.__setattr__(_a, 'guilds', _g)
    return _a

def get_config() -> NonobotConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance

def reload_config(_a: str | Path='config/config.yml') -> NonobotConfig:
    global _config_instance
    _config_instance = load_config(_a)
    return _config_instance