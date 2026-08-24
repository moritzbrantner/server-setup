# Installing And Testing `server-setup`

This file covers development and verification of `server-setup` itself. For the user-facing installation and architecture, use [`README.md`](README.md).

## Local checkout

```bash
git clone <your-server-setup-repo-url>
cd server-setup
```

## Run tests

Run the default validation suite:

```bash
./tests/run-tests.sh
```

Equivalent Make target:

```bash
make test
```

The suite includes:

- ShellCheck and Python linting,
- `setup.sh` syntax/help validation,
- Docker Compose model validation for `services/compose.yml` and the optional Dokploy/Traefik overlay,
- shell and Python tests for the retained legacy deployment helpers,
- status webapp typechecking and tests for the retained legacy dashboard.

Run only the connected-services validation:

```bash
bash ./tests/test_service_stack.sh
```

Run only Python tests:

```bash
./tests/run-python-tests.sh
```

Run only the legacy status webapp tests:

```bash
./tests/test_status_webapp_frontend.sh
```

Run lint/static checks only:

```bash
./tests/run-lint.sh
```

## Service-stack validation

`tests/test_service_stack.sh` does not install Dokploy or start production containers. It validates the Compose models and checks the installer contract without mutating the machine.

The canonical host setup itself is intentionally tested on a disposable Ubuntu/Debian server because Dokploy's installation manages Docker Swarm and binds ports 80, 443, and 3000.

Before a real-host integration test, review the safety conditions documented in the README:

- do not run the upstream Dokploy installer over an unrelated active Swarm,
- ports 80/443/3000 must be available,
- `--replace-legacy` is an explicit traffic cut-over and does not migrate applications,
- Docker-published ports require Docker-aware or provider-level firewalling.

## Legacy Docker sandbox

The root `Dockerfile` and root `compose.yml` exercise the retained Python/systemd/nginx implementation. They are a compatibility sandbox, not the production service architecture.

Build the legacy sandbox image:

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

Optional compatibility integration check:

```bash
docker build -t server-setup-test .
IMAGE_NAME=server-setup-test ./tests/test_docker_sandbox.sh
```

This test remains outside the default suite because it needs privileged Docker and systemd-in-container support.

## Release checklist

Before merging or tagging a release:

1. Run `make lint`.
2. Run `make test`.
3. Confirm the service-stack Compose validation passes.
4. Confirm `git status --short` contains only intentional tracked changes.
5. If legacy compatibility changed, run the optional privileged Docker sandbox check.
6. Confirm no generated host state, secrets, `.env` files, `node_modules`, `.next`, or `tsconfig.tsbuildinfo` files are staged.

## Architecture boundary

New functionality should go to the service that owns it rather than extending the retained deployment engine:

- Dokploy: deploy/build/runtime/domain/TLS/webhook/log/rollback functionality,
- Uptime Kuma: uptime/status functionality,
- Beszel: host/container telemetry,
- DNSControl: DNS-provider integration,
- `harden_server.py`: small host-native baseline only.

The older `server.conf`, registry, webhook receiver, nginx installer, and custom status webapp are compatibility/migration surfaces.

## CI

GitHub Actions runs the same top-level validation path as local development:

```bash
./tests/run-tests.sh
```
