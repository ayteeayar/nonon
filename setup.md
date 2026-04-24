
# setup

## prerequisites

| dependency | version | notes |
|---|---|---|
| python | 3.12+ | required |
| ffmpeg | any current | system binary — `apt install ffmpeg` / `brew install ffmpeg` |
| yt-dlp | any current | `pip install yt-dlp` or system binary — required for `/reel`, `/frame`, `/song` |
| postgresql | 16+ | optional — sqlite is used by default |

---

## installation

### local (bare metal / virtualenv)

```bash
git clone https://github.com/your-org/nonon
cd nonon
python -m venv .venv
source .venv/bin/activate   # windows: .venv\Scripts\activate
pip install -r requirements.txt
```

optional extras:

```bash
pip install pillow          # thumbnail cropping for /song
pip install lyricsgenius    # lyrics fetching for /song
pip install mutagen         # id3 tag writing — required alongside lyricsgenius
```

### docker

```bash
git clone https://github.com/your-org/nonon
cd nonon
cp .env.example .env
# edit .env
docker compose up -d
```

the compose file starts `nonon-bot` and a `postgres:16-alpine` sidecar. sqlite users can remove the `postgres` service and set `database.backend: sqlite` in `config.yml`.

---

## environment setup

copy `.env.example` to `.env` and fill in values:

```env
# required
BOT_TOKEN=your_discord_bot_token_here
BOT_OWNER_ID=your_discord_user_id_here

# optional — leave blank to use sqlite
DATABASE_URL=postgresql://nonon:changeme@postgres:5432/nonon
POSTGRES_PASSWORD=changeme

# provider credentials — only required for the relevant note sync backends
GITHUB_TOKEN=
FTP_USER=
FTP_PASS=
GDRIVE_CREDENTIALS_JSON=
ONEDRIVE_CLIENT_ID=
ONEDRIVE_CLIENT_SECRET=
ONEDRIVE_TENANT_ID=
```

never commit `.env` to source control.

---

## configuration

all runtime configuration lives in `config/config.yml`. the file is validated against pydantic models at startup. missing or invalid fields abort the process with a descriptive error.

create the file if it does not exist:

```bash
cp config/config.yml.example config/config.yml   # if an example is provided
# or create from scratch — only override what you need
```

**minimal config**

```yaml
bot:
  owner_id_env: BOT_OWNER_ID

discord:
  guild_id: 123456789012345678
  log_channel_id: 123456789012345679

database:
  backend: sqlite
  sqlite_path: ./data/nonon.db
```

**per-guild overrides**

any top-level block can be overridden per guild by adding a `guilds:` section keyed by guild id:

```yaml
guilds:
  "123456789012345678":
    moderation:
      auto_ban_threshold: 10
    logging:
      log_message_edits: false
```

**in-discord configuration**

guilds can be configured entirely from within discord using `/configure`. every write persists to the `guild_config` database table and takes effect immediately. use `/configure show` to inspect the current state and `/configure reset` to restore yaml defaults.

---

## database migration

run migrations before starting the bot for the first time and after every upgrade:

```bash
python -m scripts.migrate
# or
python -m scripts.migrate --config config/config.yml
```

the runner is idempotent — re-running against an already-migrated database is safe. applied versions are tracked in the `schema_migrations` table.

**verify migration state**

```bash
sqlite3 data/nonon.db "select version, applied_at from schema_migrations order by version;"
```

**backup before structural migrations**

```bash
cp data/nonon.db data/nonon.db.bak
python -m scripts.migrate
```

---

## local development workflow

```bash
# install dev dependencies
pip install -e ".[dev]"

# lint
ruff check .

# type-check
mypy .

# run tests
pytest

# run with debug logging
LOG_LEVEL=DEBUG python main.py
```

**dev flags in `config.yml`**

```yaml
discord:
  dry_run: true        # disables channel creates/deletes during note sync

logging:
  level: DEBUG
  json_logs: false     # human-readable output in dev
```

---

## docker volumes

the compose file mounts the following host directories into the container:

| host path | container path | purpose |
|---|---|---|
| `./data` | `/app/data` | sqlite database and markov model files |
| `./logs` | `/app/logs` | structured log files |
| `./exports` | `/app/exports` | analytics csv exports |
| `./notes` | `/app/notes` | local note sync directory |
| `./config` | `/app/config` | config.yml and preset yaml files |

---

## server structure presets

built-in presets are stored in `config/presets/`:

- `minimal` — bare-minimum channel set
- `gaming` — gaming community layout
- `creative` — creative project layout
- `social` — social/community layout
- `study` — study group layout

add custom presets by placing a yaml file in that directory and running `/setup reload`.
