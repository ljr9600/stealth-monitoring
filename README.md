# monitoring

> Auto-generated stub README. Add a service description, build/run instructions, and other notes here.


## External dependencies

> Per the [rule in `~/dev/docs/CODING_STANDARDS.md`](../../docs/CODING_STANDARDS.md#-every-service-must-document-its-external-resource-locations), every service must document where its external resources live. This section was auto-generated from a code scan on 2026-05-23 and curated.

### Host bind-mounts (`docker-compose.yml`)

| Host path | Container path | Flag | Purpose |
|---|---|---|---|
| `/var/run` | `/var/run` | `ro` |  |
| `/sys` | `/sys` | `ro` |  |
| `/var/lib/docker/` | `/var/lib/docker` | `ro` |  |
| `/dev/disk/` | `/dev/disk` | `ro` |  |
| `/etc/localtime` | `/etc/localtime` | `ro` | Timezone consistency (read-only) |
| `/etc/localtime` | `/etc/localtime` | `ro` | Timezone consistency (read-only) |
| `/etc/localtime` | `/etc/localtime` | `ro` | Timezone consistency (read-only) |
| `/etc/localtime` | `/etc/localtime` | `ro` | Timezone consistency (read-only) |

### External HTTP APIs (third-party)

- **github.com** — 1 reference(s) in code
- **grafana.com** — 1 reference(s) in code

### Environment variables relevant to external resources

- `HOST`
- `MONITORING_LOG_DIR`

### Backup story

- **TODO** — fill in: which of the above resources is critical, what backs each one up, how to recover. (Auto-generated section; please complete with service-specific detail.)

### Per-host differences (`.30` dev vs `.50` prod)

- **TODO** — fill in: are the bind-mount paths / DB hosts / external endpoints the SAME on both hosts? If different, document why and how.
