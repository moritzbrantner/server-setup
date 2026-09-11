# Optional host monitoring

The new host-management core can reconcile the existing pinned Uptime Kuma and Beszel services from `services/compose.yml`.

Enable either service in `/etc/server-setup/config.toml`:

```toml
[monitoring]
uptime_kuma = true
beszel = true
```

Then review and apply the normal deterministic plan:

```bash
server-setup plan
sudo server-setup apply --yes
server-setup validate
```

The guided `server-setup setup` flow exposes the same two choices.

## Runtime boundary

Monitoring remains complementary host infrastructure. Dokploy still owns application deployment, application domains/TLS, deployment history, rollback, and application logs.

The monitoring module uses the bundled Compose model installed under `/opt/server-setup/services/compose.yml`. On a fresh host with Dokploy enabled, monitoring can be planned before Docker exists because the earlier Dokploy module installs the runtime in the same ordered apply. If Dokploy is disabled and Docker Compose is unavailable, the plan is terminal before any host mutation rather than partially applying unrelated changes.

## Exposure

The canonical Compose model binds both dashboards to loopback by default:

- Uptime Kuma: `127.0.0.1:3001`
- Beszel: `127.0.0.1:8090`

This slice does not publish monitoring dashboards through a second reverse proxy or add public routing. Remote administration should use an SSH tunnel unless a later, explicit Dokploy/Traefik integration is configured.

## Disable behavior

Changing a monitoring flag back to `false` stops that service but does not remove named volumes. This makes disable/re-enable reversible and avoids treating a configuration toggle as permission to delete monitoring history.

The current slice manages the Uptime Kuma and Beszel hub containers only. Beszel agent enrollment, credentials, and public routing remain separate because they require additional secret/configuration boundaries.
