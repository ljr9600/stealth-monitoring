# platform/monitoring/

The Stealth observability stack: Prometheus + Grafana + cAdvisor + node-exporter, plus a cron-based sampler. Runs on every host (`.30`, `.50`) so you can see container metrics + host metrics + system health for the platform.

This is NOT a single service — it's a multi-container compose stack co-located on each host.

---

## Containers

| Container | Image | Port | Purpose |
|---|---|---|---|
| `monitoring-prometheus` | `prom/prometheus:v2.54.1` | `9090` | Time-series DB. Scrapes cAdvisor (`:8189`), node-exporter (`:9100`) and itself (`:9090`) every 60s; 30-day retention. No app `/actuator` jobs are configured (see "What's pre-provisioned"). Deployed on both `.30` and `.50` (see "Per-host differences"). **⚠️ `.50`'s instance has been down since 2026-05-31** — see "Known issues". |
| `monitoring-grafana` | `grafana/grafana:11.2.0` | `3000` | Dashboards. UI at `http://<host>:3000`. Pre-provisioned with the dashboards in `grafana/dashboards/` (provider + datasource configs in `grafana/provisioning/`). |
| `monitoring-cadvisor` | `gcr.io/cadvisor/cadvisor:v0.49.1` | `8189` | Per-container CPU/RAM/network/disk metrics. Source for the "container health" panels. (Port overridden from the image default `8080` to `8189` to avoid host-network collisions.) **⚠️ Currently reports `unhealthy`** — see "Known issues" below. Metrics still scrape fine; it's the Docker healthcheck status that's red. |
| `monitoring-node-exporter` | `prom/node-exporter:v1.8.2` | `9100` | Host-level metrics (CPU, RAM, disk, network). |

Plus a host-side cron-driven script:

- **`cron-sampler.sh`** — runs every minute via crontab. Samples `docker stats` (per-container CPU/mem) and host memory/swap/disk/load into two dated CSVs under `~/monitoring-logs/` (`containers-YYYY-MM-DD.csv`, `host-YYYY-MM-DD.csv`); CSVs older than 30 days are auto-deleted. It captures no health endpoints — just `docker stats`, `free`, `df`, and `/proc/loadavg`. Independent of Prometheus — it reads `docker stats` directly, so it's the fallback trail if the TSDB is lost. Env: `MONITORING_LOG_DIR` overrides the output dir (default `~/monitoring-logs`).

## Run

```bash
cd ~/dev/platform/monitoring
docker compose up -d
```

- **Grafana UI:** `http://<host-ip>:3000` (default login: admin / admin → prompted to change).
- **Prometheus UI:** `http://<host-ip>:9090` (no auth; LAN-only).

**⚠️ Always deploy from `~/dev/platform/monitoring`.** The compose project name is `monitoring` (directory basename), so the `prometheus-data` / `grafana-data` volumes survive redeploys from this path. Containers created from the retired pre-reorg path (`~/dev/monitoring`) must be removed and recreated from here — a stale pre-reorg deployment is exactly what broke prod `.50` (see "Known issues").

## Cron-sampler install

The cron-sampler is run via a crontab line on `.30` + `.50`:

```cron
* * * * * /home/lloyd/dev/platform/monitoring/cron-sampler.sh >> /home/lloyd/monitoring-logs/cron.err 2>&1
```

**⚠️ Verify the crontab points at `~/dev/platform/monitoring/...`** — the pre-reorg `~/dev/monitoring/` path is dead. Both hosts' crontabs still had the old path as of 2026-07, so the sampler silently wrote nothing after 2026-05-22 (check with `crontab -l | grep cron-sampler` and `ls -lt ~/monitoring-logs/ | head`).

(`.35` does not have the cron-sampler — see `docs/INFRASTRUCTURE.md` for why.)

## What's pre-provisioned

- **Prometheus scrape targets** (`prometheus/prometheus.yml`) — three jobs, all on `localhost` (host networking): `cadvisor` (`:8189`), `node-exporter` (`:9100`), and `prometheus` (`:9090`) itself. There are currently **no per-app `/actuator/prometheus` scrape jobs** in this config; container + host metrics come from cAdvisor/node-exporter. Add an app job here if you want JVM/app metrics scraped.
- **Grafana dashboards** — the JSONs live in `grafana/dashboards/` (bind-mounted to `/dashboards` in the container); `grafana/provisioning/dashboards/` holds only the provider config (`dashboards.yml`) that tells Grafana to load them. Exactly two dashboards ship in this repo:
  - **"Cadvisor exporter"** (`grafana/dashboards/cadvisor-exporter.json`) — per-container CPU/memory/network/filesystem
  - **"Node Exporter Full"** (`grafana/dashboards/node-exporter-full.json`) — host CPU, RAM, disk, network
- **Grafana datasource** — Prometheus, pointed at local `:9090`.

## Bind-mounts (host paths)

| Host | Container | Why |
|---|---|---|
| `/var/run/` | `/var/run:ro` | cAdvisor reads Docker daemon socket |
| `/sys/` | `/sys:ro` | cAdvisor reads cgroups |
| `/var/lib/docker/` | `/var/lib/docker:ro` | cAdvisor reads container layer metadata |
| `/dev/disk/` | `/dev/disk:ro` | cAdvisor disk metadata |
| `/` | `/rootfs:ro` | cAdvisor reads host root filesystem |
| `/` | `/host:ro,rslave` | node-exporter host filesystem metrics (`--path.rootfs=/host`) |
| `prometheus-data` (named volume) | `/prometheus` | Time-series retention (30d, `--storage.tsdb.retention.time=30d`) |
| `grafana-data` (named volume) | `/var/lib/grafana` | Dashboards + users |

## Env vars

