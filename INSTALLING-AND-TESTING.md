# Installing And Testing Repositories

This guide covers two related workflows:

1. Install `server-setup` itself so you can run its scripts and self-checks.
2. Install an application repository that contains a compatible `server.conf` and deploy it with this project.

## Install `server-setup`

Clone the repository:

```bash
git clone <your-server-setup-repo-url>
cd server-setup
```

If you want to bootstrap a fresh Ubuntu server with Nginx, optional Docker, TLS, and automation helpers, run:

```bash
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --www
```

Useful variants:

```bash
# Skip TLS for now
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --skip-certbot

# Reverse proxy a local app server such as Next.js
sudo ./scripts/init-server.sh \
  --domain example.com \
  --port 3000 \
  --email admin@example.com \
  --skip-certbot

# Avoid Docker installation during bootstrap
sudo ./scripts/init-server.sh \
  --domain example.com \
  --web-root /var/www/example.com/public \
  --email admin@example.com \
  --skip-docker
```

To configure just the monitoring dashboard as a persistent service on port `4000`:

```bash
sudo ./scripts/setup-status-webapp.sh
```

The dashboard reports public-safe setup health summaries for TLS, deploy automation, host hardening,
and runtime services so you can spot server-side setup problems quickly without exposing raw secrets
or command output.

## Install An App Repository

Each app repository must contain a valid `server.conf` at the repository root. The easiest way to install and register one app is:

```bash
sudo ./scripts/onboard-app.sh \
  --repo-url git@github.com:your-org/marketing-site.git \
  --dest /srv/apps/marketing-site \
  --email admin@example.com
```

What this does:

- Clones or updates the repository in `--dest`.
- Validates `server.conf`.
- Registers or updates the site in `deploy/sites.json`.
- Deploys the site through `scripts/sync-github-sites.sh`.
- Requests or renews TLS unless `--skip-tls` is supplied.

Useful variants:

```bash
# Skip TLS
sudo ./scripts/onboard-app.sh \
  --repo-url git@github.com:your-org/marketing-site.git \
  --dest /srv/apps/marketing-site \
  --skip-tls

# Force a specific branch before validation and deploy
sudo ./scripts/onboard-app.sh \
  --repo-url git@github.com:your-org/marketing-site.git \
  --dest /srv/apps/marketing-site \
  --email admin@example.com \
  --branch main
```

## Install Multiple Repositories By Discovery

If you already have several repositories checked out under one base directory, discover and normalize them into `deploy/sites.json`:

```bash
./scripts/discover-sites.sh \
  --base-glob '/srv/apps/*' \
  --output deploy/sites.json
```

Then deploy all discovered sites:

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json
```

Deploy only one site:

```bash
./scripts/sync-github-sites.sh --config deploy/sites.json --site marketing-site
```

Discover and deploy in one step:

```bash
./scripts/sync-github-sites.sh \
  --discover-base '/srv/apps/*' \
  --config deploy/sites.json
```

## Test `server-setup` Locally

Run the built-in self-checks:

```bash
./scripts/run-self-checks.sh
```

This executes:

- `tests/run-tests.sh`
- `benchmarks/discover-sites-benchmark.sh`

Run only the integration tests:

```bash
./tests/run-tests.sh
```

Run only the benchmark:

```bash
./benchmarks/discover-sites-benchmark.sh
```

## Try It With Docker

The Docker image is a sandbox host, not a test runner. It boots `systemd` so you can exercise the installation scripts inside a disposable Ubuntu container.

Build the sandbox image:

```bash
docker build -t server-setup-sandbox .
```

Start the container:

```bash
docker run --privileged --cgroupns=host \
  --name server-setup-sandbox \
  -p 8080:80 \
  -p 8443:443 \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -d server-setup-sandbox
```

Open a shell inside it:

```bash
docker exec -it server-setup-sandbox bash
cd /opt/server-setup
```

Useful sandbox commands:

```bash
# Try the one-command bootstrap without Docker or public TLS
./scripts/init-server.sh \
  --domain example.test \
  --web-root /var/www/example.test/public \
  --email admin@example.com \
  --skip-docker \
  --skip-certbot

# Or just exercise the Nginx setup directly
./scripts/install-nginx-site.sh \
  --domain example.test \
  --root /var/www/example.test/public \
  --www-redirect

# Or proxy a local app already listening on port 3000
./scripts/install-nginx-site.sh \
  --domain example.test \
  --port 3000 \
  --www-redirect
```

Notes:

- `setup-letsencrypt.sh` only works if DNS for the chosen domain points to the container and port 80 is reachable.
- Running `init-server.sh` without `--skip-docker` inside the container is usually not useful unless you specifically want Docker-in-Docker.
- The repository is installed at `/opt/server-setup`.
- The sandbox entrypoint auto-seeds example repos into `/srv/apps`.

### Docker Compose Sandbox With Postgres

Use the included [`compose.yml`](/home/moenarch/moritzbrantner/server-setup/compose.yml) to start the sandbox server and a disposable Postgres database together:

```bash
docker compose up -d --build
docker compose exec server-setup bash
cd /opt/server-setup
```

Published host ports:

- `22` for SSH
- `80` for HTTP
- `443` for HTTPS
- `4000` for the status webapp
- `4001` for the example REST API service
- `4002` for the example complex web app service
- `4003` for the example simple static web app
- `55432` for Postgres

Included applications after `docker compose up -d --build`:

- Monitor: `http://127.0.0.1:4000/`
- REST API: `http://127.0.0.1:4001/healthz` and `http://127.0.0.1:4001/api/items`
- Complex site: `http://127.0.0.1:4002/`
- Simple static site: `http://127.0.0.1:4003/`

Inside the container, nginx also serves:

- `monitor.localhost`
- `api.localhost`
- `app.localhost`
- `simple.localhost`

Database connection details inside the sandbox:

```bash
echo "$TEST_DATABASE_URL"
psql "postgres://server_setup:server_setup@test-db:5432/server_setup"
```

What compose adds on top of the raw sandbox:

- Postgres 16 attached as `test-db:5432`
- `SKIP_UNLIGHTHOUSE=1` so local HTTP example deploys do not try to run the Unlighthouse step
- auto-seeded example repos under `/srv/apps`

Seeded example repos:

- `/srv/apps/simple-site`
- `/srv/apps/rest-api`
- `/srv/apps/complex-site`

End-to-end tryout:

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

Reseed the example repos if you want to restore them to template state:

```bash
./scripts/seed-example-repositories.sh --target-dir /srv/apps --force
```

The compose setup waits for Postgres health before starting the sandbox container, and the database is reachable from your host at `localhost:55432`. The `55432` mapping avoids clashing with a local Postgres already using host port `5432`.

## Minimal `server.conf` Checklist

For discovery and deployment to succeed, each app repository should provide:

- `name`
- `domain`
- `deploy_hooks`
- `runtime.mode`
- `service.name`
- Either `web_root` or `build_output`

For runnable sandbox examples, see:

- `examples/repositories/simple-site/server.conf`
- `examples/repositories/rest-api/server.conf`
- `examples/repositories/complex-site/server.conf`

For the broader reference example, see `examples/repositories/marketing-site/server.conf`.
