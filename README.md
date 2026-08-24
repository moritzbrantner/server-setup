# server-setup

`server-setup` is an installer and integration layer for a small self-hosted server stack. New installations compose established services instead of implementing a deployment platform inside this repository.

## Canonical architecture

| Responsibility | Service |
| --- | --- |
| Git deployments, builds, environment variables, logs, deployment history, auto-deploy webhooks, rollback | Dokploy |
| Reverse proxy, domains, HTTPS certificates | Dokploy-managed Traefik |
| Uptime checks and public status pages | Uptime Kuma |
| Host and container telemetry | Beszel |
| Declarative Porkbun / Namecheap DNS | DNSControl |
| UFW, fail2ban, unattended upgrades, optional SSH hardening | host-native `scripts/harden_server.py` |

The repository should own only the glue between those components: installation, safe defaults, configuration examples, validation, and migration from the older implementation.

```text
                         Git repositories
                               |
                               v
                       +----------------+
                       |    Dokploy     |
                       | deploy / env / |
                       | logs / rollback|
                       +-------+--------+
                               |
                    +----------+-----------+
                    | Traefik + Let's Encrypt |
                    +----------+-----------+
                               |
                         applications

          +----------------+       +----------------+
          |  Uptime Kuma   |       |     Beszel     |
          | checks/status  |       | host/containers|
          +----------------+       +----------------+

                         DNSControl
                    Porkbun / Namecheap
```

## Install

On a fresh Ubuntu/Debian server:

```bash
sudo bash ./setup.sh
```

The installer:

1. installs a minimal host baseline (`curl`, `git`, `jq`, Python, networking tools),
2. installs the pinned Dokploy release declared by `DOKPLOY_VERSION` when it is not already present,
3. creates `services/.env` from the committed example,
4. starts Uptime Kuma and the Beszel hub with Docker Compose,
5. pulls DNSControl as the DNS administration tool,
6. applies the existing host-native UFW/fail2ban/unattended-upgrades hardening.

The Uptime Kuma and Beszel ports bind to `127.0.0.1` by default. Dokploy owns ports 80/443 for application traffic and initially publishes its administration UI on port 3000.

### Secure Dokploy after first login

Do not assume UFW hides Docker-published ports: Docker can bypass UFW's normal input rules. Treat Dokploy's initial port 3000 as externally reachable unless a provider/VPS firewall blocks it.

Recommended bootstrap:

1. restrict port 3000 at the VPS/provider firewall to your own IP when possible,
2. create the Dokploy administrator account,
3. configure and verify an HTTPS domain for the Dokploy panel,
4. remove direct IP:port access with Dokploy's recommended command:

```bash
docker service update \
  --publish-rm "published=3000,target=3000,mode=host" \
  dokploy
```

After that, administer Dokploy through its HTTPS domain rather than `server-ip:3000`.

### Installer options

```text
--skip-dokploy           use an already-installed Dokploy/Docker control plane
--skip-observability     do not start Uptime Kuma or Beszel
--skip-hardening         do not change UFW/fail2ban/unattended-upgrades
--cutover-preflight     inventory legacy apps, units, and occupied ports without changes
--replace-legacy         stop the legacy edge during initial Dokploy installation
--confirm-legacy-cutover-ready
                         acknowledge backups, prepared app definitions, rollback access, and downtime
--public-observability   expose Uptime Kuma and Beszel through Dokploy Traefik
--with-beszel-agent      start the local Beszel agent after KEY/TOKEN are configured
--with-ssh-hardening     explicitly opt in to SSH hardening
--dry-run                print mutating commands without executing them
```

### Same-server migration and cut-over

Dokploy and the legacy nginx edge both require ports 80/443, so an in-place migration has a maintenance window. The installer does not claim to provide zero-downtime application migration.

Run the read-only inventory first and save its output with the rest of the change record:

```bash
sudo bash ./setup.sh --cutover-preflight
```

Before cut-over:

1. back up the legacy registry, application checkouts, environment files, databases, uploaded data, and any certificates or service overrides needed for rollback,
2. prepare a Dokploy-compatible Dockerfile, Compose file, or build configuration for every listed application,
3. preserve console or SSH access that does not depend on the applications being migrated,
4. schedule downtime and decide the health checks that must pass before the migration is accepted.

Then run:

```bash
sudo bash ./setup.sh \
  --replace-legacy \
  --confirm-legacy-cutover-ready
```

