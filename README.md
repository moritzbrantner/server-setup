# server-setup

Scripts for managing a Hetzner Ubuntu LTS server running multiple websites/services.

Additional guide: [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md)

## Status webapp

A small Next.js dashboard is available under [`monitor/webapp`](monitor/webapp) for a live view of:
- host load, disk, memory, and core services (`nginx`, `docker`)
- setup health for TLS, deploy automation, and hardening (`certbot.timer`, webhook/watcher/timer, `ufw`, `fail2ban`, SSH hardening)
- deployed applications from `deploy/sites.json`
- per-app HTTP reachability, deploy-state JSON, last failure context, and systemd service status

Run it locally from the repo root:

```bash
cd monitor/webapp
npm install
npm run dev
```

By default the app reads:
- `deploy/sites.json` when present, otherwise `monitor/websites.json`
- deploy state from `/var/lib/server-setup/state`

Optional environment overrides:
- `SERVER_SETUP_ROOT=/path/to/server-setup`
- `STATUS_CONFIG_PATH=deploy/sites.json`
- `STATUS_STATE_DIR=/var/lib/server-setup/state`

Install it as a persistent systemd service on a server:

```bash
sudo ./scripts/setup-status-webapp.sh
```

That script installs Node.js if needed, builds the app, and enables
`server-setup-status-webapp.service` so the monitor stays up on port `4000`.
The dashboard is intentionally public-safe: it reports summarized setup state without exposing
secrets, raw firewall dumps, or log tails.

## Scripts

## Quick start / one-command init

Use the canonical bootstrap script to prepare tools, install Nginx site config, and configure TLS in one command:

```bash
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --www
```

Common options:
- `--port`: reverse proxy to a local app on `127.0.0.1:<port>` instead of serving a static directory.
- `--skip-certbot`: install tools + Nginx only (skip TLS for now).
- `--skip-docker`: skip Docker installation/enable validation step.
- `--skip-hardening`: skip host hardening (`sshd`, `ufw`, `fail2ban`, unattended upgrades).
- `--non-interactive`: fail instead of prompting if DNS preflight says records are not ready.
- `--skip-automation`: skip installing/enabling automated deploy triggers (watcher/webhook/timer).


Hardening behavior during bootstrap:
- By default, `scripts/init-server.sh` runs `scripts/harden-server.sh` immediately after tool installation.
- Hardening is idempotent and applies: SSH daemon defaults, unattended security updates, fail2ban for SSH, and UFW default-deny incoming with explicit allow rules for SSH/HTTP/HTTPS.
- `--skip-hardening` is available if you want to defer firewall/SSH changes for a maintenance window.

Docker behavior during bootstrap:
- By default, `scripts/init-server.sh` calls `scripts/ensure-server-tools.sh`, which installs Docker Engine in a distro-aware way and runs `systemctl enable --now docker`.
- Debian/Ubuntu use Docker's official apt repository (`download.docker.com`) before installing `docker-ce` + related packages.
- Other supported package managers (`dnf`, `yum`, `apk`, `pacman`, `zypper`) install Docker from distro packages.
- Post-install checks verify both `command -v docker` and `systemctl is-active docker`.

Examples:

```bash
# HTTP-only bootstrap, run certbot later
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --skip-certbot

# Reverse proxy a Next.js app already listening on port 3000
sudo ./scripts/init-server.sh \
  --domain example.com \
  --port 3000 \
  --email admin@example.com \
  --www

# Run fully non-interactive (safe for automation)
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --www \
  --non-interactive
```

### 0) Harden server baseline (SSH/UFW/fail2ban/auto-updates)

```bash
sudo ./scripts/harden-server.sh
```

What it does:
- Writes `/etc/ssh/sshd_config.d/99-server-setup-hardening.conf` with hardened defaults, including `PasswordAuthentication no`, then validates config with `sshd -t` before restart.
- Enables unattended upgrades via apt periodic config.
- Installs/configures fail2ban with an active `sshd` jail.
- Enforces UFW defaults (`deny incoming`, `allow outgoing`) and explicit allow rules for `OpenSSH`, `80/tcp`, `443/tcp`.

Preconditions (to prevent remote lockout):
- Ensure you already have working SSH key-based access to the server before disabling password auth.
- Keep your current SSH session open while applying hardening and test a second login before disconnecting.
- If you are remote and not ready to change SSH/firewall policy yet, run `init-server.sh --skip-hardening` and apply hardening later.

