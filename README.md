# server-setup

`server-setup` supports one deployment workflow for served repositories:

1. Prepare the server once.
2. Deploy each repository with `deploy-repo`.

Repositories that should be served must contain a valid root `server.conf`.

Generated state lives on the target host:
- `deploy/registry.json` stores the local deployment registry for that host
- `/var/lib/server-setup/state/*.json` stores per-site deploy/runtime status

The committed [`deploy/registry.example.json`](deploy/registry.example.json) file is only a shape example.

## Supported deployment model

`server-setup` is first-class for Ubuntu/Debian single-server hosting with:
- static sites
- SPAs served by `nginx`
- single-process Node.js or Bun services behind `nginx` and `systemd`
- single-process Python services behind `nginx` and `systemd`

This repository assumes one server running `nginx`, optional per-app `systemd` services, Let’s Encrypt, and optional GitHub push redeploys.

## Out of scope

`server-setup` does not natively manage:
- PHP-FPM, WordPress, or Laravel stacks
- multi-container app stacks as the main deploy model
- wildcard certificates or DNS-provider integrations for certificate issuance

## Prepare the server

```bash
sudo python3 ./scripts/prepare_server.py \
  --email ops@example.com \
  --skip-docker \
  --with-status-webapp
```

What it does:
- installs baseline packages and developer tools
- installs Node.js 22.x LTS, `npm`, `npx`, and enables `corepack` for `pnpm`/`yarn`
- installs Python packaging tools (`python3-pip`, `python3-venv`)
- installs, enables, and starts `nginx`
- optionally applies unattended-upgrades/UFW/fail2ban hardening
- installs the webhook receiver service
- stores `DEFAULT_TLS_EMAIL` in `/etc/default/site-automation`
- optionally installs the status webapp

Fresh host capabilities after `prepare_server.py`:
- `nginx`
- `certbot`
- `git`
- `gh`
- Bun
- Node.js + `npm` + `corepack` (`pnpm`/`yarn`)
- Python venv and pip tooling
- third-party Namecheap DNS CLI (`namecheap`)
- PostgreSQL
- Docker, unless `--skip-docker` is supplied

`prepare_server.py` leaves SSH untouched by default. If you explicitly want it to manage `sshd`, add `--with-ssh-hardening`.

The Next.js status webapp is the supported dashboard for this repository.

If you want to use its admin controls:
1. Set `STATUS_WEBAPP_ADMIN_TOKEN` in `/etc/default/server-setup-status-webapp`.
2. To manage Porkbun DNS from the dashboard, also set `PORKBUN_API_KEY` and `PORKBUN_SECRET_API_KEY`.
3. Restart `server-setup-status-webapp.service`.
4. Send the token in the `x-status-admin-token` header when calling admin APIs.

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
  --skip-github-hook
```

What it does:
- clones or updates the checkout under `/srv/apps/<repo-name>` by default
- validates `server.conf`
- rejects registry conflicts before deploy when another site already uses the same domain, service name, or runtime port
- upserts `deploy/registry.json`
- runs the repo deploy hooks
- creates or updates the app systemd service
- writes or updates the nginx site
- verifies DNS and requests/renews Let’s Encrypt
- configures the webhook receiver and, when possible, the GitHub webhook

If automatic GitHub webhook creation is not possible, the script prints the exact payload URL and secret to configure manually.

## Deployment State

`deploy-repo` and the webhook receiver write deploy state into `/var/lib/server-setup/state/<site>.json`.

Each state file includes:
- `last_deploy_status`
- `last_deploy_timestamp`
- `current_release`
- `checkout_path`
- `last_attempted_release`

Failed deploys also record `last_failure_reason` and `last_failure_at`.
Successful deploys record `last_success_at` and clear stale failure metadata.

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

Canonical Python service:

```json
{
  "name": "python-api",
  "domain": "api.example.com",
  "build_output": ".",
  "deploy_hooks": {
    "build": "python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  },
  "runtime": {
    "mode": "service",
    "command": "PORT=4001 ./.venv/bin/python app.py",
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

Legacy top-level shorthand like `build`, `command`, `port`, `www_redirect`, and infrastructure keys like `repo`, `branch`, or `workdir` are rejected.

`nginx.tls_hostnames` is intentionally narrow for this project scope:
- the primary domain is always included
- the only optional extra hostname is `www.<domain>`
- any other alias hostname is rejected during validation
- if `nginx.www_redirect` is `true`, `www.<domain>` is automatically added for TLS

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

Manage Porkbun DNS records from the terminal:

```bash
export PORKBUN_API_KEY=pk_...
export PORKBUN_SECRET_API_KEY=sk_...

python3 ./scripts/manage_porkbun_dns.py domains
python3 ./scripts/manage_porkbun_dns.py list example.com
python3 ./scripts/manage_porkbun_dns.py create example.com --type A --name www --content 203.0.113.10
python3 ./scripts/manage_porkbun_dns.py edit example.com 123456 --type A --name www --content 203.0.113.20
python3 ./scripts/manage_porkbun_dns.py delete example.com 123456
```

The status webapp exposes the same list/create/edit/delete flow after admin unlock. Porkbun API access must be enabled for the domains you want to manage.

Manage Namecheap DNS records from the terminal with the installed third-party `namecheap` CLI:

```bash
export NAMECHEAP_API_KEY="your-api-key"
export NAMECHEAP_API_USER="your-api-user"
export NAMECHEAP_USERNAME="your-username"
export NAMECHEAP_CLIENT_IP="your-whitelisted-ip"

namecheap domains
namecheap records example.com
namecheap add -d example.com -t A -h www -v 203.0.113.10 -l 3600
namecheap update -d example.com -t A -h www -v 203.0.113.20 -l 3600
namecheap delete -d example.com -t A -h www
```

Namecheap API access must be enabled in the account, and the client IP must be whitelisted in Namecheap API settings.

Stop managed services:

```bash
sudo python3 ./scripts/shutdown_server.py
```

Preview purge:

```bash
sudo python3 ./scripts/shutdown_server.py --purge --dry-run
```

## Legacy Migration

If you still have an older `deploy/sites.json` based installation, [`scripts/migrate_registry.py`](scripts/migrate_registry.py) can perform a one-time migration into `deploy/registry.json`. That migration path is for existing legacy installs only; new setups should use `prepare-server` and `deploy-repo` directly.

## Development

Development and testing notes live in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
