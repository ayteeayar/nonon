
# project structure

## directory layout

```
nonon/
├── main.py
├── pyproject.toml
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── backup.sh
├── splitcsv.sh
│
├── config/
│   ├── config.yml
│   └── presets/
│       ├── minimal.yml
│       ├── gaming.yml
│       ├── creative.yml
│       ├── social.yml
│       └── study.yml
│
├── core/
│   ├── bot.py
│   ├── config.py
│   ├── logging_setup.py
│   ├── permissions.py
│   ├── vanish.py
│   └── vanish_cog.py
│
├── database/
│   ├── connection.py
│   └── migrations/
│       ├── 001_initial.sql
│       ├── 002_presence.sql
│       ├── 003_csv_import.sql
│       ├── 004_markov.sql
│       ├── 005_captcha.sql
│       ├── 006_guild_config.sql
│       └── 007_markov_fix_unique.sql
│
├── analytics/
│   ├── tracker.py
│   ├── presence_tracker.py
│   └── csv_import.py
│
├── configure/
│   ├── cog.py
│   ├── store.py
│   ├── merger.py
│   └── coerce.py
│
├── discord_layer/
│   ├── channel_manager.py
│   ├── permission_manager.py
│   └── rate_limiter.py
│
├── logging_system/
│   ├── event_logger.py
│   └── console_relay.py
│
├── lookup/
│   └── commands.py
│
├── markov/
│   ├── engine.py
│   ├── webhook_manager.py
│   └── cog.py
│
├── media/
│   ├── reel.py
│   └── song.py
│
├── moderation/
│   ├── commands.py
│   ├── automod.py
│   ├── infractions.py
│   └── captcha.py
│
├── providers/
│   ├── base.py
│   ├── local.py
│   ├── remote.py
│   └── github.py
│
├── scraping/
│   └── scraper.py
│
├── scripts/
│   ├── migrate.py
│   ├── export.py
│   └── purge_old_logs.py
│
├── setup/
│   ├── cog.py
│   ├── models.py
│   └── preset_loader.py
│
├── sync/
│   └── engine.py
│
└── tests/
    ├── conftest.py
    └── test_suite.py
```

---

## module descriptions

### `main.py`

entry point. initialises structured logging, loads and validates `config.yml`, starts the aiohttp health check server, registers os signal handlers, and starts the bot.

---

### `core/`

| file | responsibility |
|---|---|
| `bot.py` | `KnowledgeBot` — main bot class. manages cog loading, slash command sync, graceful shutdown, and guild config application. |
| `config.py` | pydantic v2 models for the entire configuration tree. `load_config()` reads yaml, resolves env var references, and validates on startup. |
| `logging_setup.py` | configures structlog with optional json output and file logging. |
| `permissions.py` | `PermissionResolver` — evaluates whether a user may invoke a command by checking the `permission_grants` table and configured role defaults. `scope_commands` maps scope names to command lists. |
| `vanish.py` | in-memory per-user ephemeral toggle state. |
| `vanish_cog.py` | cog exposing `/vanish` command. |

---

### `database/`

| file | responsibility |
|---|---|
| `connection.py` | `DatabasePool` — async wrapper around aiosqlite or asyncpg. provides `acquire()` context manager used by all cogs. |
| `migrations/` | numbered, append-only sql files. `001_initial.sql` creates the full base schema; subsequent files add tables or columns incrementally. |

---

### `analytics/`

| file | responsibility |
|---|---|
| `tracker.py` | `/stats`, `/exportstats`, `/analyticsdebug` commands. schedules hourly snapshots and weekly summaries as background tasks. |
| `presence_tracker.py` | listens for `on_presence_update` events and writes rows to `presence_events`. respects `presence_min_interval_seconds` to suppress rapid updates. |
| `csv_import.py` | `/importcsv` and `/importstatus` commands. parses scraper-produced csv files in batches, inserts into `messages`, and backfills `analytics_snapshots`. |

---

### `configure/`

| file | responsibility |
|---|---|
| `cog.py` | `/configure` command group and all subcommands (channels, logging, moderation, analytics, markov, captcha, source, media, show, reset, save-to-file, import-from-file). |
| `store.py` | `GuildConfigStore` — reads and writes the `guild_config` key-value table. `SECTION_MODELS` maps section names to pydantic model classes. |
| `merger.py` | `apply_guild_db_overrides` — reads all stored overrides for a guild and deep-merges them into the running `NonobotConfig`. |
| `coerce.py` | `coerce_value` — converts a raw string value to the correct python type for a given pydantic field. |

---

### `discord_layer/`