Recovery steps if SSH access is interrupted:
1. Use your hosting provider's console/KVM/rescue mode to get shell access.
2. Re-enable temporary password auth or relax SSH settings by editing `/etc/ssh/sshd_config.d/99-server-setup-hardening.conf`.
3. Validate and restart SSH:
   ```bash
   sudo sshd -t && sudo systemctl restart ssh
   ```
4. Confirm UFW rules include SSH access (adjust CIDR/IP if needed):
   ```bash
   sudo ufw status verbose
   sudo ufw allow OpenSSH
   sudo ufw reload
   ```

### 1) Install Nginx site config for a domain

```bash
sudo ./scripts/install-nginx-site.sh \
  --domain example.com \
  --root /var/www/example.com/public \
  --www-redirect
```

What it does:
- Installs Nginx
- Creates the web root when `--root` is used
- Or reverse proxies to `127.0.0.1:<port>` when `--port` is used
- Writes `/etc/nginx/sites-available/example.com.conf`
- Enables the site and reloads Nginx
- Opens `Nginx Full` in UFW (if active)

Example for an app server:

```bash
sudo ./scripts/install-nginx-site.sh \
  --domain example.com \
  --port 3000 \
  --www-redirect
```

### 2) Configure Let's Encrypt certificate (Certbot)

```bash
sudo ./scripts/setup-letsencrypt.sh \
  --domain example.com \
  --email admin@example.com \
  --www
```

What it does:
- Installs Certbot + Nginx plugin
- Requests a certificate with HTTP->HTTPS redirect
- Enables renewal timer
- Runs `certbot renew --dry-run`

### 3) Discover, sync, and deploy websites from GitHub

Preferred workflow (automatic discovery + deploy):

```bash
./scripts/sync-github-sites.sh --discover-base '/srv/apps/*' --config deploy/sites.json
```

This runs discovery first (`scripts/discover-sites.sh`), validates each app `server.conf`, writes a normalized `deploy/sites.json`, then deploys.

What it does:
- Scans each repo under `--discover-base` for `server.conf`.
- Validates required keys and fails with explicit errors for missing/invalid data.
- Validates uniqueness for site names and domains.
- Normalizes each entry to deploy shape (`name`, `repo`, `branch`, release paths, hooks, service metadata, nginx metadata).
- Clones each site into a timestamped release directory (for example `<releases_dir>/20260214-120102`).
- Checks out the configured branch and runs optional `pre_deploy_cmd`, `build_cmd`, and `deploy_script` in the release.
- Captures the current release pointer, atomically switches `current_symlink` to the new release on success, then runs `post_deploy_cmd`.
- Runs Unlighthouse after deployment to collect website metrics.

#### Stable `server.conf` format (JSON)

Every discovered repository must include `server.conf` at the repo root. The shortest useful forms are:

```json
{
  "name": "simple-site",
  "domain": "simple.localhost",
  "build_output": "public"
}
```

```json
{
  "name": "marketing-site",
  "domain": "example.com",
  "build_output": "dist",
  "build": "npm ci && npm run build",
  "command": "PORT=4003 npm run start",
  "port": 4003,
  "user": "www-data",
  "env_file": "/etc/default/marketing-site",
  "health_endpoint": "/healthz",
  "post_deploy": "sudo systemctl reload nginx",
  "www_redirect": true,
  "tls_hostnames": ["example.com", "www.example.com"]
}
```

Validation rules:
- Required keys: `name`, `domain`, and one of `web_root` or `build_output`.
- `repo` and `branch` are auto-detected from Git when omitted.
- `runtime.mode` defaults to `static`; if `command` or `port` is present, it defaults to `service`.
- `service.name` defaults to `<name>.service`.
- `nginx.tls_hostnames` defaults to `[domain]`.
- `web_root` or `build_output` must be present (either one is acceptable).
- `name` and `domain` must be globally unique across all discovered repos.
- Optional `repo_auth.github_token` lets you keep the clone token separate from `repo`; GitHub HTTPS and `git@github.com:` URLs are both supported.
- Optional `nginx.www_redirect` must be boolean; optional `nginx.tls_hostnames` must be an array of non-empty hostnames.
- The expanded nested shape is still supported for advanced cases:

