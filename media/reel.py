from __future__ import annotations
import asyncio
import json
import os
import random
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse
import discord
from discord import app_commands
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_TMP_ROOT = Path('./tmp/reels')

async def _check_media(_c: discord.Interaction) -> bool:
    _b: KnowledgeBot = _c.client
    _a = await _b.resolver.can_use(_c.user, 'reel', _c.guild_id or 0)
    if not _a:
        await _c.response.send_message('❌ Permission denied.', ephemeral=True)
    return _a

def _source_site(_b: str) -> str:
    try:
        _a = urlparse(_b).netloc.lower()
        _a = re.sub('^www\\.', '', _a)
        return _a
    except Exception:
        return 'unknown'

def _human_size(_a: int) -> str:
    for _b in ('B', 'KB', 'MB', 'GB'):
        if _a < 1024:
            return f'{_a:.1f} {_b}'
        _a //= 1024
    return f'{_a:.1f} TB'

async def _run_subprocess(*args: str, _d: float=120) -> tuple[int, str, str]:
    _a = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _c, _b = await asyncio.wait_for(_a.communicate(), timeout=_d)
    except asyncio.TimeoutError:
        _a.kill()
        return (-1, '', 'Timeout exceeded.')
    return (_a.returncode, _c.decode(errors='replace'), _b.decode(errors='replace'))

async def _ytdlp_download(_o: str, _i: Path, _g: int) -> tuple[Path | None, dict | None, str | None]:
    _i.mkdir(parents=True, exist_ok=True)
    _j = str(_i / '%(id)s.%(ext)s')
    _e = f'bestvideo[ext=mp4][filesize<{_g}M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<{_g}M]/best[filesize<{_g}M]/best'
    _b = ['yt-dlp', '--format', _e, '--merge-output-format', 'mp4', f'--max-filesize', f'{_g}M', '--print-json', '--no-playlist', '--no-warnings', '-o', _j, _o]
    log.info('ytdlp.download.start', url=_o, max_mb=_g)
    _n = time.monotonic()
    _k, _m, _l = await _run_subprocess(*_b, timeout=180)
    _c = round(time.monotonic() - _n, 1)
    if _k != 0:
        log.error('ytdlp.download.failed', url=_o, rc=_k, stderr=_l[:500])
        return (None, None, f'yt-dlp exited {_k}: {_l[:300]}')
    _h: dict | None = None
    for _f in reversed(_m.splitlines()):
        _f = _f.strip()
        if _f.startswith('{'):
            try:
                _h = json.loads(_f)
                break
            except json.JSONDecodeError:
                continue
    _a = list(_i.iterdir())
    if not _a:
        return (None, _h, 'yt-dlp ran but no output file was created.')
    _p = _a[0]
    _d = _p.stat().st_size
    log.info('ytdlp.download.complete', url=_o, file=str(_p), size_mb=round(_d / 1000000.0, 2), elapsed=_c)
    return (_p, _h, None)

def _compact_caption(_d: dict | None, _c: int, _k: discord.User | discord.Member, _j: str | None=None) -> str:
    _i = _human_size(_c)
    _b = (_d or {}).get('duration')
    if _b:
        _e, _h = divmod(int(_b), 60)
        _a = f'{_e}:{_h:02d}'
    else:
        _a = None
    _f = (_d or {}).get('uploader') or (_d or {}).get('channel') or (_d or {}).get('creator') or _k.display_name
    if _j is not None:
        _g = [f'🎞️ {_i}']
        _g.append(f'@ {_j}')
        _g.append(_f)
    else:
        _g = [f'📎 {_i}']
        if _a:
            _g.append(_a)
        _g.append(_f)
    return '  •  '.join(_g)

