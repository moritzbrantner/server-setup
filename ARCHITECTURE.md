# server-setup architecture

Status: accepted, migration in progress  
Decision date: 2026-08-27

## Purpose

`server-setup` is an interactive, idempotent bootstrap and configuration tool for turning a fresh supported Debian/Ubuntu host into an opinionated, secured, validated Dokploy host.

The project owns **host setup and verification**, not application deployment. Dokploy and the services it composes remain the application control plane.

## Ownership boundary

`server-setup` owns:

- supported-host detection and host prerequisites;
- base packages and host-level configuration;
- firewall, fail2ban, unattended security upgrades, and optional SSH hardening;
- installing and bootstrapping a pinned Dokploy release;
- optional host-level DNS tooling;
- optional complementary monitoring that is not already adequately owned by Dokploy;
- future backup/recovery prerequisites and integrations;
- read-only planning, validation, and diagnostics for the desired host state.

`server-setup` does **not** own application builds/deployments, Git integration, application secrets, application domains/TLS, application reverse-proxy rules, replicas/load balancing, deployment history/rollback, or application logs. Those responsibilities belong to Dokploy and its underlying runtime/edge components.

## Interaction model

All user interfaces converge on one versioned declarative configuration and one Python core:

```text
interactive setup ─┐
CLI plan/apply      ├─> /etc/server-setup/config.toml
local-only web UI ──┘                │
                                     v
                              server_setup core
                          inspect / desired / plan
                           apply / validate
                                     │
                    ┌────────────────┼────────────────┐
                    v                v                v
                   host           security         Dokploy
                                                   + optional
                                                   host modules
```

PR2 establishes the CLI commands `setup`, `plan`, `apply`, `validate`, and `doctor`. The future `ui` command remains a convenience surface over the same configuration/core; it must bind to loopback by default and must not grow its own host-mutation logic.

## Configuration contract

The canonical machine configuration is `/etc/server-setup/config.toml`.

- The schema is explicitly versioned.
- Version 1 starts small and uses secure, progressive defaults.
- Optional facilities remain disabled unless selected.
- Secrets are not stored directly in the ordinary configuration file.
- Unknown keys are rejected so typos cannot silently change host intent.
- Dokploy versions are exact pins, not moving `latest`/`canary` references.
- Parsing and semantic validation are separate from host mutation.

`config.example.toml` documents the current v1 schema.

## Module contract

Host responsibilities use a common lifecycle:

1. `inspect()` reads the relevant current host state.
2. `desired(config)` derives desired state from the shared configuration.
3. `plan(current, desired)` reports changes without mutating the host.
4. `apply(changes)` performs only that module's planned changes.
5. `validate(desired)` reports whether the host satisfies desired state.

Plans carry stable machine-readable action/target fields in addition to human summaries. The shared core preserves deterministic module order and rejects changes or validation results attributed to another module.

The operational invariant is:

```text
apply → validate succeeds → second plan is empty
```

## Safety rules

- Host mutation requires root; planning/validation do not intentionally mutate.
- Dangerous changes are explicit and cannot be silently accepted by non-interactive execution.
- SSH hardening validates `sshd` configuration and refuses remote lockout when the current SSH user has no authorized key.
- Dokploy installation re-checks ports and Docker Swarm state at apply time, not only during planning.
- UFW is a host baseline; Docker-published ports are not assumed to be protected by UFW.
- PR2 supports Debian 12 and Ubuntu 24.04. Unsupported distributions fail closed.

## Validation architecture

Tests are layered because normal containers cannot prove the Dokploy installation path:

1. unit/contract tests use deterministic fake host state;
2. Debian 12 and Ubuntu 24.04 privileged systemd containers exercise the real bootstrap, apt, services, validation, and idempotency;
3. an opt-in disposable real VM exercises the official Dokploy installer and real Docker/Swarm/Traefik state.

See `TESTING.md` for the exact gates and limitations.

## Migration rule

The old application deployment engine remains compatibility-only while existing hosts migrate. PR2 moves `setup.sh` to the new host bootstrap and preserves the previous installer as `scripts/legacy_setup.sh`; known legacy flags are forwarded to it.

DNS/monitoring v1 fields remain explicit but are deferred until their existing implementations are evaluated in the optional-services cleanup. Enabling a deferred feature fails visibly rather than silently doing nothing.

Legacy compatibility is not a reason to expand the old deployment engine with new features. The desired end state is smaller than the current repository: `server-setup` manages the host; Dokploy manages applications.
