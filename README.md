# server-setup

`server-setup` supports one deployment workflow for served repositories:

1. Prepare the server once.
2. Deploy each repository with `deploy-repo`.

Repositories that should be served must contain a valid root `server.conf`.

Generated state lives on the target host:
- `deploy/registry.json` stores the local deployment registry for that host
- `/var/lib/server-setup/state/*.json` stores per-site deploy/runtime status

The committed [`deploy/registry.example.json`](deploy/registry.example.json) file is only a shape example.

## Prepare the server

```bash
sudo python3 ./scripts/prepare_server.py \
  --email ops@example.com \
  --skip-docker \
  --with-status-webapp
```

What it does:
- installs baseline packages and developer tools
- installs, enables, and starts `nginx`
- optionally applies unattended-upgrades/UFW/fail2ban hardening
- installs the webhook receiver service
- stores `DEFAULT_TLS_EMAIL` in `/etc/default/site-automation`
- optionally installs the status webapp

`prepare_server.py` leaves SSH untouched by default. If you explicitly want it to manage `sshd`, add `--with-ssh-hardening`.

The Next.js status webapp is the supported dashboard for this repository.
By default, `prepare_server.py --with-status-webapp` installs a systemd service on port `4000` and an nginx proxy at `http://monitor.localhost/` when that name resolves to the server.
For a different dashboard host, run `scripts/setup_status_webapp.py --server-name status.example.com`; use `--skip-nginx` only when another reverse proxy owns the route.

If you want to use its admin controls:
1. Set `STATUS_WEBAPP_ADMIN_TOKEN` in `/etc/default/server-setup-status-webapp`.
2. Restart `server-setup-status-webapp.service`.
3. Send the token in the `x-status-admin-token` header when calling admin APIs.

`--email` is required the first time you run it. Later runs can reuse the stored default.

## Deploy a repository

```bash
sudo python3 ./scripts/deploy_repo.py \
  --repo-url git@github.com:your-org/your-app.git
```

Optional:

```bash
sudo python3 ./scripts/deploy_repo.py \
  --repo-url git@github.com:your-org/your-app.git \
  --dest /srv/apps/your-app \
  --branch main \
  --email ops@example.com \
  --skip-github-hook \
  --skip-example-dotfiles
```

What it does:
- clones or updates the checkout under `/srv/apps/<repo-name>` by default
- validates `server.conf`
- upserts `deploy/registry.json`
- runs the repo deploy hooks
- creates or updates the app systemd service
- writes or updates the nginx site
- verifies DNS and requests/renews Let’s Encrypt
- configures the webhook receiver and, when possible, the GitHub webhook

Use `--skip-example-dotfiles` for repositories whose `server.conf` and deploy hooks manage production runtime configuration directly. Without it, noninteractive deploys stop when a checked-out repository contains missing `*.example` dotfile targets that would normally be filled in through prompts.

If automatic GitHub webhook creation is not possible, the script prints the exact payload URL and secret to configure manually.

## Repair a website

Use `repair_site.py` when a managed checkout is incomplete, a repo-owned `server.conf` has changed, or a previous deploy left the site in a failed state:

```bash
sudo python3 ./scripts/repair_site.py --site your-app
```

Preview the exact actions first:

```bash
python3 ./scripts/repair_site.py --site your-app --dry-run
```

Repair is designed to be idempotent and conservative:
- it selects one site from `deploy/registry.json`
- it aborts before deploy reset if the checkout has tracked local modifications
- it clones or updates the configured checkout and branch
- it creates a missing root `server.conf` only when the registry already has enough `deploy_config` metadata
- it never overwrites an existing `server.conf`
- it refreshes the registry from the repo-owned `server.conf`
- it blocks if required repo config files such as `.env` are still missing
- it redeploys through the same deploy engine used by `deploy_repo.py`

The authenticated status webapp exposes the same operation as **Repair site** on each managed application.

## Deployment State

`deploy-repo` and the webhook receiver write deploy state into `/var/lib/server-setup/state/<site>.json`.

Each state file includes:
- `last_deploy_status`
- `last_deploy_timestamp`
- `current_release`
- `checkout_path`
- `last_attempted_release`
- `previous_successful_release`
- `rollback_status`
- `rollback_reason`

Failed deploys also record `last_failure_reason` and `last_failure_at`.
Successful deploys record `last_success_at` and clear stale failure and rollback metadata. When a deploy fails after service or nginx configuration changes have started, the deploy engine attempts to restore the previous app service unit and nginx site config before recording the failure.

## `server.conf`

Minimal static site:

```json
{
  "name": "simple-site",
  "domain": "simple.example.com",
  "build_output": "public"
}
```

Minimal long-running service:

```json
{
  "name": "api",
  "domain": "api.example.com",
  "build_output": ".",
  "deploy_hooks": {
    "build": "npm ci && npm run build"
  },
  "runtime": {
    "mode": "service",
    "command": "PORT=4001 npm run start",
    "port": 4001
  }
}
```

Supported top-level keys:
- `name`
- `domain`
- `build_output`
- `web_root`
- `deploy_hooks`
- `runtime`
- `service`
- `nginx`
- `dns`

Legacy top-level shorthand like `build`, `command`, `port`, `www_redirect`, and infrastructure keys like `repo`, `branch`, or `workdir` are rejected.

Optional DNS provider configuration enables domain management in the authenticated status webapp:

