from __future__ import annotations
import asyncio
import csv
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional
import discord
from discord import app_commands
from discord.ext import commands, tasks
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
BATCH_SIZE = 100
BATCH_DELAY = 1.5
BACKOFF_BASE = 2.0
BACKOFF_MAX = 30.0
PROGRESS_INTERVAL = 20
CSV_COLUMNS = ['message_id', 'timestamp', 'author_id', 'author_name', 'author_display_name', 'content', 'is_bot', 'reply_to_id', 'attachment_urls', 'attachment_filenames', 'attachment_sizes_bytes', 'embed_count', 'reaction_summary', 'pinned', 'message_type']

def _serialize_row(_f: discord.Message) -> dict:
    _e = _f.attachments
    _h = _f.reactions
    _d = '|'.join((_a.url for _a in _e))
    _b = '|'.join((_a.filename for _a in _e))
    _c = '|'.join((str(_a.size) for _a in _e))
    _j = ' '.join((f'{_g.emoji}:{_g.count}' for _g in _h))
    _i = None
    if _f.reference and _f.reference.message_id:
        _i = _f.reference.message_id
    return {'message_id': _f.id, 'timestamp': _f.created_at.isoformat(), 'author_id': _f.author.id, 'author_name': str(_f.author), 'author_display_name': _f.author.display_name, 'content': _f.content or '', 'is_bot': int(_f.author.bot), 'reply_to_id': _i or '', 'attachment_urls': _d, 'attachment_filenames': _b, 'attachment_sizes_bytes': _c, 'embed_count': len(_f.embeds), 'reaction_summary': _j, 'pinned': int(_f.pinned), 'message_type': str(_f.type.name)}

class _JobState:

    def __init__(self, job_id: int, channel: discord.TextChannel) -> None:
        self.job_id = job_id
        self.channel = channel
        self.total = 0
        self.unique_authors: set[int] = set()
        self.with_attachments = 0
        self.with_reactions = 0
        self.started_at = datetime.now(timezone.utc)
        self.status = 'running'
        self.output_path: str | None = None
        self.error: str | None = None
        self.progress_message: discord.Message | None = None

