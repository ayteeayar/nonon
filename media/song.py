from __future__ import annotations
import asyncio
import importlib.util
import json
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
import discord
from discord import app_commands
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_COOKIES_PATH = Path('./cookies.txt')

def _human_size(_a: int) -> str:
    for _b in ('B', 'KB', 'MB', 'GB'):
        if _a < 1024:
            return f'{_a:.1f} {_b}'
        _a //= 1024
    return f'{_a:.1f} TB'

def _source_colour(_b: str) -> int:
    try:
        _a = urlparse(_b).netloc.lower()
        _a = re.sub('^www\\.', '', _a)
        if 'spotify' in _a:
            return 1947988
        if 'youtube' in _a or 'youtu.be' in _a:
            return 16711680
    except Exception:
        pass
    return 5793266

def _source_domain(_b: str) -> str:
    try:
        _a = urlparse(_b).netloc.lower()
        return re.sub('^www\\.', '', _a)
    except Exception:
        return 'unknown'

def _format_duration(_b: int | float | None) -> str:
    if not _b:
        return '0:00'
    _c = int(_b)
    _a, _c = divmod(_c, 60)
    return f'{_a}:{_c:02d}'

def _safe_filename(_a: str) -> str:
    _a = re.sub('[\\\\/*?:"<>|]', '', _a)
    return _a.strip()[:200] or 'track'

async def _run_subprocess(*args: str, _d: float=120) -> tuple[int, str, str]:
    _a = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _c, _b = await asyncio.wait_for(_a.communicate(), timeout=_d)
    except asyncio.TimeoutError:
        _a.kill()
        return (-1, '', 'timeout exceeded.')
    return (_a.returncode, _c.decode(errors='replace'), _b.decode(errors='replace'))

def _parse_meta(_b: str) -> dict | None:
    for _a in reversed(_b.splitlines()):
        _a = _a.strip()
        if _a.startswith('{'):
            try:
                return json.loads(_a)
            except json.JSONDecodeError:
                continue
    return None

def _parse_meta_list(_c: str) -> list[dict]:
    _a = []
    for _b in _c.splitlines():
        _b = _b.strip()
        if _b.startswith('{'):
            try:
                _a.append(json.loads(_b))
            except json.JSONDecodeError:
                continue
    return _a

async def _crop_thumbnail(_f: Path, _d: Path) -> bool:
    if importlib.util.find_spec('PIL') is None:
        return False
    if not _f.exists():
        return False
    try:
        from PIL import Image
        _b = Image.open(_f)
        _h, _a = _b.size
        _e = min(_h, _a)
        _c = (_h - _e) // 2
        _g = (_a - _e) // 2
        _b = _b.crop((_c, _g, _c + _e, _g + _e))
        _b = _b.resize((400, 400), Image.LANCZOS)
        _b.save(_d, 'JPEG', quality=90)
        return True
    except Exception as exc:
        log.debug('song.thumbnail.crop_failed', error=str(exc))
        return False

async def _inject_lyrics(_c: Path, _f: str | None, _a: str | None) -> None:
    if not _f:
        return
    try:
        import lyricsgenius
        import mutagen.id3 as mid3
        _b = lyricsgenius.Genius(verbose=False, skip_non_songs=True, remove_section_headers=True)
        _d = await asyncio.get_event_loop().run_in_executor(None, lambda: _b.search_song(_f, _a or ''))
        if not _d or not _d.lyrics:
            return
        _e = mid3.ID3(str(_c))
        _e.delall('USLT')
        _e.add(mid3.USLT(encoding=3, lang='eng', desc='', text=_d.lyrics))
        _e.save(str(_c), v2_version=3)
        log.debug('song.lyrics.injected', title=_f)
    except Exception as exc:
        log.debug('song.lyrics.failed', title=_f, error=str(exc))

