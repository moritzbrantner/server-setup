# server-setup

`server-setup` now has one canonical setup flow for served repositories:

1. Prepare the server once.
2. Deploy each repository with `deploy-repo`.

Repositories that should be served must contain a valid root `server.conf`. Server-local state is generated into `deploy/registry.json`.

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

## Development

Development and testing notes live in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
