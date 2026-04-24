
# api

nonon exposes a minimal http api for health monitoring. it is not a general-purpose api — discord commands are the primary interface.

---

## health check server

enabled by default. controlled by the `health` block in `config.yml`.

```yaml
health:
  enabled: true
  host: "0.0.0.0"
  port: 8080
```

---

## endpoints

### `GET /healthz`

returns current bot status. always returns `200 OK` once the server is running, regardless of bot readiness.

**response**

```json
{
  "status": "ok",
  "guilds": 3,
  "latency_ms": 42.5,
  "uptime_seconds": 3600.1,
  "ready": true
}
```

| field | type | description |
|---|---|---|
| `status` | string | always `"ok"` |
| `guilds` | integer | number of guilds the bot is in |
| `latency_ms` | float | discord gateway heartbeat latency in milliseconds |
| `uptime_seconds` | float | seconds since the bot reported ready |
| `ready` | boolean | whether `bot.is_ready()` is true |

---

### `GET /readyz`

readiness probe. returns `200` once the bot is fully ready, `503` beforehand.

**response — ready**

```json
{ "status": "ready" }
```

**response — not ready**

```json
{ "status": "not_ready" }
```
http status: `503 Service Unavailable`

---

## docker healthcheck integration

the `Dockerfile` and `docker-compose.yml` configure the health check automatically:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/healthz"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 20s
```

---

## notes

- the health server runs as an `aiohttp` application on the same event loop as the bot
- there is no authentication on health endpoints — restrict network access at the infrastructure level if needed
- prometheus metrics are not currently implemented; the `/healthz` response is intended for simple uptime checks and container orchestration probes
