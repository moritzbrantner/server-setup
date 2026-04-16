# Installing And Testing `server-setup`

This file covers development and verification of `server-setup` itself. For the user-facing setup flow, use [`README.md`](/home/moenarch/moritzbrantner/server-setup/README.md).

## Local install

```bash
git clone <your-server-setup-repo-url>
cd server-setup
```

## Run tests

Run the default validation suite:

```bash
./tests/run-tests.sh
```

That suite includes:
- shell tests for the deployment helpers and systemd assets
- Python unit tests for the deployment engine and contracts
- the `monitor/webapp` TypeScript test suite via `npm ci && npm test`

Run only Python tests:

```bash
./tests/run-python-tests.sh
```

Run only the status webapp tests:

```bash
./tests/test_status_webapp_frontend.sh
```

Run the self-check wrapper:

```bash
./scripts/run-self-checks.sh
```

## Docker sandbox

Build the sandbox image:

```bash
docker build -t server-setup-sandbox .
```

Start it:

```bash
docker run --privileged --cgroupns=host \
  --name server-setup-sandbox \
  -p 8080:80 \
  -p 8443:443 \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -d server-setup-sandbox
```

Open a shell:

```bash
docker exec -it server-setup-sandbox bash
cd /opt/server-setup
```

Useful sandbox commands:

```bash
./scripts/prepare-server.sh --email admin@example.com --skip-docker
./scripts/deploy-repo.sh --repo-url /srv/apps/simple-site --dest /srv/apps/simple-site --email admin@example.com --skip-github-hook
./scripts/manage-services.sh
```

## Examples

The example repositories under `examples/repositories/` use the current nested `server.conf` contract:
- `simple-site`
- `rest-api`
- `complex-site`
- `marketing-site`

## Canonical Development Notes

- The supported dashboard is `monitor/webapp`; the legacy Python dashboard has been retired.
- `deploy/registry.json` is generated host-local runtime state and should not be committed.
- `scripts/migrate_registry.py` exists only to migrate older `deploy/sites.json` installations.

## CI

GitHub Actions runs the same top-level validation path as local development:

```bash
./tests/run-tests.sh
```
