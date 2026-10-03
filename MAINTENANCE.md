# Host maintenance and drift

This maintenance surface is intentionally narrower than a general server-management or deployment platform. Application deployment, application rollback, domains, TLS, logs, and deployment history remain Dokploy responsibilities.

## Drift detection

Run a read-only comparison between the declared configuration and live host state:

```bash
server-setup drift
```

The command returns exit code `0` when the plan is empty and validation has no failures, or `1` when reconciliation is needed. For CI or external tooling, use deterministic JSON evidence:

```bash
server-setup drift --json
```

Drift is derived from the same module inspection, planning, and validation paths used by `apply`; there is no second drift model to get out of sync.

## Maintenance snapshots

Capture the exact ordinary configuration, installed versions of packages owned by the host/security modules, and the observed Dokploy version:

```bash
sudo server-setup snapshot
```

The default snapshot is `/var/lib/server-setup/last-upgrade.json` with mode `0600`. A custom path can be supplied with `--output`.

Snapshots are deliberately constrained to the known server-setup package set. A modified snapshot cannot be used to make `rollback` install arbitrary package names.

## Safe managed-package upgrade

```bash
sudo server-setup upgrade --yes
```

The workflow is fail-closed:

1. require the host to be converged before maintenance;
2. capture a rollback snapshot before package mutation;
3. run `apt-get update`;
4. upgrade only already-installed packages owned by server-setup, using `--only-upgrade`;
5. rerun the normal plan and validation surfaces;
6. retain the snapshot whether validation succeeds or fails.

This command does **not** perform a distribution upgrade and does not update arbitrary host packages. It also does not update Dokploy. Dokploy remains explicitly version-pinned in `config.toml` and changes through the normal `plan` / `apply` path.

## Rollback

```bash
sudo server-setup rollback --yes
```

Rollback restores the exact managed package versions recorded in the snapshot using explicit `package=version` targets and `--allow-downgrades`, then reruns plan and validation.

Rollback refuses to proceed when the configuration or observed Dokploy version changed since the snapshot. That prevents a package rollback from silently mixing with unrelated configuration or control-plane changes.

Apt must still be able to resolve the recorded versions. If an old package version is no longer available from configured repositories, rollback fails visibly rather than substituting another version. Preserve VM/provider snapshots or filesystem/database backups for recovery requirements beyond the narrow server-setup-managed package set.
