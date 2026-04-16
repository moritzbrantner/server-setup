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
sudo ./scripts/prepare-server.sh \
  --email ops@example.com \
  --skip-docker \
  --with-status-webapp
```

What it does:
- installs baseline packages and developer tools
- optionally applies SSH/UFW/fail2ban hardening
- installs the webhook receiver service
- stores `DEFAULT_TLS_EMAIL` in `/etc/default/site-automation`
- optionally installs the status webapp

The Next.js status webapp is the supported dashboard for this repository.

If you want to use its admin controls:
1. Set `STATUS_WEBAPP_ADMIN_TOKEN` in `/etc/default/server-setup-status-webapp`.
2. Restart `server-setup-status-webapp.service`.
3. Send the token in the `x-status-admin-token` header when calling admin APIs.

`--email` is required the first time you run it. Later runs can reuse the stored default.

## Deploy a repository

```bash
sudo ./scripts/deploy-repo.sh \
  --repo-url git@github.com:your-org/your-app.git
```

Optional:

```bash
sudo ./scripts/deploy-repo.sh \
  --repo-url git@github.com:your-org/your-app.git \
  --dest /srv/apps/your-app \
  --branch main \
  --email ops@example.com \
  --skip-github-hook
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

## Operate the stack

Show managed services:

```bash
./scripts/manage-services.sh
```

Restart one app service:

```bash
sudo ./scripts/manage-services.sh restart --app your-app
```

Stop managed services:

```bash
sudo ./scripts/shutdown-server.sh
```

Preview purge:

```bash
sudo ./scripts/shutdown-server.sh --purge --dry-run
```

## Legacy Migration

If you still have an older `deploy/sites.json` based installation, [`scripts/migrate_registry.py`](scripts/migrate_registry.py) can perform a one-time migration into `deploy/registry.json`. That migration path is for existing legacy installs only; new setups should use `prepare-server` and `deploy-repo` directly.

## Development

Development and testing notes live in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
