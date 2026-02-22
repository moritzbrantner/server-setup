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
- `--non-interactive`: fail instead of prompting if DNS preflight says records are not ready.

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

### 3) Sync and deploy websites from GitHub

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json
```

What it does:
- Reads site deployment config from JSON.
- Clones/updates each configured GitHub repo.
- Checks out the configured branch (defaults to `main`).
- Runs optional `build_cmd`, `deploy_script`, and `post_deploy_cmd`.
- Runs Unlighthouse after deployment to collect website metrics.

#### Configure website deployment entries

Copy the example file and edit values:

```bash
cp deploy/sites.example.json deploy/sites.json
```

Example entry:

```json
[
  {
    "name": "marketing-site",
    "repo": "git@github.com:your-org/marketing-site.git",
    "branch": "main",
    "workdir": "/srv/github-sites/marketing-site",
    "site_url": "https://example.com",
    "build_cmd": "bun install --frozen-lockfile && bun run build",
    "deploy_script": "scripts/deploy.sh",
    "post_deploy_cmd": "sudo systemctl reload nginx",
    "unlighthouse_cmd": "npx --yes unlighthouse-ci@latest --site https://example.com"
  }
]
```

Unlighthouse behavior:
- `site_url` enables the built-in Unlighthouse step (run after `post_deploy_cmd`).
- `unlighthouse_cmd` is optional and overrides the default command when set.
- Default report output path is `/var/log/unlighthouse/<site-name>/<timestamp>`.

> If your repo has a deploy script (for example `scripts/deploy.sh`), it can contain any server-side commands you want to run after pulling code.

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
