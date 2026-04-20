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
- shell linting with ShellCheck
- status webapp TypeScript typechecking
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

Run lint and static checks only:

```bash
./tests/run-lint.sh
```

Run the self-check wrapper:

```bash
python3 ./scripts/run_self_checks.py
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
python3 ./scripts/prepare_server.py --email admin@example.com --skip-docker
python3 ./scripts/deploy_repo.py --repo-url /srv/apps/simple-site --dest /srv/apps/simple-site --email admin@example.com --skip-github-hook
python3 ./scripts/manage_services.py
```

Optional pre-release integration check:

```bash
docker build -t server-setup-test .
TLM_DEUTSCHLAND_GITHUB_TOKEN=github_pat_... \
  IMAGE_NAME=server-setup-test \
  ./tests/test_docker_sandbox.sh
```

This check is intentionally outside `./tests/run-tests.sh` because it needs privileged Docker, systemd-in-container support, network access, and a GitHub token with access to the deployment test repository. If `TLM_DEUTSCHLAND_GITHUB_TOKEN` is not set, the script reports a skip instead of failing.

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
