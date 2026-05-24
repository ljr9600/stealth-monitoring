# platform/monitoring/

The Stealth observability stack: Prometheus + Grafana + cAdvisor + node-exporter, plus a cron-based sampler. Runs on every host (`.30`, `.50`) so you can see container metrics + host metrics + system health for the platform.

This is NOT a single service — it's a multi-container compose stack co-located on each host.

---

## Containers

| Container | Image | Purpose |
|---|---|---|
| `monitoring-prometheus` | `prom/prometheus:v2.54.1` | Time-series DB. Scrapes targets (cAdvisor, node-exporter, app `/actuator/prometheus` endpoints) every 15s. |
| `monitoring-grafana` | `grafana/grafana:11.2.0` | Dashboards. UI at `http://<host>:3000`. Pre-provisioned with the Stealth dashboard set in `grafana/provisioning/`. |
| `monitoring-cadvisor` | `gcr.io/cadvisor/cadvisor:v0.49.1` | Per-container CPU/RAM/network/disk metrics. Source for the "container health" panels. |
| `monitoring-node-exporter` | `prom/node-exporter:v1.8.2` | Host-level metrics (CPU, RAM, disk, network). |

Plus a host-side cron-driven script:

- **`cron-sampler.sh`** — runs every minute via crontab. Captures `docker stats` + key health endpoints + writes JSONL to `~/monitoring-logs/` for ad-hoc post-mortems. Not a Prometheus replacement — a complementary cheap-log capture.

## Run

```bash
cd ~/dev/platform/monitoring
docker compose up -d
```

- **Grafana UI:** `http://<host-ip>:3000` (default login: admin / admin → prompted to change).
- **Prometheus UI:** `http://<host-ip>:9090` (no auth; LAN-only).

## Cron-sampler install

The cron-sampler is run via a crontab line on `.30` + `.50`:

```cron
* * * * * /home/lloyd/dev/platform/monitoring/cron-sampler.sh >> /home/lloyd/monitoring-logs/cron.err 2>&1
```

(`.35` does not have the cron-sampler — see `docs/INFRASTRUCTURE.md` for why.)

## What's pre-provisioned

- **Prometheus scrape targets** — auto-configured for all `stealth-*` containers exposing `/actuator/prometheus` (Spring Boot Java services).
- **Grafana dashboards** — under `grafana/provisioning/dashboards/`:
  - "Container Health" — per-container CPU/RAM/restart counts
  - "Host Health" — OS-level (CPU, RAM, disk %, network)
  - "JVM" — Java services' GC + heap
  - "Service Endpoints" — per-service `/actuator/health` status
- **Grafana datasource** — Prometheus, pointed at local `:9090`.

## Bind-mounts (host paths)

| Host | Container | Why |
|---|---|---|
| `/var/run/` | `/var/run:ro` | cAdvisor reads Docker daemon socket |
| `/sys/` | `/sys:ro` | cAdvisor reads cgroups |
| `/var/lib/docker/` | `/var/lib/docker:ro` | cAdvisor reads container layer metadata |
| `/dev/disk/` | `/dev/disk:ro` | node-exporter for disk stats |
| `prometheus-data` (named volume) | `/prometheus` | Time-series retention (default 15d) |
| `grafana-data` (named volume) | `/var/lib/grafana` | Dashboards + users |

## Env vars

| Var | Purpose |
|---|---|
| `MONITORING_LOG_DIR` | Where cron-sampler writes (`~/monitoring-logs/` by default) |
| `GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD` | Override Grafana default admin (optional) |

## Per-host differences

- `.30` and `.50` BOTH run the full monitoring stack — they're independent. You can compare DEV vs PROD by opening Grafana on each host.
- `.35` does not run monitoring (it's the data-tier host; the data-tier mongo/elastic emit their own metrics consumed by `.30`/`.50`'s Prometheus).
- `.25` does not run monitoring (low-overhead RAM cache host).

## Backup story

The TSDB (`prometheus-data`) and the Grafana DB (`grafana-data`) are local volumes — **lost** if the host disk dies. That's accepted: metrics are short-lived observability data, not canonical state. If `.30` or `.50`'s monitoring volume is lost, the dashboards re-provision automatically on restart; you'll just have a gap in the historical metrics.

## See also

- `docs/OBSERVABILITY.md` — the 4-layer observability model (logs/metrics/dashboards/alerts) + runbooks ("service slow", "service down", "find log line")
- `docs/LOGGING.md` — where each service writes logs (the OTHER pillar of observability)
- `docs/INFRASTRUCTURE.md` § Monitoring stack — host placement
