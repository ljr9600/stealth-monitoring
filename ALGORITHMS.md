# ALGORITHMS.md — platform/monitoring

Audience: a junior engineer taking over this stack. `README.md` already covers the
container roster, ports, bind-mounts, run/deploy steps, per-host differences, and the
current known-issue outages — **read it first, this file does not repeat it.** This
doc covers only the parts with real *logic* or *non-obvious design decisions*: the
`cron-sampler.sh` parsing algorithm and the tuning/binding constants baked into
`docker-compose.yml` and `prometheus/prometheus.yml`.

Reality check: monitoring is 95% upstream images (Prometheus, Grafana, cAdvisor,
node-exporter) wired together with config. The **only hand-written code** in the repo
is `cron-sampler.sh`. Everything else is configuration whose *values were chosen
deliberately* — those choices are what a junior will trip on, so they are documented
below with the reasoning from the code comments.

---

## 1. cron-sampler.sh — the CSV fallback trail

**What it does (one line):** every minute, cron runs this script to append one row
per running container (CPU%, memory) and one host row (RAM/swap/disk/load) to dated
CSVs under `~/monitoring-logs/`, independent of Prometheus.
Lives in `cron-sampler.sh` (whole file, 64 lines).

### Why it exists (design rationale)
It reads `docker stats` **directly**, not Prometheus (`cron-sampler.sh:11-12`). So if
the Prometheus TSDB or its volume is ever lost — exactly the `.50` outage the README
documents — this plain-text trail still has the CPU/mem/disk trend. The two systems
are deliberately independent so they can't fail together. The tradeoff: it's coarse
(1-minute `docker stats` snapshots, no per-metric time series, no querying beyond
`awk`), which is fine for a *fallback*.

### Step-by-step
1. **PATH hardening** (`:19-20`). cron runs with a minimal `PATH`, so the script
   explicitly sets `PATH=/usr/local/bin:/usr/bin:/bin` or `docker`/`free`/`df` won't
   be found. This is the #1 reason a cron script "works in my shell but writes nothing
   from cron" — a junior gotcha.
2. **Paths & headers** (`:22-31`). `LOG_DIR` = `$MONITORING_LOG_DIR` or `~/monitoring-logs`.
   Files are dated: `containers-YYYY-MM-DD.csv`, `host-YYYY-MM-DD.csv`. The header line
   is written only if the file doesn't already exist (`[ -f "$CONT" ] ||`), so the
   first run of each day creates the file with a header and every later run in that day
   just appends rows.
3. **Per-container sampling** (`:47-53`). `docker stats --no-stream --format
   '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'` gives one pipe-delimited line
   per container. `--no-stream` = take one snapshot and exit (otherwise it streams
   forever and cron would hang). The `MemUsage` field looks like `"9.92GiB / 14GiB"`;
   the script splits it with bash parameter expansion:
   - `used=${mem%% / *}` → strips the longest ` / *` suffix → `"9.92GiB"`
   - `lim=${mem##* / }` → strips the longest `* / ` prefix → `"14GiB"`
   Percent tokens have their trailing `%` stripped (`${cpu%\%}`, `${memp%\%}`) so the
   CSV holds bare numbers.
4. **Unit normalization — `to_mib()`** (`:34-44`). This is the one real algorithm.
   `docker stats` emits human units (`9.92GiB`, `512MiB`, `0B`) that you can't compare
   numerically. `to_mib` splits a token into number + unit with awk and converts
   everything to **MiB**:
   - `GiB`/`GB` → `× 1024`
   - `MiB`/`MB` → `× 1` (unchanged, this is the base unit)
   - `KiB`/`kB` → `÷ 1024`
   - `B` or no unit → `÷ 1048576`
   - anything else → passed through unchanged (defensive fallback)
   Gotcha: it treats `GiB` and `GB` (and `MiB`/`MB`, etc.) as **identical** — a
   deliberate simplification, since Docker's ~2.4% GiB-vs-GB discrepancy is noise for a
   trend log. Don't "fix" this expecting exact bytes.
