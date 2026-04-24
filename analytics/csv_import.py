from __future__ import annotations
import csv
import datetime
import io
import re
import time
from typing import TYPE_CHECKING, Optional
import discord
from discord import app_commands
from discord.ext import commands
import structlog
from core.vanish import ephemeral_for
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_MAX_FILE_BYTES = 50 * 1024 * 1024
_BATCH_SIZE = 500
_PROGRESS_EVERY = 250
_SCRAPE_FILENAME_RE = re.compile('^scrape_(?P<channel_id>\\d{17,19})_(?P<job>\\d+)_(?P<date>\\d{8})_(?P<time>\\d{6})(?:_part\\d+)?\\.csv$')

def _parse_scrape_filename(_d: str) -> tuple[int, str] | None:
    _e = _SCRAPE_FILENAME_RE.match(_d)
    if not _e:
        return None
    _a = int(_e.group('channel_id'))
    _b, _f = (_e.group('date'), _e.group('time'))
    _c = f'{_b[:4]}-{_b[4:6]}-{_b[6:]}T{_f[:2]}:{_f[2:4]}:{_f[4:]}'
    return (_a, _c)

def _get(_d: dict, *_c: str) -> str | None:
    _e = {_a.strip().lower(): _f for _a, _f in _d.items()}
    for _b in _c:
        _g = _e.get(_b.lower())
        if _g not in (None, ''):
            return str(_g).strip()
    return None

def _ts_from_snowflake(_b: int) -> str:
    _c = (_b >> 22) + 1420070400000
    _a = datetime.datetime.utcfromtimestamp(_c / 1000)
    return _a.strftime('%Y-%m-%dT%H:%M:%S')

def _normalise_timestamp(_c: str | None, _b: int) -> str:
    if _c:
        try:
            _a = datetime.datetime.fromisoformat(_c)
            if _a.tzinfo is not None:
                _a = _a.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            return _a.strftime('%Y-%m-%dT%H:%M:%S')
        except (ValueError, OverflowError):
            pass
    return _ts_from_snowflake(_b)

def _coerce_int(_b: str | None, _a: int=0) -> int:
    if _b is None:
        return _a
    try:
        return int(_b)
    except (ValueError, TypeError):
        return _a

def _coerce_bool(_a: str | None) -> int:
    return 1 if _a in ('1', 'true', 'True', 'yes', 'YES') else 0

