# server-setup

Scripts for managing a Hetzner Ubuntu LTS server running multiple websites/services.

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
- Creates the web root
- Writes `/etc/nginx/sites-available/example.com.conf`
- Enables the site and reloads Nginx
- Opens `Nginx Full` in UFW (if active)

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

Every discovered repository must include `server.conf` at the repo root with this schema:

```json
{
  "name": "marketing-site",
  "repo": "git@github.com:your-org/marketing-site.git",
  "branch": "main",
  "domain": "example.com",
  "workdir": "/srv/github-sites/marketing-site",
  "web_root": "public",
  "build_output": "dist",
  "deploy_hooks": {
    "pre_deploy": "echo pre",
    "build": "npm ci && npm run build",
    "post_deploy": "sudo systemctl reload nginx"
  },
  "runtime": {
    "mode": "service",
    "command": "npm run start",
    "working_dir": ".",
    "user": "www-data",
    "env_file": "/etc/default/marketing-site",
    "port": 3000,
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

Validation rules:
- Required keys: `name`, `domain`, `deploy_hooks`, `runtime`, `service` (`repo` and `branch` are auto-detected from Git when omitted).
- `web_root` or `build_output` must be present (either one is acceptable).
- Required nested keys: `runtime.mode`, `service.name`.
- `name` and `domain` must be globally unique across all discovered repos.
- Optional `nginx.www_redirect` must be boolean; optional `nginx.tls_hostnames` must be an array of non-empty hostnames.

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
- The deployer deterministically renders `/etc/systemd/system/app-<name>.service` from `runtime` fields.
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