5. **Host sampling** (`:56-60`). `free -m` → total/used/available RAM (`$2 $3 $7` of the
   `Mem:` line) and swap used; `df -BG --output=pcent,avail /` → root-disk used-% and
   available GiB; `/proc/loadavg` field 1 → 1-minute load average. All appended as one
   host row.
6. **Retention** (`:63`). `find "$LOG_DIR" -name '*.csv' -mtime +30 -delete` prunes CSVs
   older than **30 days**. This mirrors Prometheus's 30-day TSDB retention so the two
   trails cover the same window. `|| true` keeps a failed prune from failing the script.

### Edge cases / gotchas
- `set -uo pipefail` (`:18`) — **no `-e`.** A single failing command won't abort the
  run, intentional so a transient `docker stats` hiccup still lets the host row write.
- `docker stats … 2>/dev/null` (`:47`) swallows errors; if the Docker socket is down
  you silently get a host row but no container rows for that minute.
- The install is a **crontab line, not managed by compose** (see README "Cron-sampler
  install"). The README documents that both `.30` and `.50` crontabs pointed at the
  dead pre-reorg path `~/dev/monitoring/cron-sampler.sh` and wrote nothing after
  2026-05-22 — always verify `crontab -l | grep cron-sampler` points at
  `~/dev/platform/monitoring/`.

---

## 2. Prometheus scrape config — why 60s, why localhost-only

**Where:** `prometheus/prometheus.yml`.

- **`scrape_interval: 60s` / `evaluation_interval: 60s`** (`:6-7`). Chosen to match the
  1-minute sampling cadence the container CPU/mem/disk trend was specced at. This value
  is load-bearing for the cAdvisor tuning in §3 — the internal housekeeping interval was
  set assuming a 60s scrape, so if you drop this to 15s you must also revisit
  `--housekeeping_interval`.
- **Three jobs, all `localhost`** (`:11-23`): `cadvisor` (`localhost:8189`),
  `node-exporter` (`localhost:9100`), `prometheus` (`localhost:9090` — self-scrape for
  Prometheus's own health). Because every container uses **host networking**, the
  exporters are reachable at plain `localhost:<port>` with no service discovery.
- **Design consequence — no cross-host scrape.** Each host's Prometheus scrapes only
  *its own* localhost exporters. There is no central aggregation: `.30` cannot see `.50`.
  To centralize, add `.50` targets here (README "Per-host differences" says the same).
- **No app `/actuator/prometheus` jobs.** Only container (cAdvisor) + host
  (node-exporter) metrics are collected; JVM/app metrics are *not* scraped by default.
  Add a job here to change that.

---

## 3. docker-compose.yml — the tuning constants and why they're set

These are the non-obvious knobs. Each was set to solve a specific problem; changing one
blindly re-introduces the bug it fixed.

| Setting (file:line) | Value | Why |
|---|---|---|
| cAdvisor `--housekeeping_interval` (`:33`) | `15s` (vs `1s` default) | Cuts cAdvisor CPU **~5x**. The 60s scrape cadence (§2) never needs 1s internal sampling, so the default was pure waste. |
| cAdvisor `--port` (`:27`) | `8189` (vs image default `8080`) | `:8080` collides with other services under host networking. **This is also why `CADVISOR_HEALTHCHECK_URL` (`:25`) must be overridden** to `http://localhost:8189/healthz` — the image's built-in healthcheck hard-codes `:8080`, so without the override the health probe hits a *neighboring* service on `:8080`, 404s, and marks cAdvisor `unhealthy` even though it's fine (this is the exact cosmetic-unhealthy bug the README documents on `.50`). |
| cAdvisor `--docker_only=true` (`:35`) | on | Ignore non-Docker cgroups; less noise, less CPU. |
| cAdvisor `init: true` (`:17`) | tini | tini reaps the orphaned `wget` children the HEALTHCHECK spawns every 30s, preventing zombie-process buildup. |
| cAdvisor `--listen_ip` (`:31`) | `${CADVISOR_IP:-0.0.0.0}`, `.env` sets `127.0.0.1` | Bind loopback so the exporter doesn't occupy `:8189` on the box's **secondary IPs** (`.31/.32/.33`). Local Prometheus still scrapes it via loopback. |
| node-exporter `--web.listen-address` (`:61`) | `${NODE_EXPORTER_LISTEN:-:9100}`, `.env` sets `127.0.0.1:9100` | **The wildcard `:9100` shadows every IP on the host** — including the IQFeed emulator's dedicated `.33/.53`, which then can't bind its *own* `:9100`. Binding a single address frees the port on the other IPs. |
| Prometheus `--web.listen-address` (`:84`) | `${PROM_LISTEN:-:9090}`, `.env` sets `127.0.0.1:9090` | Same secondary-IP-freeing rationale; Prometheus is only reached by local Grafana + admins. |
| Prometheus `user: root` (`:77`) | root | So it can write the TSDB into the named volume without a first-run permission-denied crash loop. |
| Prometheus `--storage.tsdb.retention.time` (`:81`) | `30d` | Matches the cron-sampler's 30-day CSV retention (§1.6). |
| Grafana `GF_SERVER_HTTP_ADDR` (`:104`) | `${GRAFANA_ADDR:-}`, `.env` sets `192.168.1.30` | Bind the host primary IP so Grafana stays reachable on `<host>:3000` while freeing `:3000` on secondary IPs. Empty default = all interfaces (backward compat). |
| memory limits | cadvisor 512M, node-exporter 128M, prometheus 1G, grafana 512M (`:45-48,66-68,89-92,111-114`) | Per-container caps so an observability leak can't starve the monitored host. |

**Key cross-cutting decision — everything uses `network_mode: host`** (compose header
comment, `:10-12`). Every monitored service already uses host networking, and it lets
Prometheus reach the exporters at plain `localhost:<port>` (no Docker DNS, no bridge).
The **cost** is the port-collision fragility above: on host networking a container's
port is the *host's* port, shared across all the box's IP aliases — which is the entire
reason the `--listen_ip` / `--web.listen-address` / `GF_SERVER_HTTP_ADDR` overrides
exist. A junior who removes those "to simplify" will break the IQFeed emulator's `:9100`
and re-occupy ports on the secondary IPs.

---

## 4. Grafana provisioning — how dashboards/datasource appear with zero clicks

**Where:** `grafana/provisioning/` (config) + `grafana/dashboards/` (the JSONs).

- **Datasource** (`provisioning/datasources/prometheus.yml`). On first boot Grafana reads
  this and auto-creates a Prometheus datasource (`uid: prometheus`, `url:
  http://localhost:9090`, `isDefault: true`). No manual "Add data source" step. The
  fixed `uid: prometheus` matters — the dashboard JSONs reference the datasource by that
  uid, so don't rename it.
- **Dashboard provider** (`provisioning/dashboards/dashboards.yml`). Type `file`, watching
  `/dashboards` (the bind-mounted `grafana/dashboards/`) with
  `updateIntervalSeconds: 30` — so **editing a dashboard JSON in the repo shows up in
  Grafana within 30s**, no restart. `allowUiUpdates: true` lets you also edit in the UI;
  `disableDeletion: false`.
- **The two shipped dashboards** are `cadvisor-exporter.json` (per-container
  CPU/mem/net/fs) and `node-exporter-full.json` (host CPU/RAM/disk/net) — standard
  community dashboards, not hand-authored here.
- **Provisioning is bind-mount-path-sensitive.** This is the root of a real prod outage:
  the README documents that `.50`'s Grafana was deployed from the pre-reorg path
  `~/dev/monitoring`, whose `grafana/provisioning` + `grafana/dashboards` are now empty
  dockerd-auto-created dirs — so provisioning changes never reach `.50`. Always deploy
  from `~/dev/platform/monitoring`.

---

## TL;DR for the junior
- The only real code is `cron-sampler.sh`; its one algorithm is `to_mib()` unit
  normalization, and its one operational trap is the cron `PATH` and the crontab path.
- Everything else is upstream images tuned by config. The tuning constants
  (housekeeping 15s, port 8189, per-IP binding) each fix a specific bug — read §3 before
  editing compose.
- Host networking + secondary IPs is the theme behind most of the odd-looking overrides.
- Prometheus/Grafana state is disposable (30d, re-provisions on restart); the CSV trail
  is the independent fallback.
