# Testing server-setup

Server-management code cannot be validated convincingly by mocks alone. The test strategy therefore separates fast deterministic checks from real OS integration and from the genuinely destructive Dokploy installation path.

## Acceptance invariants

Every managed feature should be testable against the same lifecycle:

```text
inspect current state
→ plan is read-only and deterministic
→ apply performs only the plan
→ validate succeeds
→ second plan is empty
→ second apply is a no-op
```

Additional safety invariants:

- unsupported operating systems fail closed;
- non-root mutation fails closed;
- a dangerous plan cannot be applied accidentally;
- SSH hardening refuses a remote-session lockout when no authorized key is present;
- a fresh Dokploy install is blocked when 80/443/3000 are occupied;
- the standard Dokploy installer is blocked when an unrelated Docker Swarm is already active;
- exact Dokploy versions are pinned in configuration instead of `latest` or `canary`.

## Layer 1: unit and contract tests

Run:

```bash
./tests/run-python-tests.sh
./tests/run-lint.sh
```

These use deterministic fake host state to test:

- configuration parsing and round-tripping;
- module ownership boundaries;
- plan/apply behavior;
- exact commands and file content used by host/security modules;
- failure and lockout paths;
- Dokploy precondition detection;
- CLI safety and exit codes.

They are fast and should run on every PR. They prove decision logic, not that systemd/apt/Docker behave the way the fake says they do.

## Layer 2: real OS host sandboxes

Run locally when Docker is available:

```bash
./tests/host-sandbox/run.sh debian:12
./tests/host-sandbox/run.sh ubuntu:24.04
```

CI runs both variants on every PR. Each image boots real systemd in a privileged container and invokes the **real `setup.sh` bootstrap**, then exercises:

- real apt package installation;
- real filesystem writes;
- real systemd enable/start/restart behavior;
- unattended-upgrades;
- fail2ban;
- the installed `server-setup` entrypoint;
- `validate` after apply;
- an empty second `plan`;
- a safe second bootstrap/apply.

UFW is disabled in this layer so an infrastructure test cannot accidentally interfere with the CI runner's networking. Its command ordering and safety behavior remain covered deterministically in Layer 1.

Dokploy is also disabled in this layer. Its official installer expects a real Linux host, so pretending this is a full Dokploy E2E test would provide false confidence.

## Layer 3: disposable real-host smoke

The destructive smoke test is:

```bash
sudo SERVER_SETUP_DISPOSABLE_VM=1 ./tests/real-host/smoke.sh
```

It must run only on a throwaway Debian 12 or Ubuntu 24.04 VM. It performs the real bootstrap, installs the pinned Dokploy release, validates Docker Swarm/network/edge state, probes the fresh Dokploy admin endpoint, and checks that a second plan is empty.

There is also a manual GitHub Actions workflow, `Disposable real-host smoke`, targeting only a self-hosted runner labeled:

```text
server-setup-disposable-vm
```

This makes the highest-fidelity test possible without paying for a cloud service: attach a GitHub Actions runner to a disposable local VM, trigger the workflow, then discard/revert the VM.

### When Layer 3 is required

Run it before relying on changes that affect:

- `setup.sh` bootstrap behavior;
- the Dokploy installer/update flow;
- Docker/Swarm detection;
- host firewall/SSH behavior;
- supported OS versions;
- a tagged/released server-setup version.

For a new Dokploy pin, also reboot the disposable VM and run:

```bash
sudo server-setup validate
```

before considering the pin verified. Reboot behavior cannot be proven by the container sandbox.

## GitHub Actions pipeline

`.github/workflows/ci.yml` contains two required conceptual gates:

- **test** — the existing repository suite, Python unit/contract tests, lint, and frontend compatibility checks;
- **host-sandbox (Debian 12 / Ubuntu 24.04)** — real systemd/apt integration and idempotency.

The real-host workflow is manual and intentionally separate because it mutates an entire machine and installs Docker/Dokploy.

A PR should not be called host-validated if the required CI checks did not actually execute. An absent check is not equivalent to a passing check.