| Var | Purpose |
|---|---|
| `MONITORING_LOG_DIR` | Where cron-sampler writes (`~/monitoring-logs/` by default) |
| `GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD` | Override Grafana default admin (optional) |
| `GF_SERVER_HTTP_PORT` | `3000` — Grafana listen port (set in compose) |
| `GF_USERS_ALLOW_SIGN_UP` | `false` — disable Grafana self-signup (set in compose) |
| `CADVISOR_HEALTHCHECK_URL` | `http://localhost:8189/healthz` — points the image's healthcheck at the overridden port (set in compose) |

## Logging

All four containers log via the Docker `json-file` driver; logs are collected under `/var/log/containers/<container-name>/` per `docs/LOGGING.md` — no app-specific log files. The cron-sampler's own stderr goes to `~/monitoring-logs/cron.err` (see the crontab line above).

## Per-host differences

- **The full 4-container stack is deployed on both `.30` and `.50`**, each scraping only its own localhost exporters — there is no central cross-host scrape. `.30:9090` (Prometheus) and `.30:3000` (Grafana) are the primary dashboards. **NOTE:** the `.50` deployment was created from the pre-reorg `~/dev/monitoring` path and is currently broken — see "Known issues".
- The scrape config in this repo (`prometheus/prometheus.yml`) targets only `localhost` — i.e. each host's Prometheus scrapes that host's own exporters. To collect `.50` metrics centrally on `.30`, add `.50` targets to `prometheus.yml`.
- `.35` (data-tier host) and `.25` (RAM-cache host) do not run the monitoring stack.

## Known issues

- **`monitoring-prometheus` on `.50` has been `Exited(127)` since 2026-05-31 ~15:27 ET** —
  root cause confirmed 2026-07 (this is the pre-reorg-path problem, not a config bug):
  the `.50` stack was deployed from the pre-reorg path `/home/lloyd/dev/monitoring`
  (visible in the container's `com.docker.compose.project.working_dir` label). After
  the repo moved to `~/dev/platform/monitoring`, the bind-mount source
  `prometheus/prometheus.yml` no longer existed at the old path; on the 2026-05-31
  restart, dockerd auto-created it as a root-owned **directory**, and mounting a
  directory onto the image's config **file** (`/etc/prometheus/prometheus.yml`)
  fails at container init — `docker inspect` shows `State.Error` = "not a
  directory ... Are you trying to mount a directory onto a file", exit code 127.
  **Impact:** NO container/host metrics have been collected on prod `.50` since
  2026-05-31 (nothing else covers `.50` — `.30`'s Prometheus scrapes only its own
  localhost). **Fix procedure (needs `.50` prod go-ahead, outside market hours):**
  on `.50`, remove the bogus auto-created tree `/home/lloyd/dev/monitoring/`
  (verify first it contains only the empty dockerd-created dirs, nothing real),
  then `docker compose down` the old-path containers and recreate the stack from
  `~/dev/platform/monitoring` (`docker compose up -d`). The compose project name
  is `monitoring` either way (same directory basename), so the
  `monitoring_prometheus-data` TSDB volume is reused — history from before the
  outage survives.
- **`monitoring-cadvisor` may show `unhealthy` in `docker ps` on hosts where the
  container predates commit `ce88d3a` (healthcheck fix)** — e.g. `.50`, whose stack
  was created from the pre-reorg `~/dev/monitoring` path. Such containers still
  carry the image-default `CADVISOR_HEALTHCHECK_URL=http://localhost:8080/healthz`,
  which under host networking probes a *different* service on `:8080` and 404s
  every 30s. The env-var override in this compose **works** (verified: `.30`'s
  container, recreated from the current compose, is `healthy`) — so the remedy
  is to recreate the container from `~/dev/platform/monitoring`, not a config
  change. **Impact is cosmetic:** cAdvisor itself is fine — Prometheus still
  scrapes `localhost:8189/metrics` and `curl -fsS http://localhost:8189/healthz`
  returns `ok`; only the Docker-reported health badge is red. On `.50` the
  recreate falls under the prometheus fix procedure above (same redeploy, same
  go-ahead).
- **`monitoring-grafana` on `.50` runs with EMPTY provisioning** — same pre-reorg
  root cause: its bind mounts point at `/home/lloyd/dev/monitoring/grafana/{provisioning,dashboards}`,
  which are empty dockerd-auto-created directories. Provisioning changes committed
  to this repo never reach `.50`, and after a `grafana-data` volume loss `.50`'s
  Grafana would come up with no datasource and no dashboards (so the "Backup
  story" re-provisioning claim below does not hold on `.50` until it's redeployed).
  Fixed by the same recreate-from-`~/dev/platform/monitoring` procedure.
- **cron-sampler crontabs went stale in the reorg** — both `.30` and `.50` crontabs
  still pointed at the dead `/home/lloyd/dev/monitoring/cron-sampler.sh` as of
  2026-07, so the CSV fallback trail captured nothing after 2026-05-22 (including
  during the `.50` Prometheus outage it was designed for). Update the crontab line
  to the `~/dev/platform/monitoring/` path shown in "Cron-sampler install" above.

## Backup story

The TSDB (`prometheus-data`) and the Grafana DB (`grafana-data`) are local volumes — **lost** if the host disk dies. That's accepted: metrics are short-lived observability data, not canonical state. If `.30` or `.50`'s monitoring volume is lost, the dashboards re-provision automatically on restart; you'll just have a gap in the historical metrics.

## See also

- `docs/OBSERVABILITY.md` — the 4-layer observability model (logs/metrics/dashboards/alerts) + runbooks ("service slow", "service down", "find log line")
- `docs/LOGGING.md` — where each service writes logs (the OTHER pillar of observability)
- `docs/INFRASTRUCTURE.md` § Monitoring stack — host placement
