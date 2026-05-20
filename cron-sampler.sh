#!/usr/bin/env bash
#
# cron-sampler.sh — plain-CSV resource logger; a backup to the Grafana
# stack (cAdvisor + Prometheus + Grafana, also in this project).
#
# Runs every minute from cron.  Writes two dated CSVs:
#   <LOG_DIR>/containers-YYYY-MM-DD.csv   per-container CPU + memory
#   <LOG_DIR>/host-YYYY-MM-DD.csv         host memory / swap / disk / load
#
# Why a CSV backup when Grafana already exists: it reads `docker stats`
# directly (not Prometheus), so if Prometheus or its volume is ever lost
# this plain-text trail still has the trend.  The two are independent.
#
# Review later — e.g. peak memory per container today:
#   awk -F, 'NR>1{if($5>m[$2])m[$2]=$5} END{for(c in m)print c,m[c]}' \
#       ~/monitoring-logs/containers-$(date +%F).csv | sort -k2 -rn
#
set -uo pipefail
# cron runs with a minimal PATH — set one that finds docker/free/df.
export PATH=/usr/local/bin:/usr/bin:/bin

LOG_DIR="${MONITORING_LOG_DIR:-$HOME/monitoring-logs}"
DAY=$(date +%F)
TS=$(date '+%F %T')
mkdir -p "$LOG_DIR"

CONT="$LOG_DIR/containers-$DAY.csv"
HOST="$LOG_DIR/host-$DAY.csv"

[ -f "$CONT" ] || echo "ts,container,cpu_pct,mem_used_mib,mem_limit_mib,mem_pct" > "$CONT"
[ -f "$HOST" ] || echo "ts,mem_total_mib,mem_used_mib,mem_avail_mib,swap_used_mib,disk_root_used_pct,disk_root_avail_gib,load1" > "$HOST"

# Normalise a docker-stats size token ("9.92GiB", "512MiB", "0B") to MiB.
to_mib() {
  awk -v v="$1" 'BEGIN{
    n=v; sub(/[A-Za-z]+$/,"",n); n=n+0;
    u=v; sub(/^[0-9.]+/,"",u);
    if      (u=="GiB"||u=="GB") print n*1024;
    else if (u=="MiB"||u=="MB") print n;
    else if (u=="KiB"||u=="kB") print n/1024;
    else if (u=="B" ||u=="")    print n/1048576;
    else                        print n;
  }'
}

# --- per-container CPU + memory ---
docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}' 2>/dev/null | \
while IFS='|' read -r name cpu mem memp; do
  [ -z "$name" ] && continue
  used=${mem%% / *}        # "9.92GiB / 14GiB" -> "9.92GiB"
  lim=${mem##* / }         # "9.92GiB / 14GiB" -> "14GiB"
  echo "$TS,$name,${cpu%\%},$(to_mib "$used"),$(to_mib "$lim"),${memp%\%}" >> "$CONT"
done

# --- host memory / swap / disk / load ---
read -r mt mu ma < <(free -m | awk '/^Mem:/{print $2, $3, $7}')
sw=$(free -m | awk '/^Swap:/{print $3}')
read -r dpct dav < <(df -BG --output=pcent,avail / | awk 'NR==2{gsub(/%/,"",$1); gsub(/G/,"",$2); print $1, $2}')
l1=$(cut -d' ' -f1 /proc/loadavg)
echo "$TS,$mt,$mu,$ma,$sw,$dpct,$dav,$l1" >> "$HOST"

# --- retention: drop CSVs older than 30 days ---
find "$LOG_DIR" -name '*.csv' -mtime +30 -delete 2>/dev/null || true
