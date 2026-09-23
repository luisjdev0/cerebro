# VPS Deployment

Guide for the recommended deployment from plan_v2 §10: VPS with 4 vCPU / 8 GB RAM / 100 GB
SSD running Ubuntu 24.04 (works the same on Debian 12). The full stack is 3 containers
(Postgres+pgvector, `cerebro-memory-api` and `cerebro-docs-api`, ecosistema-cerebro.md §8)
and uses < 1.5 GB of RAM at rest.

## 1. Prepare the VPS (one time only)

```bash
# Docker + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# log out and back in for the group change to take effect

# Firewall: only SSH and HTTPS exposed
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

The Postgres (5432), `cerebro-memory-api` (8005), and `cerebro-docs-api` (8006) ports
are **not opened**: `compose.yaml` already binds them to `127.0.0.1` — only the reverse
proxy reaches them. These three are the default values; if the host already has
something occupying one of those ports (e.g. a shared server with its own Postgres on
5432), override them without touching `compose.yaml` via `POSTGRES_HOST_PORT`,
`CEREBRO_MEMORY_HOST_PORT`, `CEREBRO_DOCS_HOST_PORT` in that host's `.env` (see
`.env.example`) — adjust the reverse proxy below if you change any of them.

## 2. Clone the repo (it's private)

Simple option with a read-only deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cerebro_deploy -N ""
cat ~/.ssh/cerebro_deploy.pub
# paste that key into GitHub: cerebro repo → Settings → Deploy keys → Add (without write access)

git clone git@github.com:luisjdev0/cerebro.git -c core.sshCommand="ssh -i ~/.ssh/cerebro_deploy"
cd cerebro
git config core.sshCommand "ssh -i ~/.ssh/cerebro_deploy"
```

## 3. Configure secrets

```bash
cp .env.example .env
# Generate a strong root token and put it in .env (root for BOTH services — §6):
sed -i "s/^API_TOKEN=.*/API_TOKEN=$(openssl rand -hex 32)/" .env
grep API_TOKEN .env   # save it in your password manager
```

**Also change the Postgres password.** Unlike before, it is **no longer** edited in
`compose.yaml`: it's defined once as `POSTGRES_PASSWORD` in `.env`, and `compose.yaml`
interpolates it in the three places that need it (the `postgres` service itself and the
`DATABASE_URL` injected into each API within the compose network — commit "Despliegue
VPS: password de Postgres via .env y puerto host 8005"):

```bash
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 32)/" .env
```

If you omit `POSTGRES_PASSWORD` in `.env`, `docker compose` fails to start with an
explicit error (`define POSTGRES_PASSWORD in .env`) instead of starting with an
insecure default.

Note: if you already initialized the volume with the old password, changing it in
`.env` does not change it in the database — do it before the first startup, or use
`ALTER USER` in psql.

## 4. Start the stack

```bash
docker compose --profile full build   # downloads the embeddings model during the build (~5 min the first time)
docker compose --profile full up -d
docker compose ps                     # all three "healthy" (postgres, cerebro-memory-api, cerebro-docs-api)
curl -s localhost:8005/health         # {"status":"ok"}  (cerebro-memory-api)
curl -s localhost:8006/health         # {"status":"ok"}  (cerebro-docs-api)
```

`docker compose up -d` (without `--profile full`) still only starts `postgres` — no
change to the day-to-day flow if you run the APIs outside Docker.

## 5. HTTPS with Caddy (reverse proxy)

**Recommended: a single subdomain, via the internal gateway.** `compose.yaml` already
includes a `gateway` container (Caddy, `full` profile, see `gateway/Caddyfile`) that
routes `/memory`, `/docs`, and `/flows` by prefix to each internal API and exposes
everything on a single port (`CEREBRO_GATEWAY_HOST_PORT`, default `8080`) -- so your
VPS's Caddy only needs one block, no matter how many modules the ecosystem has:

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8080
}
EOF
sudo systemctl reload caddy
curl -s https://cerebro.luisjdev.com/memory/health
curl -s https://cerebro.luisjdev.com/docs/health
curl -s https://cerebro.luisjdev.com/flows/health
```

With this, `CEREBRO_MEMORY_URL=https://cerebro.luisjdev.com/memory` (same pattern for
`_DOCS_`/`_FLOWS_`) — `MemoryClient`/`DocsClient`/`FlowsClient` need no code changes,
since they already build the final URL by concatenating `base_url` + relative path.