```json
{
  "deploy_hooks": {
    "pre_deploy": "echo pre",
    "build": "npm ci && npm run build",
    "post_deploy": "sudo systemctl reload nginx"
  },
  "runtime": {
    "mode": "service",
    "command": "PORT=4003 npm run start",
    "working_dir": ".",
    "user": "www-data",
    "env_file": "/etc/default/marketing-site",
    "port": 4003,
    "health_endpoint": "/healthz"
  },
  "service": {
    "name": "marketing-site.service",
    "reload_cmd": "sudo systemctl reload nginx"
  },
  "nginx": {
    "www_redirect": true,
    "tls_hostnames": ["example.com", "www.example.com"]
  }
}
```

Runtime modes:
- `static`: static site mode. No app process is managed; Nginx serves release assets directly.
- `service`: long-running application mode (Node/Python/etc.) behind Nginx reverse proxy. Requires `runtime.command` and `runtime.port`.

Nginx rendering behavior:
- For every deployed site, the deployer templates `/etc/nginx/sites-available/<site>.conf` and ensures a symlink in `/etc/nginx/sites-enabled/<site>.conf`.
- `static` mode renders `root` + `try_files` serving from the new release path (`build_output` preferred, otherwise `web_root`).
- `service` mode renders reverse-proxy rules to `http://127.0.0.1:<runtime.port>`.
- `nginx.www_redirect: true` adds an additional `www.<domain>` redirect server block to the apex hostname.
- `nginx.tls_hostnames` controls `server_name` hostnames (useful for TLS certificate host coverage).
- Before reload, deploy runs `nginx -t`; if validation fails, it restores the last-known-good site config and aborts deployment for that site with clear logs.

Service mode deployment behavior:
- The deployer renders the runtime unit at `/etc/systemd/system/<service.name>` and treats `service.name` as the canonical systemd unit name.
- `systemctl daemon-reload`, `enable`, and `restart` are only run when the unit file content changes.
- Rollout is rollback-safe: traffic (`current` symlink) is not switched until the app passes its runtime health check (`http://127.0.0.1:<port><health_endpoint>`).
- If health check fails, the new release is discarded and the previous release remains active.

#### End-to-end example repository with `server.conf`

See `examples/repositories/marketing-site/server.conf` and companion deploy hook at `examples/repositories/marketing-site/scripts/deploy.sh`.

Example local run:

```bash
./scripts/discover-sites.sh --base-glob './examples/repositories/*' --output deploy/sites.json
./scripts/sync-github-sites.sh --config deploy/sites.json --site marketing-site
```


### Built-in self-checks (tests + benchmark)

`server-setup` now ships with its own `server.conf`, test suite, and benchmark runner so you can dogfood deployment automation by cloning this repo into your apps directory.

Clone into the first server-setup checkout's apps folder:

```bash
cd /path/to/first/server-setup
git clone <your-server-setup-repo-url> apps/server-setup
./scripts/sync-github-sites.sh --discover-base './apps/*' --config deploy/sites.json --site server-setup-self
```

What runs for the cloned `apps/server-setup` repo:
- `scripts/run-self-checks.sh`
- `tests/run-tests.sh` (integration tests for discovery/autodetection behavior)
- `benchmarks/discover-sites-benchmark.sh` (uses `hyperfine` if installed, otherwise `/usr/bin/time`)

You can also execute checks directly:

```bash
./scripts/run-self-checks.sh
```

Docker sandbox:

```bash
docker build -t server-setup-sandbox .
docker run --privileged --cgroupns=host \
  --name server-setup-sandbox \
  -p 8080:80 -p 8443:443 \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -d server-setup-sandbox
docker exec -it server-setup-sandbox bash
```

Inside the container, the repository is available at `/opt/server-setup`. This path auto-seeds three runnable example repos into `/srv/apps` on startup:
- `/srv/apps/simple-site`
- `/srv/apps/rest-api`
- `/srv/apps/complex-site`

Raw `docker run` is useful for host-style script experimentation. For the full example stack, prefer Docker Compose because it also starts the attached Postgres database.

Docker Compose sandbox with Postgres:

```bash
docker compose up -d --build
docker compose exec server-setup bash
```

Published ports:
- SSH: `localhost:22` -> container `22`
- HTTP: `localhost:80` -> container `80`
- HTTPS: `localhost:443` -> container `443`
- Status webapp: `localhost:4000` -> container `4000`
- Example REST API passthrough: `localhost:4001` -> container `4001`
- Example complex app passthrough: `localhost:4002` -> container `4002`
- Example simple-site passthrough: `localhost:4003` -> container `4003`
- Postgres: `localhost:55432` -> container `5432`

