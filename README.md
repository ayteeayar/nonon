
# nonon

a production-grade discord bot for server analytics, moderation, event logging, user intelligence, media downloads, markov text generation, and server scaffolding. supports any number of guilds simultaneously with full per-guild configuration — either via yaml or the in-discord `/configure` interface.

---

## features

| category | capabilities |
|---|---|
| **analytics** | hourly snapshots, weekly summaries, raw activity counts, csv export, historical csv import |
| **moderation** | warn, mute, kick, ban, softban, lock/unlock, purge, slowmode, massban, infraction tracking |
| **automod** | spam detection, banned words/patterns, mention spam, link filtering, line limits, raid detection |
| **event logging** | message edits/deletes, member joins/leaves, role/nickname/avatar changes, voice state, invites, boosts |
| **presence tracking** | per-user status and activity event log with configurable minimum interval |
| **lookup** | user profiles, message history, infraction records, voice sessions, presence timelines, cross-user correlation |
| **note sync** | mirrors a markdown directory into discord channels; backends: local, github, ftp, google drive, onedrive |
| **markov** | train models from message history, generate text via discord webhooks with named personas |
| **media** | `/reel` for video download/repost, `/frame` for frame extraction, `/song` for audio with id3 tags and lyrics |
| **captcha** | joke-mute mechanic isolating a user until they complete a count or phrase challenge |
| **setup** | scaffold server structures (roles, channels, overwrites) from preset yaml files |
| **configure** | full in-discord guild configuration via `/configure`; all changes take effect immediately without restarts |
| **console relay** | streams live structured log output to a designated discord channel |
| **log forwarding** | route all log embeds from one guild to a central monitoring guild |
| **permissions** | zero-trust grant system with user- and role-level scopes; owner-only hard limits |

---

## quick start

```bash
git clone https://github.com/ayteeayar/nonon
cd nonon
cp .env.example .env
# edit .env — set BOT_TOKEN and BOT_OWNER_ID at minimum
pip install -r requirements.txt
python -m scripts.migrate
python main.py
```

or with docker:

```bash
cp .env.example .env
# edit .env
docker compose up -d
```

---

## installation

**prerequisites**

- python 3.12+
- ffmpeg system binary (`apt install ffmpeg` / `brew install ffmpeg`)
- yt-dlp (`pip install yt-dlp` or system binary) — required for `/reel`, `/frame`, `/song`

**install dependencies**

```bash
pip install -r requirements.txt
```

optional extras:

```bash
pip install pillow          # thumbnail cropping for /song
pip install lyricsgenius    # lyrics injection for /song
pip install mutagen         # id3 tag writing (required with lyricsgenius)
```

**run migrations**

```bash
python -m scripts.migrate
# or with explicit config path:
python -m scripts.migrate --config config/config.yml
```

**start the bot**

```bash
python main.py
```

---

## configuration

all configuration lives in `config/config.yml`. secrets are read from environment variables and never stored in yaml.

guilds can also be fully configured from within discord using `/configure`. changes are persisted to the `guild_config` database table and applied immediately — no restart required. the yaml file remains the authoritative source for global defaults.

**minimal `.env`**

```env
BOT_TOKEN=your_discord_bot_token_here
BOT_OWNER_ID=your_discord_user_id_here
```

**key `config.yml` sections**

| section | purpose |
|---|---|
| `bot` | name, command prefix, owner id env var, status text |
| `source` | note sync backend type, path, poll interval, reply behaviour |
| `discord` | per-guild channel ids for log routing, dry-run flag, log forwarding target |
| `logging` | log level, retention, per-event-type toggles, attachment re-upload, presence settings, console relay |
| `moderation` | escalation thresholds, raid detection, link whitelist, banned words/patterns, automod toggles |
| `database` | backend (`sqlite` or `postgresql`), sqlite path, postgresql dsn env, pool sizes |
| `analytics` | snapshot interval, weekly summary schedule, export path |
| `media` | reel/song enabled flags, max file size caps |
| `markov` | model directory, per-guild model cap, generation count cap, minimum training messages |
| `captcha` | category name, count challenge bounds, phrase word count bounds |
| `setup` | preset directory path |
| `health` | http health check host and port |
| `guilds` | per-guild overrides — any of the above blocks, keyed by guild id |

see [docs/setup.md](docs/setup.md) for full configuration instructions.

---

## usage

```bash
# show activity statistics for the last 7 days
/stats days:7

# look up a user's full profile
/lookup profile user_id:123456789

# train a markov model from a channel
/markov train name:general-model source_type:channel source_id:987654321

# download and repost a video
/reel url:https://youtube.com/watch?v=...

# apply a server structure preset
/setup apply preset:gaming
```

see [docs/usage.md](docs/usage.md) for full command reference and workflows.

---

## project structure

```
nonon/
├── main.py                    # entry point
├── config/
│   ├── config.yml             # global config and per-guild overrides
│   └── presets/               # server structure yaml presets
├── core/                      # bot class, config models, permissions, vanish
├── database/
│   ├── connection.py          # async sqlite/postgresql pool
│   └── migrations/            # versioned sql migration files
├── analytics/                 # stats, presence tracking, csv import
├── configure/                 # /configure command group and guild config store
├── discord_layer/             # channel manager, permission manager, rate limiter
├── logging_system/            # event logger, console relay
├── lookup/                    # /lookup command group
├── markov/                    # markov engine, webhook personas, /markov cog
├── media/                     # /reel, /frame, /song
├── moderation/                # commands, automod, infractions, captcha
├── providers/                 # note sync backends (local, github, ftp, gdrive, onedrive)
├── scraping/                  # /scrape channel exporter
├── scripts/                   # migrate, export, purge_old_logs
├── setup/                     # /setup cog, preset loader, preset models
├── sync/                      # note sync engine
└── tests/                     # test suite
```

see [docs/project-structure.md](docs/project-structure.md) for full module-level descriptions.

---

## health check

when `health.enabled: true` (default), an http server starts alongside the bot:

- `GET /healthz` — returns bot status, guild count, latency, uptime
- `GET /readyz` — returns `503` until the bot is fully ready

---

## roadmap

- postgresql support is implemented but not yet battle-tested at scale
- prometheus metrics endpoint
- web dashboard for analytics and infraction management
- additional note sync backends

---

## contributing

contributions are welcome. please read [docs/contributing.md](docs/contributing.md) before opening a pull request.

1. fork the repository
2. create a branch from `main`
3. make changes with tests where applicable
4. run `ruff check .` and `mypy .` before submitting
5. open a pull request with a clear description of the change

---

## license

mit license. see `LICENSE` for details.
