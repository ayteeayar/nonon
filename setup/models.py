from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class PresetOverwrite(BaseModel):
    role: str
    view_channel: bool | None = None
    send_messages: bool | None = None
    read_message_history: bool | None = None
    connect: bool | None = None
    speak: bool | None = None
    manage_messages: bool | None = None
    mention_everyone: bool | None = None
    embed_links: bool | None = None
    attach_files: bool | None = None
    add_reactions: bool | None = None
    use_external_emojis: bool | None = None
    create_instant_invite: bool | None = None
    mute_members: bool | None = None
    deafen_members: bool | None = None
    move_members: bool | None = None
    stream: bool | None = None
    use_voice_activation: bool | None = None
    priority_speaker: bool | None = None
    manage_channels: bool | None = None
    manage_roles: bool | None = None
    manage_webhooks: bool | None = None
    manage_emojis: bool | None = None
    kick_members: bool | None = None
    ban_members: bool | None = None
    administrator: bool | None = None
    view_audit_log: bool | None = None
    change_nickname: bool | None = None
    manage_nicknames: bool | None = None
    send_tts_messages: bool | None = None
    use_application_commands: bool | None = None
    request_to_speak: bool | None = None

    def to_discord_overwrite(self) -> dict[str, bool | None]:
        return {k: v for k, v in self.model_dump(exclude={'role'}).items() if v is not None}

class PresetRole(BaseModel):
    name: str
    color: int = 0
    hoist: bool = False
    mentionable: bool = False
    permissions: int = 0

class PresetTextChannel(BaseModel):
    name: str
    topic: str = ''
    nsfw: bool = False
    slowmode_delay: int = Field(default=0, ge=0, le=21600)
    position: int = 0
    overwrites: list[PresetOverwrite] = Field(default_factory=list)

class PresetVoiceChannel(BaseModel):
    name: str
    bitrate: int = Field(default=64000, ge=8000, le=384000)
    user_limit: int = Field(default=0, ge=0, le=99)
    video_quality_mode: int = Field(default=1, ge=1, le=2)
    position: int = 0
    overwrites: list[PresetOverwrite] = Field(default_factory=list)

class PresetCategory(BaseModel):
    name: str
    position: int = 0
    overwrites: list[PresetOverwrite] = Field(default_factory=list)
    text_channels: list[PresetTextChannel] = Field(default_factory=list)
    voice_channels: list[PresetVoiceChannel] = Field(default_factory=list)

class Preset(BaseModel):
    name: str
    description: str = ''
    roles: list[PresetRole] = Field(default_factory=list)
    categories: list[PresetCategory] = Field(default_factory=list)