<details>
<summary>Alternative: one subdomain per service (without the gateway)</summary>

If you prefer to keep each service on its own subdomain (as it was before the
gateway existed), point each one directly to its host port:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8005
}

docs-cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8006
}

flows-cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8007
}
EOF
sudo systemctl reload caddy
```

In that case there's no need to run the `gateway` container at all.
</details>

> **No domain?** Private alternative: install [Tailscale](https://tailscale.com) on
> the VPS and on your machines; the gateway becomes accessible only within your
> tailnet via `http://<ip-tailscale>:8080` without exposing anything to the internet
> (in that case bind the gateway port to the tailscale IP or use `tailscale serve`).

## 6. Update an existing VPS to this version (monorepo + schemas)

**Only applies if your VPS is running a version prior to the monorepo** (a single
`api` container on the `public` schema). If this is a new installation, skip this
section.

Starting `cerebro-memory-api` applies the `005_schema_cerebro_memory.sql` migration,
which moves all of its tables (`contexts`, `memories`, `audit_log`, `disambiguation_log`,
`context_preferences`, `memory_edges`, `api_tokens`, `schema_migrations`) from `public`
to its own `cerebro_memory` schema (`ALTER TABLE ... SET SCHEMA`, not a copy). Also, the
compose service was renamed from `api` to `cerebro-memory-api`. Follow this order:

**a) Mandatory full backup, before touching anything:**

```bash
cd ~/cerebro
mkdir -p ~/cerebro-backups
docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos \
  | gzip > ~/cerebro-backups/pre-upgrade-$(date +%Y%m%d-%H%M%S).sql.gz
```

Do not continue if this command fails or produces an empty file.

**b) `git pull` and start with `--remove-orphans`:**

The service rename (`api` → `cerebro-memory-api`) means Docker Compose no longer
recognizes the old `api` container as part of the stack — it becomes orphaned, running
without `docker compose up` touching it, unless you explicitly tell it to:

```bash
git pull
docker compose --profile full build
docker compose --profile full up -d --remove-orphans
```

This is safe: the old `api` container is stateless (the data lives in the Postgres
volume, not in the container), so removing it loses nothing.

**c) Post-startup verification:**

```bash
docker compose ps                     # postgres, cerebro-memory-api, cerebro-docs-api: "healthy"; no old "api"
curl -s localhost:8005/health         # {"status":"ok"}
curl -s localhost:8006/health         # {"status":"ok"}

# Row counts moved to the new schema — should match what you had before
# the upgrade (compare them against the backup if you have doubts):
docker compose exec -T postgres psql -U knowledgeos -d knowledgeos -c "
  SELECT 'memories' AS tabla, count(*) FROM cerebro_memory.memories
  UNION ALL SELECT 'contexts', count(*) FROM cerebro_memory.contexts
  UNION ALL SELECT 'memory_edges', count(*) FROM cerebro_memory.memory_edges
  UNION ALL SELECT 'api_tokens', count(*) FROM cerebro_memory.api_tokens;
"
```

If something fails or the counts don't match, restore from the backup in step (a)
before continuing to use the system.

## 7. Tokens (don't use root for day-to-day work)

From your local machine (the CLI talks to the remote APIs):

```bash
set CEREBRO_MEMORY_URL=https://cerebro.luisjdev.com/memory
set CEREBRO_DOCS_URL=https://cerebro.luisjdev.com/docs
set CEREBRO_FLOWS_URL=https://cerebro.luisjdev.com/flows
set CEREBRO_TOKEN=<your root token, the API_TOKEN from .env>

# Token SCOPED to a single service (cerebro-memory):
cerebro memory token create claude-desktop --scopes read,write

# CROSS-SERVICE token: a single secret (prefix cbr_), registered across the services
# that support it in the same operation (ecosistema-cerebro.md SS13):
cerebro token create automatizacion-x --scopes read --contexts infraestructura
```

(URLs above: with the gateway from step 5. If you deployed with separate subdomains
instead, use those -- see the alternative in that same step.)