Included applications after `docker compose up -d --build`:
- Monitor: open `http://127.0.0.1:4000/`
- REST API: open `http://127.0.0.1:4001/healthz` or POST to `http://127.0.0.1:4001/api/items`
- Complex site: open `http://127.0.0.1:4002/`
- Simple static site: open `http://127.0.0.1:4003/`

Inside the container, nginx also serves host-based routes:
- `monitor.localhost` -> monitor webapp
- `api.localhost` -> REST API
- `app.localhost` -> complex site
- `simple.localhost` -> simple static site

Why `55432`:
- Host port `5432` is commonly already used by a local Postgres instance.
- The sandbox still uses the normal in-network Postgres port `5432` at hostname `test-db`.

Inside the sandbox container, the attached database is reachable at `test-db:5432` with:
- database: `server_setup`
- user: `server_setup`
- password: `server_setup`
- URL: `postgres://server_setup:server_setup@test-db:5432/server_setup`

Compose defaults for the examples:
- `SKIP_UNLIGHTHOUSE=1` so local HTTP-only example deploys are fast and do not require extra audit tooling.
- `SKIP_EXAMPLE_SEED=0` so the three sandbox repos are created automatically under `/srv/apps`.

End-to-end example tryout:

```bash
docker compose up -d --build
docker compose exec server-setup bash
cd /opt/server-setup
ls -1 /srv/apps
./scripts/discover-sites.sh --base-glob '/srv/apps/*' --output deploy/sites.json
./scripts/sync-github-sites.sh --config deploy/sites.json
curl -H 'Host: simple.localhost' http://127.0.0.1/
curl -H 'Host: api.localhost' http://127.0.0.1/healthz
curl -H 'Host: app.localhost' http://127.0.0.1/
curl -H 'Host: api.localhost' http://127.0.0.1/api/items
curl -H 'Host: api.localhost' \
  -H 'Content-Type: application/json' \
  -d '{"title":"Created from the compose sandbox"}' \
  http://127.0.0.1/api/items
psql "$TEST_DATABASE_URL" -c 'select count(*) from demo_items;'
```

Host-side smoke checks:

```bash
curl http://127.0.0.1:4000/
curl http://127.0.0.1:4003/
curl -H 'Host: simple.localhost' http://127.0.0.1/
curl -H 'Host: api.localhost' http://127.0.0.1/healthz
curl -H 'Host: app.localhost' http://127.0.0.1/
curl http://127.0.0.1:4001/healthz
curl http://127.0.0.1:4002/
```

Reseeding example repos:

```bash
./scripts/seed-example-repositories.sh --target-dir /srv/apps --force
```

Stop the sandbox:

```bash
docker compose down
```


### 4) Onboard a single app repository end-to-end

```bash
sudo ./scripts/onboard-app.sh \
  --repo-url git@github.com:your-org/marketing-site.git \
  --dest /srv/apps/marketing-site \
  --email admin@example.com
```

What it does:
- Clones (or updates) the repository in `--dest`.
- Validates `server.conf` and registers/updates the app in `deploy/sites.json` without duplicating entries.
- Deploys the app via `scripts/sync-github-sites.sh` (runtime + Nginx config generation included).
- Acquires/updates TLS certs via `scripts/setup-letsencrypt.sh` (unless `--skip-tls`).
- Prints a post-run summary with domain, service status, active release path, DNS state, and required manual actions.

#### Optional manual JSON flow

You can still manage `deploy/sites.json` directly if needed:

```bash
cp deploy/sites.example.json deploy/sites.json
cp .env.example .env
set -a; source .env; set +a
./scripts/sync-github-sites.sh --config deploy/sites.json
```

Use `${ENV_VAR}` placeholders in `deploy/sites.json` for secret values. The sync script resolves placeholders at runtime and fails with a clear error if a referenced variable is missing or empty.

For private GitHub repos, prefer:

```json
{
  "repo": "https://github.com/your-org/your-repo.git",
  "repo_auth": {
    "github_token": "${YOUR_REPO_GITHUB_TOKEN}",
    "github_username": "x-access-token"
  }
}
```

That keeps the secret out of the repo URL in the tracked JSON while still allowing the deployer to clone over HTTPS.