| file | responsibility |
|---|---|
| `channel_manager.py` | utilities for channel creation, deletion, and permission overwrite management. used by sync and captcha. |
| `permission_manager.py` | `/permit` and `/revoke` commands for managing the `permission_grants` table. |
| `rate_limiter.py` | per-guild rate limiting utilities for outbound discord api calls. |

---

### `logging_system/`

| file | responsibility |
|---|---|
| `event_logger.py` | primary event cog. listens for all discord events (messages, members, roles, channels, voice, invites, boosts) and writes to the database and log channels. each event type maps to a configurable toggle. |
| `console_relay.py` | background task that periodically flushes the structlog output buffer to a configured discord channel. |

---

### `lookup/`

| file | responsibility |
|---|---|
| `commands.py` | `/lookup` command group: `profile`, `messages`, `infractions`, `voice`, `presence`, `mutual`. queries the database and formats multi-embed responses. |

---

### `markov/`

| file | responsibility |
|---|---|
| `engine.py` | markovify wrapper: `build_model`, `generate_sentence`, `save_model`, `load_model`. handles json serialisation and state size configuration. |
| `webhook_manager.py` | discord webhook creation and caching by `(guild_id, channel_id, persona_name)`. dispatches generated text via a webhook persona. |
| `cog.py` | `/markov` command group: `train`, `generate`, `list`, `delete`, `persona add`, `persona list`, `persona loadfile`. |

---

### `media/`

| file | responsibility |
|---|---|
| `reel.py` | `/reel` and `/frame` commands. invokes yt-dlp and ffmpeg as subprocesses, handles size limits, and posts the result. |
| `song.py` | `/song` command. downloads audio via yt-dlp, converts to mp3 via ffmpeg, optionally crops album art and injects lyrics via lyricsgenius/mutagen, handles playlists by creating a new channel. |

---

### `moderation/`

| file | responsibility |
|---|---|
| `commands.py` | all manual moderation commands: `/warn`, `/mute`, `/kick`, `/ban`, `/softban`, `/note`, `/lock`, `/unlock`, `/purge`, `/slowmode`, `/nickname`, `/role`, `/infractions`, `/massban`. |
| `automod.py` | `on_message` listener that evaluates all enabled automod checks (spam, banned words/patterns, mention spam, link filter, line count, raid detection) and escalates to mute or ban on threshold breach. |
| `infractions.py` | `InfractionManager` — creates, reads, and counts infraction records in the `infractions` table. |
| `captcha.py` | `/captcha lock` and `/captcha release` commands. manages the captcha role, per-channel permission overwrites, private challenge channel, and `on_message` listener for challenge validation. session state is persisted in `captcha_sessions`. |

---

### `providers/`

| file | responsibility |
|---|---|
| `base.py` | abstract `ProviderBase` interface with `list_files()` and `read_file()` methods. |
| `local.py` | local filesystem provider using `watchdog` for event-driven change detection. |
| `remote.py` | base class for polling-based remote providers. |
| `github.py` | github api provider — polls a repository branch for markdown file changes. |

> google drive and onedrive providers are initialised by the sync engine using the google api python client and msal respectively; their provider classes follow the same interface.

---

### `scraping/`

| file | responsibility |
|---|---|
| `scraper.py` | `/scrape` command. paginates through a channel's message history using the discord api and writes structured csv output with metadata columns. |

---

### `scripts/`

| file | responsibility |
|---|---|
| `migrate.py` | migration runner. discovers and applies all pending `.sql` files from `database/migrations/` in filename order. idempotent. |
| `export.py` | data export utilities. |
| `purge_old_logs.py` | deletes log rows older than `logging.retention_days`. intended for scheduled execution (cron or task scheduler). |

---

### `setup/`

| file | responsibility |
|---|---|
| `cog.py` | `/setup` command group: `apply`, `list`, `reload`, `preview`. |
| `models.py` | pydantic models for preset yaml validation: `PresetRole`, `PresetChannel`, `PresetCategory`, `ServerPreset`. |
| `preset_loader.py` | loads and validates all yaml files in `config/presets/`. supports hot-reload via `/setup reload`. |

---

### `sync/`

| file | responsibility |
|---|---|
| `engine.py` | note sync cog. polls the configured backend on `source.poll_interval_seconds`. diffs the file listing against `sync_state` table entries, creates/updates/deletes discord channels accordingly, and optionally appends discord replies back to source files. |

---

### `tests/`

| file | responsibility |
|---|---|
| `conftest.py` | shared pytest fixtures: mock bot, mock database, sample config. |
| `test_suite.py` | integration and unit tests covering core workflows. |
