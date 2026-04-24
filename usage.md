
# usage

all commands are discord slash commands. access is controlled by the permission grant system — see [permissions](#permissions) below.

---

## permissions

by default, all commands are restricted to the bot owner. access is granted explicitly via `/permit`.

```
/permit user:@alice scope:lookup
/permit role:@Moderators scope:moderation
/revoke user:@alice scope:lookup
```

**built-in scopes:** `stats`, `exportstats`, `lookup`, `moderation`, `media`, `markov`, `configuration`

---

## analytics

```
/stats days:7
```
shows activity statistics for the last n days (1–90). displays snapshot messages, voice minutes, unique authors, infractions, joins/leaves, and imported csv-sourced counts.

```
/exportstats days:30
```
exports analytics snapshots as a csv file attachment.

---

## lookup

```
/lookup profile user_id:123456789
```
comprehensive user report: account info, nickname/username history, infractions summary, message stats, most active channel, voice time, last seen.

```
/lookup messages user_id:123456789 limit:50 after:2024-01-01
```
paginated message history with optional channel filter and date range.

```
/lookup infractions user_id:123456789
```
full infraction history, paginated.

```
/lookup voice user_id:123456789 days:30
```
voice session history for a user.

```
/lookup presence user_id:123456789 days:7
```
status and activity event log with an hourly heatmap summary and up to 60 recent events.

```
/lookup mutual user_id_a:111 user_id_b:222
```
cross-reference two users: shared channels, voice session overlaps, co-occurring infractions.

---

## moderation

```
/warn user:@user reason:spamming
/mute user:@user duration:60 reason:repeated warnings
/kick user:@user reason:...
/ban user:@user reason:...
/softban user:@user reason:...       # ban + immediate unban to delete messages
/note user:@user text:internal note
```

```
/lock channel:#general
/unlock channel:#general
/purge limit:100
/slowmode seconds:5
/nickname user:@user nick:new-name
/role user:@user role:@SomeRole action:add
/massban [user_ids...]
/infractions user:@user
```

all moderation actions create infraction records queryable via `/lookup infractions`.

**automod escalation:** reaching `auto_mute_threshold` infractions triggers an automatic mute; reaching `auto_ban_threshold` triggers an automatic ban. thresholds are configurable per guild.

---

## markov

```
# train a model from a channel's message history
/markov train name:general-model source_type:channel source_id:987654321

# train from a specific user
/markov train name:alice-model source_type:user source_id:111222333

# generate text
/markov generate model:general-model count:3

# generate with a persona via webhook
/markov generate model:general-model persona:hal9000 seed:i think

# list models
/markov list

# register a persona
/markov persona add name:hal9000 avatar_url:https://example.com/hal.png

# batch-import personas from a json file
/markov persona loadfile attachment:<file.json>

# delete a model (owner only)
/markov delete model:general-model
```

**persona json format**

```json
[
  { "name": "hal9000", "avatar_url": "https://example.com/hal.png" },
  { "name": "clippy" }
]
```

---

## media

```
# download and repost a video
/reel url:https://youtube.com/watch?v=...

# extract a frame at a specific timestamp
/frame url:https://youtube.com/watch?v=... timestamp:01:23

# download audio as mp3
/song url:https://youtube.com/watch?v=... quality:320

# download a playlist (creates a new channel)
/song url:https://youtube.com/playlist?list=...
```

non-owner users are subject to `song_max_mb` per file (default 10 mb). the owner is limited only by the guild's discord upload cap.

---

## captcha

```
# lock a user in a challenge channel
/captcha lock user:@user

# manually release without requiring completion
/captcha release user:@user
```

challenges are randomly assigned: count (count from 1 to n) or phrase (type an exact random phrase). on success the captcha channel is automatically deleted.

---

## setup (server scaffolding)

```
# list available presets
/setup list

# preview what a preset would create
/setup preview preset:gaming

# apply a preset
/setup apply preset:gaming

# hot-reload preset files from disk
/setup reload
```

all setup commands are owner-only.

---

## scraping

```
/scrape channel:#general
```

scrapes a channel's message history and produces a structured csv file attachment. the output filename format is `scrape_{channel_id}_{job}_{yyyymmdd}_{hhmmss}.csv`. owner only.

---

## csv import

```
# import a scraper-produced csv
/importcsv attachment:<file.csv>

# dry run — count rows without writing
/importcsv attachment:<file.csv> dry_run:true

# check import status for a channel
/importstatus channel:#general
```

historical csv imports backfill `analytics_snapshots` so that `/stats` reflects full server history.

---

## configure

```
# show all current overrides for this guild
/configure show

# assign log channels
/configure channels log channel:#bot-log
/configure channels audit channel:#audit-log

# toggle event logging
/configure logging toggle event:log_message_deletes enabled:false

# manage automod
/configure moderation toggle automod_spam_enabled:true
/configure moderation banned-words action:add word:badword
/configure moderation banned-words action:list

# reset a section to global defaults
/configure reset section:moderation

# sync in-memory state back to config.yml (owner only)
/configure save-to-file

# import existing yaml block into the db store (owner only)
/configure import-from-file
```

---

## vanish mode

```
/vanish
```

toggles ephemeral mode for your session. when off, bot responses to your commands are posted publicly in the channel.

---

## troubleshooting

**commands are not appearing**

slash commands may take up to an hour to propagate globally. to sync immediately, set `discord.guild_id` in `config.yml` to your guild id and restart.

**bot is not responding to automod**

all automod checks are disabled by default. enable them individually in `config.yml` under `moderation:` or via `/configure moderation toggle`.

**migration fails with "column already exists"**

this is expected if a migration was previously interrupted. the runner handles duplicate `ALTER TABLE ADD COLUMN` statements silently — re-run `python -m scripts.migrate` safely.

**markov generation returns nothing**

the model may not have enough training data. check `markov.min_training_messages` (default 50) and ensure the source has sufficient message history. use `/markov list` to verify the model exists.

**`/song` fails with ffmpeg error**

ensure `ffmpeg` is installed as a system binary and available on `$PATH`. `pip install ffmpeg` does not install the system binary.

**log relay is flooding the console channel**

adjust `logging.console_relay_max_lines` and `logging.console_relay_level` to reduce volume. set `console_relay_level: WARNING` to suppress info-level output.