`cerebro token create` (cross-service) prints the secret **only once**; use it as
`CEREBRO_TOKEN`. `cerebro memory token create`, on the other hand, generates a token
valid only for cerebro-memory. Both are revocable: `cerebro token revoke <name>`
(cross-service) or `cerebro memory token revoke <name>` (memory only).

If `cerebro token create` fails on one service and succeeds on the other (partial
failure), the CLI reports it explicitly per service and exits with an error; retry
the same command — it safely reuses the same secret (it doesn't duplicate the
registration).

**Compatibility**: `KNOWLEDGEOS_API_URL`/`KNOWLEDGEOS_API_TOKEN` are still supported
as legacy variables, for cerebro-memory only, if some old script still uses them.

## 8. Connect your agents (local MCP → remote APIs)

The MCP server runs on YOUR machine (stdio) and talks to the VPS. It's a single
binary (`cerebro-mcp`, package `cerebro-mcp`) that exposes the 36 tools from the three
services (`memory_*`, `docs_*`, `flow_*`). In `claude_desktop_config.json`:

```json
"cerebro": {
  "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\cerebro-mcp.exe",
  "env": {
    "CEREBRO_MEMORY_URL": "https://cerebro.luisjdev.com/memory",
    "CEREBRO_DOCS_URL": "https://cerebro.luisjdev.com/docs",
    "CEREBRO_FLOWS_URL": "https://cerebro.luisjdev.com/flows",
    "CEREBRO_TOKEN": "<cross-service token for the agent, not the root one>",
    "CEREBRO_AGENT_NAME": "claude-desktop"
  }
}
```

None of the three URLs falls back to a useful value for a remote VPS (at most they
fall back to the local development default) — always include all three explicitly.

## 9. Automatic backups (plan_v2 §9 / ecosistema-cerebro.md §9: an untested restore isn't a backup)

A single shared Postgres instance means that one `pg_dump` of the instance covers
**both** schemas (`cerebro_memory` and `cerebro_docs`) completely in a single
operation — no special per-service handling is needed.

```bash
mkdir -p ~/cerebro/backups
crontab -e
```

Add (daily backup at 03:15, keeps 14 days; the name no longer includes "knowledgeos"
— it's a backup of the full ecosystem):

```
15 3 * * * cd ~/cerebro && docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos | gzip > backups/cerebro-$(date +\%Y\%m\%d).sql.gz && find backups -name '*.sql.gz' -mtime +14 -delete
```

Copy the backups OFF the VPS (rclone to a bucket/Drive, or a scheduled `scp` from
your machine). Test the restore at least once:

```bash
gunzip -c backups/cerebro-XXXXXXXX.sql.gz | docker compose exec -T postgres psql -U knowledgeos -d knowledgeos_restore_test
```

**Local alternative**: `cerebro backup` (with no arguments) does the same thing via
the CLI, but writes **outside the repo tree** (`../cerebro-backups/`, a sibling of
`cerebro/`) with `0600` permissions on the file — designed to avoid it accidentally
getting committed or being readable by other users on the system (cerebro-docs
documents don't filter content, so a dump could carry secrets pasted in by mistake).
`cerebro restore <file>` performs the reverse restore, with an interactive confirmation
unless `--yes` is passed.

## 10. Update to a new version

```bash
cd ~/cerebro
git pull
docker compose --profile full build
docker compose --profile full up -d --remove-orphans   # migrations apply automatically on startup
```

It's safe to always leave `--remove-orphans` in: it only acts if a `git pull`
brought in a service rename or removal in `compose.yaml` (as happened once, §6);
otherwise it does nothing.

## Final security checklist

- [ ] Random root `API_TOKEN`, kept out of the repo (only in the VPS's `.env`) — it's
      the root for **both** services
- [ ] `POSTGRES_PASSWORD` changed in `.env` (not in `compose.yaml`)
- [ ] `ufw` active; 5432/8005/8006 NOT exposed (verify: `ss -tlnp | grep -E '5432|8005|8006'` should show only 127.0.0.1)
- [ ] HTTPS working on both subdomains (or Tailscale)
- [ ] Agents using scoped tokens (cross-service or scoped to a single service), not root
- [ ] Backup cron active and a restore tested