class ReelCog(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    @app_commands.command(name='reel', description='Download and repost a video from a URL.')
    @app_commands.describe(url='URL supported by yt-dlp (YouTube, TikTok, Twitter/X, Instagram, …)')
    async def reel(self, interaction: discord.Interaction, url: str) -> None:
        if not await _check_media(interaction):
            return
        cfg_media = self.bot.config.get_guild_media(interaction.guild_id or 0)
        if not cfg_media.reel_enabled:
            await interaction.response.send_message('❌ `/reel` is disabled for this server.', ephemeral=True)
            return
        await interaction.response.defer()
        guild = interaction.guild
        discord_limit_bytes = guild.filesize_limit if guild else 8 * 1024 * 1024
        max_mb = min(cfg_media.reel_max_mb, discord_limit_bytes // (1024 * 1024))
        tmp_dir = _TMP_ROOT / str(interaction.guild_id or 'dm') / str(int(time.time()))
        try:
            video_path, meta, err = await _ytdlp_download(url, tmp_dir, max_mb)
            if err or video_path is None:
                await interaction.followup.send(f'❌ Download failed: {err}')
                return
            file_size = video_path.stat().st_size
            if file_size > discord_limit_bytes:
                await interaction.followup.send(embed=discord.Embed(title='❌ File too large', description=f"Downloaded file is **{_human_size(file_size)}** but this guild's upload limit is **{_human_size(discord_limit_bytes)}**.", colour=15548997))
                return
            caption = _compact_caption(meta, file_size, interaction.user)
            discord_file = discord.File(str(video_path), filename=video_path.name)
            await interaction.followup.send(content=caption, file=discord_file)
            log.info('reel.posted', guild=interaction.guild_id, url=url, size=file_size, user=interaction.user.id)
        except Exception as exc:
            log.error('reel.error', url=url, error=str(exc))
            await interaction.followup.send(f'❌ Unexpected error: {exc}')
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    @app_commands.command(name='frame', description='Extract a frame from a video URL.')
    @app_commands.describe(url='URL supported by yt-dlp (or local file path for owner)', timestamp='Timestamp to extract (HH:MM:SS or seconds). Omit for random.')
    async def frame(self, interaction: discord.Interaction, url: str, timestamp: Optional[str]=None) -> None:
        if not await _check_media(interaction):
            return
        await interaction.response.defer()
        if not shutil.which('ffmpeg'):
            await interaction.followup.send('❌ `ffmpeg` is not installed or not on PATH. Install it with `apt install ffmpeg` (Linux) or `brew install ffmpeg` (macOS).')
            return
        cfg_media = self.bot.config.get_guild_media(interaction.guild_id or 0)
        max_mb = cfg_media.reel_max_mb
        is_local = not url.startswith(('http://', 'https://'))
        if is_local:
            if interaction.user.id != self.bot.config.owner_id:
                await interaction.followup.send('❌ Local file paths are owner-only.')
                return
            video_path = Path(url)
            if not video_path.exists():
                await interaction.followup.send(f'❌ File not found: `{url}`')
                return
            meta: dict | None = None
            tmp_dir: Path | None = None
        else:
            tmp_dir = _TMP_ROOT / str(interaction.guild_id or 'dm') / str(int(time.time()))
            video_path_result, meta, err = await _ytdlp_download(url, tmp_dir, max_mb)
            if err or video_path_result is None:
                await interaction.followup.send(f'❌ Download failed: {err}')
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return
            video_path = video_path_result
        frame_path = video_path.parent / 'frame.jpg'
        try:
            duration: float | None = (meta or {}).get('duration')
            is_live = duration is None or duration == 0
            if is_live:
                await interaction.followup.send('❌ Cannot extract frames from a live stream (no fixed duration).')
                return
            if timestamp:
                ts_str = timestamp
            else:
                ts_secs = random.uniform(duration * 0.05, duration * 0.95)
                mins, secs = divmod(int(ts_secs), 60)
                hours, mins = divmod(mins, 60)
                ts_str = f'{hours:02d}:{mins:02d}:{secs:02d}'
            log.info('frame.extract', url=url, timestamp=ts_str, video=str(video_path))
            rc, stdout, stderr = await _run_subprocess('ffmpeg', '-y', '-ss', ts_str, '-i', str(video_path), '-vframes', '1', '-q:v', '2', str(frame_path), timeout=60)
            if rc != 0 or not frame_path.exists():
                log.error('frame.ffmpeg_failed', rc=rc, stderr=stderr[:400])
                await interaction.followup.send(f'❌ ffmpeg failed (exit {rc}). The timestamp may be beyond the video length.\n```{stderr[:300]}```')
                return
            file_size = frame_path.stat().st_size
            caption = _compact_caption(meta, file_size, interaction.user, timestamp=ts_str)
            discord_file = discord.File(str(frame_path), filename='frame.jpg')
            await interaction.followup.send(content=caption, file=discord_file)
            log.info('frame.posted', guild=interaction.guild_id, url=url, timestamp=ts_str)
        except Exception as exc:
            log.error('frame.error', url=url, error=str(exc))
            await interaction.followup.send(f'❌ Unexpected error: {exc}')
        finally:
            try:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                elif frame_path.exists():
                    frame_path.unlink(missing_ok=True)
            except Exception:
                pass

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(ReelCog(_a))