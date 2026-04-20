# Contributing

## Development Setup

Use the repository scripts directly from a checkout:

```bash
git clone <server-setup-repo-url>
cd server-setup
```

Install system tools used by the default checks:

```bash
sudo apt-get update
sudo apt-get install -y jq shellcheck python3
```

The status webapp tests install their Node dependencies with `npm ci` under `monitor/webapp`.

## Validation

Run the full default suite before opening a change:

```bash
./tests/run-tests.sh
```

Useful narrower checks:

```bash
./tests/run-lint.sh
./tests/run-python-tests.sh
./tests/test_status_webapp_frontend.sh
```

The Docker sandbox integration check is optional and documented in `INSTALLING-AND-TESTING.md`.

## Change Guidelines

- Keep deployment behavior idempotent and conservative.
- Prefer existing scripts and helpers over new entrypoints.
- Add or update tests for deployment, registry, webhook, service, nginx, and dashboard behavior changes.
- Do not commit generated host state such as `deploy/registry.json`, `/var/lib/server-setup/state`, `monitor/webapp/.next`, or `node_modules`.
