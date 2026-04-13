# server-setup

This repo now has five user-facing scripts:

0. Prepare a fresh server once
1. Deploy a repository from GitHub and wire up redeploy hooks
2. Put a running service behind a domain and Let's Encrypt
3. Shut the stack down again
4. Inspect or manage the generated systemd services

## 0. Prepare the server

```bash
sudo ./scripts/prepare-server.sh
```

What it does:
- installs the baseline packages and developer tools
- applies SSH/UFW/fail2ban hardening
- installs the deploy watcher, webhook receiver, and fallback timer

Optional:

```bash
sudo ./scripts/prepare-server.sh --skip-docker --with-status-webapp
```

## 1. Deploy a repository

```bash
sudo ./scripts/deploy-repo.sh \
  --repo-url git@github.com:your-org/your-app.git \
  --dest /srv/apps/your-app
```

What it does:
- clones or updates the repository
- reads `server.conf` from the repo root
- registers the app in `deploy/sites.json`
- creates or updates the systemd service and nginx config
- deploys the app
- configures the local GitHub webhook receiver
- tries to create the GitHub webhook automatically when `gh auth login` is already set up

If automatic GitHub webhook creation is not possible, the script prints the exact `Payload URL` and `Secret` to use in GitHub.

`deploy-repo.sh` expects the repository to contain a `server.conf`. Minimal examples:

Static site:

```json
{
  "name": "simple-site",
  "domain": "simple.example.com",
  "build_output": "public"
}
```

Service:

```json
{
  "name": "api",
  "domain": "api.example.com",
  "build": "npm ci && npm run build",
  "command": "PORT=4001 npm run start",
  "port": 4001,
  "health_endpoint": "/health"
}
```

## 2. Put a running service behind a domain and TLS

For an app already listening on a local port:

```bash
sudo ./scripts/setup-domain.sh \
  --domain app.example.com \
  --port 4001 \
  --email ops@example.com
```

For a static directory:

```bash
sudo ./scripts/setup-domain.sh \
  --domain www.example.com \
  --root /var/www/example.com/public \
  --email ops@example.com \
  --www
```

What it does:
- writes the nginx site
- checks that DNS for the domain points at this server
- requests and installs the Let's Encrypt certificate

## 3. Shut things down

Stop the managed apps, nginx, and the deploy automation:

```bash
sudo ./scripts/shutdown-server.sh
```

Preview only:

```bash
sudo ./scripts/shutdown-server.sh --dry-run
```

Also delete generated units, nginx configs, state files, and env files:

```bash
sudo ./scripts/shutdown-server.sh --purge
```

## 4. Inspect or manage services

Show all managed units, whether they currently exist, whether they are active, and which app owns them:

```bash
./scripts/manage-services.sh
```

Restart one app service:

```bash
sudo ./scripts/manage-services.sh restart --app your-app
```

Filter to a specific unit:

```bash
./scripts/manage-services.sh --service site-apps-watcher.service
```

## Notes

- Run step 0 once per server.
- Run step 1 for repo-managed apps that ship a `server.conf`.
- Run step 2 for anything already running locally that just needs nginx and TLS.
- Advanced internals and testing notes remain in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