```json
{
  "name": "api",
  "domain": "api.example.com",
  "build_output": ".",
  "dns": {
    "provider": "porkbun",
    "zone": "example.com"
  }
}
```

Supported `dns.provider` values are `porkbun` and `namecheap`. Credentials stay out of `server.conf`; set them in the status webapp environment file (`/etc/default/server-setup-status-webapp`) and restart `server-setup-status-webapp.service`.
Namecheap writes replace the full host list for the zone, so preview changes with `scripts/manage_dns_records.py ... --dry-run` before mutating production records.

Porkbun:

```bash
PORKBUN_API_KEY=...
PORKBUN_SECRET_API_KEY=...
```

Namecheap:

```bash
NAMECHEAP_API_USER=...
NAMECHEAP_API_KEY=...
NAMECHEAP_USERNAME=...       # optional, defaults to NAMECHEAP_API_USER
NAMECHEAP_CLIENT_IP=...      # required by Namecheap API access
NAMECHEAP_SANDBOX=false      # optional
```

## Operational environment

The deploy registry is `deploy/registry.json`. It is host-local runtime state and should stay uncommitted; the committed `deploy/registry.example.json` is only an example.

Status webapp knobs in `/etc/default/server-setup-status-webapp`:
- `STATUS_CONFIG_PATH`: overrides the dashboard config source. Set it to the active `deploy/registry.json` for admin deploy actions.
- `STATUS_STATE_DIR`: overrides the deploy-state directory read by the dashboard.
- `STATUS_WEBAPP_GITHUB_TOKEN`: token used by status webapp-launched GitHub commands.
- DNS variables: `PORKBUN_API_KEY`, `PORKBUN_SECRET_API_KEY`, `NAMECHEAP_API_USER`, `NAMECHEAP_API_KEY`, `NAMECHEAP_USERNAME`, `NAMECHEAP_CLIENT_IP`, and `NAMECHEAP_SANDBOX`.

Automation knobs in `/etc/default/site-automation`:
- `REGISTRY_PATH`: deploy registry used by webhook redeploys.
- `STATE_DIR`: deploy-state directory written by automation.
- `SITE_AUTOMATION_GITHUB_TOKEN`: token used by automation-launched GitHub commands.

## Operate the stack

Show managed services:

```bash
python3 ./scripts/manage_services.py
```

Restart one app service:

```bash
sudo python3 ./scripts/manage_services.py restart --app your-app
```

Manage repository secrets from the terminal:

```bash
python3 ./scripts/manage_github_secrets.py list --site your-app
python3 ./scripts/manage_github_secrets.py set MY_SECRET --site your-app --value "super-secret"
python3 ./scripts/manage_github_secrets.py delete MY_SECRET --site your-app
```

The script scans `.github/workflows/*.yml` and `.github/workflows/*.yaml` in the checked-out repository for `secrets.*` references, then stores values in the repo-local env file. If the repo has a root `.env.example`, the managed target is the matching `.env`; otherwise the script falls back to the runtime env file or `./.env`.

Stop managed services:

```bash
sudo python3 ./scripts/shutdown_server.py
```

Preview purge:

```bash
sudo python3 ./scripts/shutdown_server.py --purge --dry-run
```

## Troubleshooting

Webhook does not trigger:
- confirm `site-webhook-receiver.service` is active with `systemctl status site-webhook-receiver.service`
- confirm `/etc/default/site-automation` contains `WEBHOOK_SECRET`, `WEBHOOK_ALLOWED_REPOS`, `WEBHOOK_ALLOWED_BRANCHES`, `REGISTRY_PATH`, and `DEFAULT_TLS_EMAIL`
- confirm the GitHub webhook payload URL matches `WEBHOOK_PATH` and the public host/port that reaches the receiver
- check `/var/log/server-setup/webhook-*.log` for rejected signatures, unmatched repos, or unmatched branches

Webhook triggers but old code is served:
- check `/var/log/server-setup/webhook-*.log` for `checkout refresh` and `registry refresh` events
- confirm `deploy/registry.json` points at the expected `checkout_path`, `branch`, and `webhook_repo`
- run `sudo python3 ./scripts/repair_site.py --site your-app` to refresh the checkout, registry metadata, automation env, and deploy state through the same deploy engine

TLS setup fails:
- confirm `DEFAULT_TLS_EMAIL` is set in `/etc/default/site-automation`
- confirm the domain resolves to this server before retrying
- run `sudo certbot certificates` and inspect `/var/log/letsencrypt/letsencrypt.log`
- rerun repair after DNS and certificate issues are resolved

DNS verification fails:
- confirm the domain's A/AAAA records point at the server's public IP
- if using managed DNS, confirm `dns.provider` and `dns.zone` in `server.conf`, then set the matching provider credentials in `/etc/default/server-setup-status-webapp`
- restart `server-setup-status-webapp.service` after changing DNS provider credentials

Status webapp admin token is missing:
- set `STATUS_WEBAPP_ADMIN_TOKEN` in `/etc/default/server-setup-status-webapp`
- restart with `sudo systemctl restart server-setup-status-webapp.service`
- use the same token in the dashboard token field or send it as the `x-status-admin-token` API header

## Legacy Migration

If you still have an older `deploy/sites.json` based installation, [`scripts/migrate_registry.py`](scripts/migrate_registry.py) can perform a one-time migration into `deploy/registry.json`. That migration path is for existing legacy installs only; new setups should use `prepare-server` and `deploy-repo` directly.

## Development

Development and testing notes live in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