async def _download_track(_x: str, _j: Path, _l: int) -> tuple[Path | None, dict | None, str | None]:
    _j.mkdir(parents=True, exist_ok=True)
    _k = str(_j / '%(id)s.%(ext)s')
    _d = ['yt-dlp', '--format', 'bestaudio/best', '--embed-metadata', '--write-thumbnail', '--convert-thumbnails', 'jpg', '--no-playlist', '--print-json', '--no-warnings', '-o', _k, _x]
    if _COOKIES_PATH.exists():
        _d.extend(['--cookies', str(_COOKIES_PATH)])
    log.info('song.download.start', url=_x, quality=_l)
    _v = time.monotonic()
    _n, _u, _s = await _run_subprocess(*_d, timeout=180)
    _e = round(time.monotonic() - _v, 1)
    if _n == -1:
        return (None, None, '⏱ download timed out.')
    if _n != 0:
        _q = _s[:300]
        if 'no audio' in _s.lower() or 'video only' in _s.lower():
            return (None, None, '❌ no audio stream found for this url — video-only content is not supported.')
        return (None, None, f'❌ download failed: {_q}')
    _h = _parse_meta(_u)
    if not shutil.which('ffmpeg'):
        return (None, _h, '❌ ffmpeg is not installed or not on path.')
    _c = {'.webm', '.opus', '.m4a', '.ogg', '.mp3', '.aac', '.flac', '.wav'}
    _b = [_f for _f in _j.iterdir() if _f.suffix.lower() in _c]
    if not _b:
        return (None, _h, '❌ download failed: yt-dlp produced no audio file.')
    _r = _b[0]
    _i = _r.with_suffix('.mp3')
    if _r.suffix.lower() == '.mp3':
        _i = _r.parent / f'converted_{_r.stem}.mp3'
    _o, _a, _t = await _run_subprocess('ffmpeg', '-y', '-i', str(_r), '-vn', '-b:a', f'{_l}k', '-id3v2_version', '3', '-write_id3v1', '1', str(_i), timeout=120)
    if _o != 0:
        return (None, _h, f'❌ ffmpeg conversion failed: {_t[:200]}')
    if _r != _i:
        _r.unlink(missing_ok=True)
    _w = list(_j.glob('*.jpg'))
    if _w and shutil.which('ffmpeg'):
        _m = _w[0]
        _g = _i.parent / f'final_{_i.name}'
        _p, _a, _a = await _run_subprocess('ffmpeg', '-y', '-i', str(_i), '-i', str(_m), '-map', '0', '-map', '1', '-c', 'copy', '-id3v2_version', '3', '-metadata:s:v', 'title=Album cover', '-metadata:s:v', 'comment=Cover (front)', str(_g), timeout=60)
        if _p == 0:
            _i.unlink(missing_ok=True)
            _g.rename(_i)
    log.info('song.download.complete', url=_x, file=str(_i), elapsed=_e)
    return (_i, _h, None)

async def _build_embed(_g: Path, _f: dict | None, _j: Path | None, _m: str, _h: int, _n: discord.User | discord.Member) -> discord.Embed:
    _k = (_f or {}).get('title') or _g.stem
    _l = (_f or {}).get('uploader') or (_f or {}).get('channel') or (_f or {}).get('artist') or 'unknown'
    _c = _format_duration((_f or {}).get('duration'))
    _b = _source_domain(_m)
    _a = _source_colour(_m)
    _e = _g.stat().st_size
    _i = _human_size(_e)
    _d = discord.Embed(title=_k, colour=_a)
    _d.description = f'{_l}  •  {_c}  •  {_b}'
    _d.set_footer(text=f'{_i} • mp3 {_h}kbps • requested by {_n.display_name}')
    if _j and _j.exists():
        _d.set_thumbnail(url='attachment://thumb_cropped.jpg')
    return _d

