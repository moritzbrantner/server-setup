# server-setup

`server-setup` is an opinionated, idempotent bootstrap and validation tool for turning a fresh Debian/Ubuntu machine into a secured Dokploy host.

**Boundary:** `server-setup` manages the host. Dokploy manages applications, domains, TLS, deployments, replicas, logs, and rollbacks.

The new host-management path currently supports **Debian 12** and **Ubuntu 24.04**. The former custom application-deployment implementation is compatibility-only during the migration; its previous documentation is retained in [`LEGACY.md`](LEGACY.md).

## Fresh host

Clone/copy this repository onto a fresh supported host and run:

```bash
sudo ./setup.sh
```

The bootstrap installs only the Python prerequisite and the `server-setup` command, then launches the guided setup. The wizard writes the canonical configuration to:

```text
/etc/server-setup/config.toml
```

It shows the planned changes before mutation. Firewall activation, SSH hardening, Dokploy updates, unsupported-host states, and other risky situations are classified as **dangerous** and require explicit confirmation.

For automation with an already-prepared configuration:

```bash
sudo ./setup.sh --non-interactive --config /path/to/config.toml --yes
```

## Commands

```text
server-setup setup       interactive first setup / config editing
server-setup plan        read-only desired-vs-current diff
server-setup apply       reconcile the current configuration
server-setup validate    verify the desired host state
server-setup doctor      validation plus host capacity diagnostics
```

`plan`, `validate`, and `doctor` are read-only. `apply` requires root. Non-interactive apply requires `--yes`; changes classified as dangerous additionally require `--allow-dangerous`.

## Configuration

See [`config.example.toml`](config.example.toml). Version 1 manages:

- host timezone and base packages;
- unattended security upgrades;
- fail2ban;
- UFW with SSH/HTTP/HTTPS allowed before activation;
- optional SSH hardening with lockout checks;
- installation/update/validation of an exactly pinned Dokploy release.

A `false` security option means **do not manage that feature**; `server-setup` does not disable an already-active security facility merely because it is unselected. UFW is treated as a host baseline, not as proof that Docker-published ports are private; Dokploy/Traefik exposure rules and a provider firewall remain relevant for published container ports.

DNSControl and the legacy Uptime Kuma/Beszel composition remain represented in the v1 schema but are deliberately deferred until the optional-services cleanup. Enabling those sections currently produces an explicit validation failure instead of silently doing nothing.

Dokploy application configuration belongs in Dokploy or the application repository, not in `server-setup`.

## Idempotency contract

A healthy host must satisfy this invariant:

```text
apply
→ validate succeeds
→ plan reports no changes
→ apply again makes no host changes
```

This is exercised in unit tests and in real Debian/Ubuntu systemd sandboxes.

## Testing

See [`TESTING.md`](TESTING.md). The repository uses three layers:

1. deterministic unit/contract tests for planning and command generation;
2. privileged Debian 12 + Ubuntu 24.04 systemd containers for real apt/service integration and idempotency;
3. an opt-in **disposable real-VM smoke test** for the actual Dokploy installer, because Dokploy explicitly requires a real Linux host rather than a normal Docker container.

The real-host test can run on a disposable local VM/self-hosted GitHub runner, so cloud infrastructure is not required.

## Compatibility path

Existing hosts that still depend on the pre-Dokploy custom deployment engine can use:

```bash
sudo ./setup.sh --legacy <legacy options>
```

Known legacy flags are also forwarded automatically. The compatibility installer is frozen: do not add new application-platform behavior to it. Once existing hosts are migrated, that code is intended to be removed.
