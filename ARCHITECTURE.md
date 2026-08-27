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

`server-setup` does **not** own:

- application builds or deployments;
- Git repository integration for applications;
- application environment variables or secrets;
- application domains and TLS configuration;
- application reverse-proxy rules;
- application replicas or load-balancing policy;
- application deployment history or rollback;
- application logs.

Those responsibilities belong to Dokploy and its underlying runtime/edge components. New work must not add a second application deployment platform inside `server-setup`.

## Desired interaction model

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
                 baseline         security         Dokploy
                                                   + optional
                                                   host modules
```

The intended stable commands are `setup`, `plan`, `apply`, `validate`, `doctor`, and `ui`. The command layer is intentionally deferred until the shared core is established.

The future web UI is a convenience surface over the same configuration/core. It must bind to loopback by default and must not grow its own host-mutation logic.

## Configuration contract

The canonical machine configuration is `/etc/server-setup/config.toml`.

- The schema is explicitly versioned.
- Version 1 starts small and uses secure, progressive defaults.
- Optional facilities remain disabled unless selected.
- Secrets are not stored directly in the ordinary configuration file.
- Unknown keys are rejected so typos cannot silently change host intent.
- Parsing and semantic validation are separate from host mutation.

`config.example.toml` documents the current v1 schema.

## Module contract

Host responsibilities are implemented as modules with a common lifecycle:

1. `inspect()` reads the relevant current host state.
2. `desired(config)` derives desired state from the shared configuration.
3. `plan(current, desired)` reports changes without mutating the host.
4. `apply(changes)` performs only that module's planned changes.
5. `validate(desired)` reports whether the host satisfies desired state.

The shared core preserves deterministic module order and rejects changes or validation results attributed to another module. This keeps future CLI and UI behavior consistent.

## Migration rule

The existing Bash installer, custom nginx/application deployment engine, webhook receiver, application registry, repair helpers, and permanent monitoring/control webapp remain compatibility code while the new host-management path is built.

During this migration:

- PR1 introduces this boundary, configuration v1, and the shared core without changing current installations.
- Later slices move bootstrap/apply/validation onto the core before removing legacy application-platform code.
- Legacy compatibility is not a reason to expand the old deployment engine with new features.

The desired end state is smaller than the current repository: `server-setup` manages the host; Dokploy manages applications.