class Scraper(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._jobs: dict[int, _JobState] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def _is_owner(self, user: discord.User | discord.Member) -> bool:
        owner_id_env = self.bot.config.bot.owner_id_env
        raw = os.environ.get(owner_id_env, '')
        try:
            return user.id == int(raw)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _build_progress_embed(state: _JobState) -> discord.Embed:
        elapsed = (datetime.now(timezone.utc) - state.started_at).total_seconds()
        mins, secs = divmod(int(elapsed), 60)
        rate = state.total / elapsed if elapsed > 0 else 0
        STATUS_COLOUR = {'running': 5793266, 'complete': 51281, 'failed': 16729156, 'cancelled': 16746496}
        STATUS_ICON = {'running': '🔄', 'complete': '✅', 'failed': '💥', 'cancelled': '⛔'}
        colour = STATUS_COLOUR.get(state.status, 8421504)
        icon = STATUS_ICON.get(state.status, '❓')
        embed = discord.Embed(title=f'{icon} Scrape Job #{state.job_id}', colour=colour, timestamp=datetime.now(timezone.utc))
        embed.add_field(name='Channel', value=state.channel.mention, inline=True)
        embed.add_field(name='Messages', value=f'{state.total:,}', inline=True)
        embed.add_field(name='Elapsed', value=f'{mins}m {secs}s', inline=True)
        embed.add_field(name='Unique Users', value=f'{len(state.unique_authors):,}', inline=True)
        embed.add_field(name='With Attachments', value=f'{state.with_attachments:,}', inline=True)
        embed.add_field(name='With Reactions', value=f'{state.with_reactions:,}', inline=True)
        embed.add_field(name='Rate', value=f'{rate:.1f} msg/s', inline=True)
        if state.status == 'complete' and state.output_path:
            embed.add_field(name='Output', value=f'`{state.output_path}`', inline=False)
        if state.status == 'failed' and state.error:
            embed.add_field(name='Error', value=state.error[:300], inline=False)
        if state.status == 'running':
            embed.set_footer(text='Updates every 20 seconds · use /scrapecancel to stop')
        return embed

    @app_commands.command(name='scrape', description='[owner] Scrape full message history of a channel.')
    @app_commands.describe(channel='Channel to scrape (defaults to current)')
    async def scrape(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]=None) -> None:
        if not self._is_owner(interaction.user):
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message('❌ Invalid channel.', ephemeral=True)
            return
        job_id = await self.bot.db.execute_returning("INSERT INTO scrape_jobs (guild_id, channel_id, requested_by, status)\n               VALUES (?, ?, ?, 'pending')", (interaction.guild_id, target.id, interaction.user.id))
        state = _JobState(job_id, target)
        self._jobs[job_id] = state
        await interaction.response.defer(ephemeral=True)
        msg = await interaction.followup.send(embed=self._build_progress_embed(state), ephemeral=True, wait=True)
        state.progress_message = msg
        task = asyncio.create_task(self._run_scrape(job_id, target, interaction.guild_id or 0, state), name=f'scrape-{job_id}')
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    @app_commands.command(name='scrapestatus', description='[owner] Check status of a scrape job.')
    @app_commands.describe(job_id='Scrape job ID')
    async def scrapestatus(self, interaction: discord.Interaction, job_id: int) -> None:
        if not self._is_owner(interaction.user):
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        state = self._jobs.get(job_id)
        if state:
            await interaction.response.send_message(embed=self._build_progress_embed(state), ephemeral=True)
            return
        row = await self.bot.db.fetch_one('SELECT * FROM scrape_jobs WHERE id = ?', (job_id,))
        if not row:
            await interaction.response.send_message(f'❌ Job `#{job_id}` not found.', ephemeral=True)
            return
        STATUS_ICON = {'pending': '⏳', 'running': '🔄', 'complete': '✅', 'cancelled': '⛔', 'failed': '💥'}
        icon = STATUS_ICON.get(row['status'], '❓')
        embed = discord.Embed(title=f'{icon} Scrape Job #{job_id}', colour=5793266)
        embed.add_field(name='Status', value=row['status'])
        embed.add_field(name='Messages', value=f"{row['messages_scraped']:,}")
        embed.add_field(name='Channel', value=f"<#{row['channel_id']}>")
        if row['output_path']:
            embed.add_field(name='Output', value=f"`{row['output_path']}`", inline=False)
        if row['error_msg']:
            embed.add_field(name='Error', value=row['error_msg'][:300], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='scrapecancel', description='[owner] Cancel a running scrape job.')
    @app_commands.describe(job_id='Scrape job ID to cancel')
    async def scrapecancel(self, interaction: discord.Interaction, job_id: int) -> None:
        if not self._is_owner(interaction.user):
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        task = self._tasks.get(job_id)
        if task and (not task.done()):
            task.cancel()
            await interaction.response.send_message(f'⛔ Job `#{job_id}` cancelled.', ephemeral=True)
        else:
            await interaction.response.send_message(f'❌ Job `#{job_id}` is not running.', ephemeral=True)

    async def _run_scrape(self, job_id: int, channel: discord.TextChannel, guild_id: int, state: _JobState) -> None:
        output_dir = Path(self.bot.config.analytics.export_path) / str(guild_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        output_path = output_dir / f'scrape_{channel.id}_{job_id}_{ts}.csv'
        await self.bot.db.execute("UPDATE scrape_jobs SET status='running', started_at=datetime('now') WHERE id=?", (job_id,))
        backoff = BACKOFF_BASE
        last_progress_edit = asyncio.get_event_loop().time()
        try:
            with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                before: discord.Message | None = None
                while True:
                    try:
                        batch: list[discord.Message] = []
                        async for msg in channel.history(limit=BATCH_SIZE, before=before, oldest_first=False):
                            batch.append(msg)
                        backoff = BACKOFF_BASE
                    except discord.HTTPException as exc:
                        if exc.status == 429:
                            wait = min(backoff, BACKOFF_MAX)
                            log.warning('scrape.rate_limited', job=job_id, wait=wait)
                            await asyncio.sleep(wait)
                            backoff = min(backoff * 2, BACKOFF_MAX)
                            continue
                        raise
                    if not batch:
                        break
                    for msg in batch:
                        row = _serialize_row(msg)
                        writer.writerow(row)
                        state.total += 1
                        state.unique_authors.add(msg.author.id)
                        if msg.attachments:
                            state.with_attachments += 1
                        if msg.reactions:
                            state.with_reactions += 1
                    before = batch[-1]
                    await self.bot.db.execute('UPDATE scrape_jobs SET messages_scraped=? WHERE id=?', (state.total, job_id))
                    log.info('scrape.progress', job=job_id, count=state.total, users=len(state.unique_authors))
                    now = asyncio.get_event_loop().time()
                    if state.progress_message and now - last_progress_edit >= PROGRESS_INTERVAL:
                        try:
                            await state.progress_message.edit(embed=self._build_progress_embed(state))
                            last_progress_edit = now
                        except discord.HTTPException:
                            pass
                    await asyncio.sleep(BATCH_DELAY)
            state.status = 'complete'
            state.output_path = str(output_path)
            await self.bot.db.execute("UPDATE scrape_jobs\n                   SET status='complete', messages_scraped=?,\n                       output_path=?, completed_at=datetime('now')\n                   WHERE id=?", (state.total, str(output_path), job_id))
            log.info('scrape.complete', job=job_id, messages=state.total, users=len(state.unique_authors), path=str(output_path))
        except asyncio.CancelledError:
            state.status = 'cancelled'
            await self.bot.db.execute("UPDATE scrape_jobs SET status='cancelled', completed_at=datetime('now') WHERE id=?", (job_id,))
            log.info('scrape.cancelled', job=job_id, count=state.total)
            raise
        except Exception as exc:
            state.status = 'failed'
            state.error = str(exc)
            log.error('scrape.failed', job=job_id, error=str(exc))
            await self.bot.db.execute("UPDATE scrape_jobs\n                   SET status='failed', error_msg=?, completed_at=datetime('now')\n                   WHERE id=?", (str(exc)[:500], job_id))
        finally:
            if state.progress_message:
                try:
                    await state.progress_message.edit(embed=self._build_progress_embed(state))
                except discord.HTTPException:
                    pass
            self._jobs.pop(job_id, None)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(Scraper(_a))