class CSVImportCog(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    @app_commands.command(name='importcsv', description='Import a historical message CSV export into the database.')
    @app_commands.describe(attachment='CSV file produced by /scrape (channel ID is auto-parsed from filename)', channel='Override: channel this CSV was exported from (only needed if filename parsing fails)', guild_id='Override: guild ID (owner only — only needed if bot cannot resolve from channel)', dry_run='Preview counts without writing to DB')
    async def importcsv(self, interaction: discord.Interaction, attachment: discord.Attachment, channel: Optional[discord.TextChannel]=None, guild_id: Optional[str]=None, dry_run: bool=False) -> None:
        bot: KnowledgeBot = interaction.client
        is_owner = interaction.user.id == bot.config.owner_id
        allowed = is_owner or await bot.resolver.can_use(interaction.user, 'lookup', interaction.guild_id or 0)
        if not allowed:
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        if not attachment.filename.lower().endswith('.csv'):
            await interaction.response.send_message('❌ Attachment must be a `.csv` file.', ephemeral=True)
            return
        if attachment.size > _MAX_FILE_BYTES:
            await interaction.response.send_message(f'❌ File exceeds the 50 MB limit ({attachment.size / 1024 / 1024:.1f} MB).', ephemeral=True)
            return
        parsed = _parse_scrape_filename(attachment.filename)
        resolved_channel_id: int
        resolved_guild_id: int
        if parsed is not None:
            resolved_channel_id, _export_dt = parsed
            if channel is not None:
                log.debug('csv_import.channel_override_ignored', filename=attachment.filename, override_channel=channel.id)
            ch_obj = bot.get_channel(resolved_channel_id)
            if ch_obj is not None and hasattr(ch_obj, 'guild'):
                resolved_guild_id = ch_obj.guild.id
            else:
                if guild_id and is_owner:
                    resolved_guild_id = int(guild_id)
                else:
                    resolved_guild_id = interaction.guild_id or 0
                log.warning('csv_import.channel_not_resolved', channel_id=resolved_channel_id, fallback_guild=resolved_guild_id)
        else:
            if channel is None:
                await interaction.response.send_message(embed=discord.Embed(title='❌ Cannot resolve channel', description='The filename does not match the expected scraper format:\n```\nscrape_{channel_id}_{job}_{YYYYMMDD}_{HHMMSS}.csv\n```\nEither rename the file to match this pattern, or supply the `channel` override argument.', colour=15548997), ephemeral=True)
                return
            resolved_channel_id = channel.id
            resolved_guild_id = channel.guild.id
            if guild_id and is_owner:
                resolved_guild_id = int(guild_id)
        log.info('csv_import.resolved_ids', filename=attachment.filename, channel_id=resolved_channel_id, guild_id=resolved_guild_id, source='filename' if parsed is not None else 'override')
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        progress_embed = discord.Embed(title='⏳ Parsing CSV…', description=f'File: `{attachment.filename}`', colour=16705372)
        await interaction.followup.send(embed=progress_embed, ephemeral=eph)
        try:
            raw = await attachment.read()
            text = raw.decode('utf-8-sig')
        except Exception as exc:
            await interaction.edit_original_response(embed=discord.Embed(title='❌ Read failed', description=str(exc), colour=15548997))
            return
        reader = csv.DictReader(io.StringIO(text))
        reader.fieldnames = [f.strip() for f in reader.fieldnames] if reader.fieldnames else reader.fieldnames
        if not reader.fieldnames or 'message_id' not in [f.lower() for f in reader.fieldnames or []]:
            await interaction.edit_original_response(embed=discord.Embed(title='❌ Invalid CSV', description='Required column `message_id` not found. Verify the file was produced by `/scrape`.', colour=15548997))
            return
        all_rows = list(reader)
        total_rows = len(all_rows)
        db = bot.db
        existing_raw = await db.fetch_all('SELECT message_id FROM messages WHERE guild_id = ? AND channel_id = ?', (resolved_guild_id, resolved_channel_id))
        existing_ids: set[int] = {int(r['message_id']) for r in existing_raw}
        inserted = 0
        already_seen = 0
        errors = 0
        processed = 0
        start_time = time.monotonic()
        batch_pending = 0
        try:
            await db.execute('BEGIN')
        except Exception:
            pass
        for row in all_rows:
            processed += 1
            if batch_pending >= _BATCH_SIZE:
                try:
                    await db.execute('COMMIT')
                    await db.execute('BEGIN')
                except Exception:
                    pass
                batch_pending = 0
            if processed % _PROGRESS_EVERY == 0:
                elapsed = time.monotonic() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = int((total_rows - processed) / rate) if rate > 0 else 0
                prog_embed = discord.Embed(title='⏳ Importing…', colour=16705372, description=f'Processed: {processed:,} / {total_rows:,}\nInserted: {inserted:,} | Skipped: {already_seen:,} | Errors: {errors:,}\nETA: ~{remaining}s')
                try:
                    await interaction.edit_original_response(embed=prog_embed)
                except Exception:
                    pass
            mid_raw = _get(row, 'message_id', 'id')
            if not mid_raw:
                errors += 1
                continue
            try:
                mid = int(mid_raw)
                if mid <= 0:
                    raise ValueError('non-positive')
            except (ValueError, TypeError):
                errors += 1
                log.warning('csv_import.invalid_message_id', raw=mid_raw)
                continue
            if mid in existing_ids:
                already_seen += 1
                continue
            if dry_run:
                inserted += 1
                existing_ids.add(mid)
                continue
            ts_raw = _get(row, 'timestamp', 'created_at')
            created_at = _normalise_timestamp(ts_raw, mid)
            author_id = _coerce_int(_get(row, 'author_id', 'user_id'))
            author_name = _get(row, 'author_name')
            author_display_name = _get(row, 'author_display_name')
            content = _get(row, 'content', 'message') or ''
            is_bot = _coerce_bool(_get(row, 'is_bot'))
            reply_to_raw = _get(row, 'reply_to_id')
            reply_to_id: int | None = _coerce_int(reply_to_raw) if reply_to_raw else None
            attachment_urls_raw = _get(row, 'attachment_urls') or ''
            attachment_filenames = _get(row, 'attachment_filenames')
            attachment_sizes_bytes = _get(row, 'attachment_sizes_bytes')
            has_attachment = 1 if attachment_urls_raw else 0
            embed_count = _coerce_int(_get(row, 'embed_count'))
            reaction_summary = _get(row, 'reaction_summary')
            pinned = _coerce_bool(_get(row, 'pinned'))
            message_type = _get(row, 'message_type') or 'default'
            try:
                await db.execute("\n                    INSERT OR IGNORE INTO messages (\n                        message_id, guild_id, channel_id, author_id,\n                        author_name, author_display_name,\n                        content, is_bot, reply_to_id,\n                        has_attachment, attachment_urls, attachment_filenames, attachment_sizes_bytes,\n                        embed_count, reaction_summary,\n                        pinned, message_type,\n                        created_at, import_source\n                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv')\n                    ", (mid, resolved_guild_id, resolved_channel_id, author_id, author_name, author_display_name, content, is_bot, reply_to_id, has_attachment, attachment_urls_raw or None, attachment_filenames, attachment_sizes_bytes, embed_count, reaction_summary, pinned, message_type, created_at))
                inserted += 1
                existing_ids.add(mid)
                batch_pending += 1
            except Exception as exc:
                errors += 1
                log.warning('csv_import.row_error', message_id=mid, error=str(exc))
        try:
            await db.execute('COMMIT')
        except Exception:
            pass
        elapsed_total = time.monotonic() - start_time
        snapshots_backfilled = 0
        if not dry_run and inserted > 0:
            snapshots_backfilled = await self._backfill_snapshots(resolved_guild_id, resolved_channel_id)
        ch_display = bot.get_channel(resolved_channel_id)
        ch_name = f'#{ch_display.name}' if ch_display else str(resolved_channel_id)
        mode_label = 'DRY RUN' if dry_run else 'LIVE'
        colour = 16705372 if dry_run else 5763719
        result_embed = discord.Embed(title=f"{('🔍 Dry run' if dry_run else '✅ Import complete')} — {ch_name}", colour=colour)
        result_embed.add_field(name='Total rows in CSV', value=f'{total_rows:,}', inline=True)
        result_embed.add_field(name='Inserted (new)' if not dry_run else 'Would insert', value=f'{inserted:,}', inline=True)
        result_embed.add_field(name='Already in DB (skipped)', value=f'{already_seen:,}', inline=True)
        result_embed.add_field(name='Parse errors', value=f'{errors:,}', inline=True)
        result_embed.add_field(name='Snapshot hours backfilled', value=f'{snapshots_backfilled:,}', inline=True)
        result_embed.add_field(name='Mode', value=mode_label, inline=True)
        result_embed.add_field(name='Duration', value=f'{elapsed_total:.1f}s', inline=True)
        result_embed.set_footer(text=f'File: {attachment.filename}')
        log.info('csv_import.complete', filename=attachment.filename, guild=resolved_guild_id, channel=resolved_channel_id, total=total_rows, inserted=inserted, already_seen=already_seen, errors=errors, snapshots_backfilled=snapshots_backfilled, dry_run=dry_run, duration_s=round(elapsed_total, 2))
        await interaction.edit_original_response(embed=result_embed)

    async def _backfill_snapshots(self, guild_id: int, channel_id: int) -> int:
        db = self.bot.db
        rows = await db.fetch_all("\n            SELECT strftime('%Y-%m-%dT%H', created_at) AS hour,\n                   COUNT(*)                              AS msg_count,\n                   COUNT(DISTINCT author_id)             AS active_users\n            FROM messages\n            WHERE guild_id = ? AND is_bot = 0\n            GROUP BY hour\n            ", (guild_id,))
        touched = 0
        for r in rows:
            hour = r['hour']
            if not hour:
                continue
            try:
                await db.execute('\n                    INSERT INTO analytics_snapshots (guild_id, snapshot_hour, message_count, active_users)\n                    VALUES (?, ?, ?, ?)\n                    ON CONFLICT(guild_id, snapshot_hour) DO UPDATE SET\n                        message_count = MAX(message_count, excluded.message_count),\n                        active_users  = MAX(active_users,  excluded.active_users)\n                    ', (guild_id, hour, r['msg_count'], r['active_users']))
                touched += 1
            except Exception as exc:
                log.warning('csv_import.snapshot_upsert_error', hour=hour, error=str(exc))
        log.info('csv_import.snapshots_backfilled', guild=guild_id, hours=touched)
        return touched

    @app_commands.command(name='importstatus', description='Show imported vs live message counts for a channel.')
    @app_commands.describe(channel='Channel to inspect')
    async def importstatus(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        bot: KnowledgeBot = interaction.client
        is_owner = interaction.user.id == bot.config.owner_id
        allowed = is_owner or await bot.resolver.can_use(interaction.user, 'lookup', interaction.guild_id or 0)
        if not allowed:
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        db = bot.db
        gid = interaction.guild_id or 0
        source_rows = await db.fetch_all("\n            SELECT\n                COALESCE(import_source, 'live') AS source,\n                COUNT(*) AS cnt\n            FROM messages\n            WHERE guild_id = ? AND channel_id = ?\n            GROUP BY source\n            ", (gid, channel.id))
        date_row = await db.fetch_one('\n            SELECT MIN(created_at) AS earliest, MAX(created_at) AS latest\n            FROM messages\n            WHERE guild_id = ? AND channel_id = ?\n            ', (gid, channel.id))
        counts: dict[str, int] = {r['source']: int(r['cnt']) for r in source_rows}
        total = sum(counts.values())
        imported = counts.get('csv', 0)
        live = counts.get('live', 0)
        earliest = (date_row['earliest'] or '—')[:16].replace('T', ' ') if date_row else '—'
        latest = (date_row['latest'] or '—')[:16].replace('T', ' ') if date_row else '—'
        embed = discord.Embed(title=f'📊 Import status — #{channel.name}', colour=5793266)
        embed.add_field(name='Imported (CSV)', value=f'{imported:,}', inline=True)
        embed.add_field(name='Live (real-time)', value=f'{live:,}', inline=True)
        embed.add_field(name='Total', value=f'{total:,}', inline=True)
        embed.add_field(name='Earliest message', value=earliest + ' UTC', inline=True)
        embed.add_field(name='Latest message', value=latest + ' UTC', inline=True)
        await interaction.followup.send(embed=embed, ephemeral=eph)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(CSVImportCog(_a))