class SongCog(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    async def _check_media(self, interaction: discord.Interaction) -> bool:
        allowed = await self.bot.resolver.can_use(interaction.user, 'song', interaction.guild_id or 0)
        if not allowed:
            await interaction.response.send_message('❌ permission denied.', ephemeral=True)
        return allowed

    @app_commands.command(name='song', description='download and send audio from youtube, youtube music, or spotify.')
    @app_commands.describe(url='url to a track, album, or playlist supported by yt-dlp', quality='audio quality in kbps (default 320)')
    async def song(self, interaction: discord.Interaction, url: str, quality: app_commands.Range[int, 64, 320]=320) -> None:
        if not await self._check_media(interaction):
            return
        cfg_media = self.bot.config.get_guild_media(interaction.guild_id or 0)
        if not cfg_media.song_enabled:
            await interaction.response.send_message('❌ `/song` is disabled for this server.', ephemeral=True)
            return
        if not shutil.which('yt-dlp'):
            await interaction.response.send_message('❌ yt-dlp is not installed or not on path.', ephemeral=True)
            return
        if not shutil.which('ffmpeg'):
            await interaction.response.send_message('❌ ffmpeg is not installed or not on path.', ephemeral=True)
            return
        await interaction.response.defer()
        is_owner = interaction.user.id == self.bot.config.owner_id
        guild = interaction.guild
        discord_limit_bytes = guild.filesize_limit if guild else 8 * 1024 * 1024
        user_cap_bytes = discord_limit_bytes if is_owner else cfg_media.song_max_mb * 1024 * 1024
        tmp_root = Path(cfg_media.song_tmp_root)
        tmp_dir = tmp_root / str(interaction.guild_id or 'dm') / str(int(time.time()))
        try:
            entries = await self._detect_playlist(url)
            if entries and len(entries) > 1:
                await self._handle_playlist(interaction=interaction, url=url, entries=entries, quality=quality, tmp_root=tmp_root, cfg_media=cfg_media, user_cap_bytes=user_cap_bytes, is_owner=is_owner, discord_limit_bytes=discord_limit_bytes)
                return
            mp3_path, meta, err = await _download_track(url, tmp_dir, quality)
            if err or mp3_path is None:
                await interaction.followup.send(err or '❌ download failed.')
                return
            file_size = mp3_path.stat().st_size
            if file_size > user_cap_bytes and (not is_owner):
                await interaction.followup.send(f'❌ file is {_human_size(file_size)} — limit is {cfg_media.song_max_mb}mb for this server.')
                return
            if cfg_media.song_lyrics_enabled and importlib.util.find_spec('lyricsgenius') and importlib.util.find_spec('mutagen'):
                title = (meta or {}).get('title')
                artist = (meta or {}).get('uploader') or (meta or {}).get('artist')
                await _inject_lyrics(mp3_path, title, artist)
            thumb_candidates = list(tmp_dir.glob('*.jpg'))
            thumb_cropped: Path | None = None
            if thumb_candidates:
                raw_thumb = thumb_candidates[0]
                cropped_path = tmp_dir / 'thumb_cropped.jpg'
                ok = await _crop_thumbnail(raw_thumb, cropped_path)
                if ok:
                    thumb_cropped = cropped_path
            embed = await _build_embed(mp3_path, meta, thumb_cropped, url, quality, interaction.user)
            safe_name = _safe_filename((meta or {}).get('title') or mp3_path.stem) + '.mp3'
            files = [discord.File(str(mp3_path), filename=safe_name)]
            if thumb_cropped:
                files.append(discord.File(str(thumb_cropped), filename='thumb_cropped.jpg'))
            await interaction.followup.send(embed=embed, files=files)
            log.info('song.posted', guild=interaction.guild_id, url=url, size=file_size, user=interaction.user.id, quality=quality, duration_seconds=(meta or {}).get('duration'))
        except asyncio.TimeoutError:
            log.error('song.error', url=url, error='timeout')
            await interaction.followup.send('⏱ download timed out.')
        except Exception as exc:
            log.error('song.error', url=url, error=str(exc))
            await interaction.followup.send(f'❌ unexpected error: {exc}')
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _detect_playlist(self, url: str) -> list[dict]:
        cmd = ['yt-dlp', '--flat-playlist', '--print-json', '--no-warnings', url]
        if _COOKIES_PATH.exists():
            cmd.extend(['--cookies', str(_COOKIES_PATH)])
        rc, stdout, _ = await _run_subprocess(*cmd, timeout=60)
        if rc != 0:
            return []
        return _parse_meta_list(stdout)

    async def _handle_playlist(self, interaction: discord.Interaction, url: str, entries: list[dict], quality: int, tmp_root: Path, cfg_media, user_cap_bytes: int, is_owner: bool, discord_limit_bytes: int) -> None:
        n = len(entries)
        playlist_title = entries[0].get('playlist_title') or entries[0].get('playlist') or 'playlist'
        await interaction.followup.send(f'found {n} tracks — creating channel and uploading...', ephemeral=True)
        guild = interaction.guild
        channel_name = (cfg_media.song_playlist_channel_prefix + re.sub('[^a-z0-9\\-]', '-', playlist_title.lower().replace(' ', '-')))[:100]
        playlist_channel: discord.TextChannel | None = None
        if guild:
            try:
                category = interaction.channel.category if hasattr(interaction.channel, 'category') else None
                playlist_channel = await guild.create_text_channel(name=channel_name, category=category, topic=url)
            except discord.HTTPException as exc:
                log.warning('song.playlist.channel_create_failed', error=str(exc))
                playlist_channel = None
        target_channel = playlist_channel or interaction.channel
        intro_embed = discord.Embed(title=f'playlist: {playlist_title}', description=f'{n} tracks • requested by {interaction.user.mention}', colour=_source_colour(url), url=url)
        await target_channel.send(embed=intro_embed)
        parent_tmp = tmp_root / str(interaction.guild_id or 'dm') / f'playlist_{int(time.time())}'
        try:
            for idx, entry in enumerate(entries, start=1):
                track_url = entry.get('url') or entry.get('webpage_url') or url
                track_tmp = parent_tmp / f'track_{idx}'
                try:
                    mp3_path, meta, err = await _download_track(track_url, track_tmp, quality)
                    if err or mp3_path is None:
                        log.warning('song.playlist_track.failed', track_index=idx, url=track_url, error=err)
                        await target_channel.send(f'⚠️ track {idx} failed: {err}')
                        shutil.rmtree(track_tmp, ignore_errors=True)
                        await asyncio.sleep(0.5)
                        continue
                    file_size = mp3_path.stat().st_size
                    if file_size > user_cap_bytes and (not is_owner):
                        log.warning('song.playlist_track.skipped_size', track_index=idx, title=(meta or {}).get('title'), size=file_size)
                        await target_channel.send(f'⚠️ track {idx} skipped — file is {_human_size(file_size)}, limit is {cfg_media.song_max_mb}mb.')
                        shutil.rmtree(track_tmp, ignore_errors=True)
                        await asyncio.sleep(0.5)
                        continue
                    if cfg_media.song_lyrics_enabled and importlib.util.find_spec('lyricsgenius') and importlib.util.find_spec('mutagen'):
                        title = (meta or {}).get('title')
                        artist = (meta or {}).get('uploader') or (meta or {}).get('artist')
                        await _inject_lyrics(mp3_path, title, artist)
                    thumb_candidates = list(track_tmp.glob('*.jpg'))
                    thumb_cropped: Path | None = None
                    if thumb_candidates:
                        raw_thumb = thumb_candidates[0]
                        cropped_path = track_tmp / 'thumb_cropped.jpg'
                        ok = await _crop_thumbnail(raw_thumb, cropped_path)
                        if ok:
                            thumb_cropped = cropped_path
                    embed = await _build_embed(mp3_path, meta, thumb_cropped, track_url, quality, interaction.user)
                    embed.set_footer(text=f'track {idx}/{n} • {embed.footer.text}')
                    safe_name = _safe_filename((meta or {}).get('title') or mp3_path.stem) + '.mp3'
                    files = [discord.File(str(mp3_path), filename=safe_name)]
                    if thumb_cropped:
                        files.append(discord.File(str(thumb_cropped), filename='thumb_cropped.jpg'))
                    await target_channel.send(embed=embed, files=files)
                    log.info('song.playlist_track', track_index=idx, title=(meta or {}).get('title'), size=file_size)
                except Exception as exc:
                    log.error('song.playlist_track.error', track_index=idx, error=str(exc))
                    await target_channel.send(f'⚠️ track {idx} error: {exc}')
                finally:
                    shutil.rmtree(track_tmp, ignore_errors=True)
                await asyncio.sleep(0.5)
            done_msg = f'✅ done — {n} tracks processed.'
            await target_channel.send(done_msg)
            if playlist_channel:
                await interaction.followup.send(f'✅ playlist upload complete — {playlist_channel.mention}', ephemeral=True)
        finally:
            shutil.rmtree(parent_tmp, ignore_errors=True)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(SongCog(_a))