The installer downloads the pinned Dokploy release before stopping traffic. If ports remain occupied, installation fails, or Dokploy does not become ready on ports 80/443/3000, it attempts to restart every legacy unit that was active before the attempt and reports any unit that could not be restored. Only after the Dokploy edge is ready does it disable the legacy units.

This automatic rollback protects the Dokploy installation step only. Once Dokploy is running, recreate the applications and verify their domains, persistent data, and health checks. A later application-level rollback is manual: free ports 80/443 from the Dokploy edge, then re-enable the legacy units recorded by the preflight.

`--replace-legacy` cannot be combined with `--skip-dokploy`, and it is intentionally rejected after Dokploy is already installed. The installer also refuses to run over an unrelated active Docker Swarm.

The default `DOKPLOY_VERSION` is pinned for reproducibility. Update that value deliberately when adopting a newer release; `DOKPLOY_INSTALL_URL` remains an explicit override for controlled testing.

## Operations services

The production-oriented service composition is [`services/compose.yml`](services/compose.yml). The root [`compose.yml`](compose.yml) remains the existing privileged development/test sandbox and is not the production control plane.

Local dashboards after setup:

```text
Uptime Kuma  http://127.0.0.1:3001
Beszel       http://127.0.0.1:8090
Dokploy      http://<server-ip>:3000 during initial setup; secure it immediately
```

### Public observability

Copy/edit `services/.env` and set:

```dotenv
UPTIME_KUMA_HOST=status.example.com
BESZEL_HOST=metrics.example.com
```

Point those hostnames at the server, then run:

```bash
sudo bash ./setup.sh --skip-dokploy --public-observability
```

The Compose overlay attaches both services to Dokploy's `dokploy-network` and lets the existing Traefik instance provide HTTPS. This avoids running a second reverse proxy.

### Beszel agent

The Beszel hub can run without a local agent. To monitor the host itself, create a public key and token in Beszel, then set:

```dotenv
BESZEL_KEY=...
BESZEL_TOKEN=...
```

Start the local agent:

```bash
sudo bash ./setup.sh --skip-dokploy --with-beszel-agent
```

The hub and agent share a Unix socket. When adding the local system in Beszel, use:

```text
/beszel_socket/beszel.sock
```

Beszel also supports installing the agent as a native system service if containerized host monitoring is not desired; that can be done independently without changing the rest of the stack.

## DNS

DNS is declarative under [`services/dnscontrol`](services/dnscontrol). Provider API code should not be added back to the status UI or deployment scripts.

Create the ignored credential file:

```bash
cp services/dnscontrol/creds.json.example services/dnscontrol/creds.json
chmod 600 services/dnscontrol/creds.json
```

Declare zones and records in `services/dnscontrol/dnsconfig.js`, then preview changes:

```bash
cd services
docker compose --env-file .env -f compose.yml --profile tools run --rm dnscontrol preview
```

Apply only after reviewing the preview:

```bash
docker compose --env-file .env -f compose.yml --profile tools run --rm dnscontrol push
```

The committed credential example contains shapes for both Porkbun and Namecheap. Real `creds.json` is gitignored.

## Deploy applications

For the canonical path, create applications or Compose projects in Dokploy and connect their Git sources there. Dokploy owns application environment variables, build/deploy configuration, domains, HTTPS, auto-deploy webhooks, logs, and rollback state.

A new application does **not** need `server.conf` merely to satisfy `server-setup`. Prefer the application's normal deployment contract (`Dockerfile`, Compose file, or Dokploy build settings) instead of adding another repository-specific hosting abstraction.

## Legacy implementation

The previous deployment engine remains in the repository for existing hosts while they migrate. It includes:

- `scripts/prepare_server.py`
- `scripts/deploy_repo.py`
- `scripts/deploy_engine.py`
- `scripts/install_nginx_site.py`
- `scripts/repair_site.py`
- the custom webhook receiver
- `monitor/webapp`
- the `server.conf` deployment contract

Those files are compatibility/migration code, not the architecture for new installations. Do not expand them with new deployment-platform features when an established service already owns the responsibility.

## Development and tests

Run the repository suite:

```bash
./tests/run-tests.sh
```

The service-stack test validates both the base Compose model and the optional Dokploy/Traefik overlay without starting production services.

Additional development notes live in [`INSTALLING-AND-TESTING.md`](INSTALLING-AND-TESTING.md).