Secret values you must set when using the tracked configs in this repo:
- `TLM_DEUTSCHLAND_GITHUB_TOKEN`: required by [deploy/sites.json](/home/moenarch/moritzbrantner/server-setup/deploy/sites.json) under `repo_auth.github_token` to clone `tlm-deutschland` over HTTPS.
- `UNLIGHTHOUSE_SERVER_TOKEN`: optional client token used by `deploy/sites.json` when uploading reports to an Unlighthouse server.
- `UNLIGHTHOUSE_AUTH_TOKEN`: optional server-side token for [docker-compose.additional-services.yml](/home/moenarch/moritzbrantner/server-setup/docker-compose.additional-services.yml); if you use the bundled Unlighthouse server, set it to the same value as `UNLIGHTHOUSE_SERVER_TOKEN`.

Release settings:
- `releases_dir` (optional): where timestamped releases are created. Defaults to `<workdir>/releases`.
- `current_symlink` (optional): symlink switched to the latest successful release. Defaults to `<workdir>/current`.
- `keep_releases` (optional): how many release directories to retain (default `5`). Cleanup preserves the active and rollback-target releases when possible.

Minimal secure default for local/server usage:
- Create a root-owned `.env` file (`chmod 600 .env`) and keep it out of version control.
- Export variables only for the deploy command (`set -a; source .env; set +a`).
- Store deploy SSH keys outside the repo (for example `/srv/keys/<site>-deploy`) and reference them via env vars such as `MARKETING_SITE_DEPLOY_KEY_PATH`.

Unlighthouse behavior:
- `site_url` enables the built-in Unlighthouse step (run after `post_deploy_cmd`).
- `unlighthouse_cmd` is optional and overrides the default command when set.
- `unlighthouse_server_url` (or `UNLIGHTHOUSE_SERVER_URL`) uploads each run to your Unlighthouse server.
- `unlighthouse_server_token` (or `UNLIGHTHOUSE_SERVER_TOKEN`) configures auth for the upload target when required.
- When upload is configured, the site `name` is sent as the Unlighthouse build name.
- Default report output path is `/var/log/unlighthouse/<site-name>/<timestamp>`.

> If your repo has a deploy script (for example `scripts/deploy.sh`), it can contain any server-side commands you want to run after pulling code.

#### Deployment runbook (operations)

Deploy one site:

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json --site marketing-site
```

Deploy all configured sites:

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json
```

Verify active release on server:

```bash
readlink -f /srv/github-sites/marketing-site/current
ls -1 /srv/github-sites/marketing-site/releases
```

Rollback quickly to previous release:

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json --rollback marketing-site
```

Cleanup behavior:
- Cleanup runs automatically after successful deploys.
- The script removes old release directories beyond `keep_releases` while attempting to keep both the active release and the rollback target (`.previous_release`).
- Rollback metadata is stored at `<releases_dir>/.previous_release` and updated on every successful switch and rollback.


### 4) Automated triggers for discovery + deploy (systemd)

Bootstrap now installs and enables three automation mechanisms by default on first setup (unless `--skip-automation` is used):

- **Option A (watcher):** `site-apps-watcher.service` runs an inotify watcher on `/srv/apps` and triggers deploys on file change events.
- **Option B (webhook):** `site-webhook-receiver.service` exposes a lightweight GitHub push webhook listener and triggers deploys on valid `push` events.
- **Option C (fallback timer):** `site-discovery-deploy.timer` triggers periodic runs even if no watcher/webhook events fire.

All triggers call the same `site-discovery-deploy.service`, which executes `scripts/run-discovery-deploy.sh`. That runner uses a `flock` lock file (`/var/lock/site-discovery-deploy.lock`) so overlapping events are serialized and never run concurrent deployments.

Systemd unit files are in `ops/systemd/` and copied to `/etc/systemd/system/` by `scripts/init-server.sh`.

Create or edit `/etc/default/site-automation` (template: `ops/systemd/site-automation.env.example`) to customize:

- `REPO_ROOT`
- `APPS_DIR` / `APPS_GLOB`
- `DEBOUNCE_SECONDS`
- `WEBHOOK_HOST`, `WEBHOOK_PORT`, `WEBHOOK_PATH`, `WEBHOOK_SECRET`
- `WEBHOOK_ALLOWED_REPOS`, `WEBHOOK_ALLOWED_BRANCHES`

Manual install (if needed):

```bash
sudo install -m 0644 ops/systemd/site-*.service /etc/systemd/system/
sudo install -m 0644 ops/systemd/site-discovery-deploy.timer /etc/systemd/system/
sudo install -m 0644 ops/systemd/site-automation.env.example /etc/default/site-automation
sudo systemctl daemon-reload
sudo systemctl enable --now site-apps-watcher.service
sudo systemctl enable --now site-webhook-receiver.service
sudo systemctl enable --now site-discovery-deploy.timer
```

#### GitHub webhook setup for push-triggered deploys

1. Put each app into `deploy/sites.json` using either the normalized fields or the short form above (`build`, `command`, `port`, `www_redirect`, and optional `repo_auth.github_token`).
2. On the server, set the matching token and webhook secret in `/etc/default/site-automation`, for example:

```bash
TLM_DEUTSCHLAND_GITHUB_TOKEN=github_pat_xxx
WEBHOOK_SECRET=replace-with-a-random-secret
WEBHOOK_ALLOWED_REPOS=moritzbrantner/tlm-deutschland
WEBHOOK_ALLOWED_BRANCHES=main
```

3. Reload the webhook service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart site-webhook-receiver.service
sudo systemctl restart site-discovery-deploy.timer
```

4. In GitHub, open the repository settings for `tlm-deutschland` or any later repo:
   - `Settings` -> `Webhooks` -> `Add webhook`
   - `Payload URL`: `http://YOUR_SERVER_IP:9001/github/push`
   - `Content type`: `application/json`
   - `Secret`: same value as `WEBHOOK_SECRET`
   - `Which events?`: `Just the push event`
   - Save and use `Recent Deliveries` to confirm GitHub gets `202 Accepted`

5. Open the firewall / reverse proxy path if needed so GitHub can reach port `9001`, or proxy `/github/push` from Nginx to the receiver.

When a push hits the configured branch, `site-webhook-receiver.service` starts `site-discovery-deploy.service`, which pulls the latest commit, runs `build_cmd`, rewrites the systemd unit if needed, restarts the app, health-checks it, and only then switches traffic.

#### Trigger deploy from GitHub Actions on push to `main`

In each website repository, add `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Hetzner

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy over SSH
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /path/to/server-setup
            ./scripts/sync-github-sites.sh --config deploy/sites.json --site marketing-site
```

This gives you push-to-main deployment: every push to `main` triggers the workflow, the server pulls the latest code for that site, and optional scripts run automatically.

#### Production secret backends (recommended)

For production, avoid long-lived plaintext files whenever possible. Good options:
- **systemd `EnvironmentFile`** (simple server default): store vars in `/etc/server-setup/deploy.env` with `0600` permissions and load them from a service unit that runs sync jobs.
- **1Password CLI**: fetch secret values at runtime (`op read ...`) and export them immediately before running `sync-github-sites.sh`.
- **Cloud secret managers**: AWS Secrets Manager, GCP Secret Manager, or Azure Key Vault in your deploy runner.

If you need a practical baseline today, use `EnvironmentFile` + restricted file permissions + dedicated deploy keys per repo.


## Additional services (Docker Compose)

A compose file is included for optional supporting services:

```bash
docker compose -f docker-compose.additional-services.yml up -d
```

Current services:
- `unlighthouse-server`: receives uploaded Unlighthouse reports and persists data in the `unlighthouse_data` Docker volume.

Set these environment variables before running your deploy sync script if you want all sites to upload by default:

```bash
set -a; source .env; set +a
```

## Monitoring dashboard service

A lightweight Python dashboard is included so you can port-forward one service in VS Code and see an overview of:
- live website status + latency,
- 24h uptime/latency analytics,
- server metrics (load, memory, disk),
- `nginx` and `docker` service status.

### Configure websites

Edit `monitor/websites.json`:

```json
[
  {"name": "Main site", "url": "https://example.com", "timeout": 5},
  {"name": "API", "url": "https://example.com/health", "timeout": 5}
]
```

### Run

```bash
python3 monitor/dashboard.py --port 8085
```

Then forward port `8085` in VS Code and open the forwarded URL.

Optional flags:

```bash
python3 monitor/dashboard.py \
  --config monitor/websites.json \
  --db monitor/history.db \
  --interval 30 \
  --host 0.0.0.0 \
  --port 8085
```

## Notes

- Point DNS A/AAAA records to your Hetzner server before running Let's Encrypt.
- Ensure ports `80` and `443` are reachable.
- Bootstrap scripts should run as `root`/`sudo` on a `systemd`-based server if you want Docker service auto-enable checks to pass.
