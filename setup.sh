#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICES_DIR="$ROOT_DIR/services"
ENV_FILE="$SERVICES_DIR/.env"
BASE_COMPOSE="$SERVICES_DIR/compose.yml"
PUBLIC_COMPOSE="$SERVICES_DIR/compose.public.yml"
DOKPLOY_VERSION="${DOKPLOY_VERSION:-v0.30.2}"
DOKPLOY_INSTALL_URL="${DOKPLOY_INSTALL_URL:-https://github.com/Dokploy/dokploy/releases/download/${DOKPLOY_VERSION}/install.sh}"
LEGACY_REGISTRY_PATH="${LEGACY_REGISTRY_PATH:-$ROOT_DIR/deploy/registry.json}"

DRY_RUN=0
INSTALL_DOKPLOY=1
INSTALL_OBSERVABILITY=1
APPLY_HARDENING=1
REPLACE_LEGACY=0
CUTOVER_PREFLIGHT=0
CONFIRM_LEGACY_CUTOVER_READY=0
PUBLIC_OBSERVABILITY=0
WITH_BESZEL_AGENT=0
WITH_SSH_HARDENING=0

LEGACY_UNITS=(
  nginx.service
  site-webhook-receiver.service
  server-setup-status-webapp.service
)
ACTIVE_LEGACY_UNITS=()

usage() {
  cat <<'EOF'
Usage: sudo bash ./setup.sh [options]

Install the canonical server-setup service architecture:
  Dokploy       application deployments, Git webhooks, Traefik, TLS, rollbacks
  Uptime Kuma   uptime checks and status pages
  Beszel        host/container telemetry
  DNSControl    declarative DNS tool for supported providers

Options:
  --skip-dokploy            Keep an existing Dokploy/Docker control plane.
  --skip-observability      Do not start Uptime Kuma or Beszel.
  --skip-hardening          Do not run the host UFW/fail2ban/unattended-upgrades setup.
  --cutover-preflight      Inventory legacy apps, units, and occupied Dokploy ports; make no changes.
  --replace-legacy          Stop the legacy edge during initial Dokploy installation.
  --confirm-legacy-cutover-ready
                            Confirm backups, app definitions, rollback access, and planned downtime.
  --public-observability    Route Uptime Kuma and Beszel through Dokploy Traefik.
  --with-beszel-agent       Start the local Beszel agent container (requires KEY/TOKEN).
  --with-ssh-hardening      Also opt in to the existing SSH hardening routine.
  --dry-run                 Print mutating commands without executing them.
  -h, --help                Show this help.

Configuration is stored in services/.env, created from services/.env.example.
EOF
}

log() {
  printf '[server-setup] %s\n' "$*"
}

die() {
  printf '[server-setup] ERROR: %s\n' "$*" >&2
  exit 1
}

print_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  print_cmd "$@"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

require_root() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return
  fi
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Run this script as root, for example: sudo bash ./setup.sh"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-dokploy) INSTALL_DOKPLOY=0 ;;
      --skip-observability) INSTALL_OBSERVABILITY=0 ;;
      --skip-hardening) APPLY_HARDENING=0 ;;
      --cutover-preflight) CUTOVER_PREFLIGHT=1 ;;
      --replace-legacy) REPLACE_LEGACY=1 ;;
      --confirm-legacy-cutover-ready) CONFIRM_LEGACY_CUTOVER_READY=1 ;;
      --public-observability) PUBLIC_OBSERVABILITY=1 ;;
      --with-beszel-agent) WITH_BESZEL_AGENT=1 ;;
      --with-ssh-hardening) WITH_SSH_HARDENING=1 ;;
      --dry-run) DRY_RUN=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
    shift
  done
}

install_host_baseline() {
  command -v apt-get >/dev/null 2>&1 || die "The canonical installer currently supports apt-based hosts (Ubuntu/Debian)."
  run apt-get update -y
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git jq python3 iproute2
}

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_cmd cp "$SERVICES_DIR/.env.example" "$ENV_FILE"
    print_cmd chmod 600 "$ENV_FILE"
    return
  fi
  cp "$SERVICES_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "Created $ENV_FILE from .env.example"
}

env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'
}

port_in_use() {
  local port="$1"
  command -v ss >/dev/null 2>&1 || return 1
  ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${port}$"
}

unit_exists() {
  local unit="$1"
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^${unit}"
}

print_legacy_inventory() {
  if [[ ! -s "$LEGACY_REGISTRY_PATH" ]]; then
    log "Legacy registry: no managed applications found at $LEGACY_REGISTRY_PATH"
    return
  fi

  command -v python3 >/dev/null 2>&1 || die "python3 is required to inspect $LEGACY_REGISTRY_PATH"
  python3 - "$LEGACY_REGISTRY_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    entries = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    raise SystemExit(f"[server-setup] ERROR: unable to read legacy registry {path}: {error}") from error
if not isinstance(entries, list):
    raise SystemExit(f"[server-setup] ERROR: legacy registry {path} must contain a JSON array")

print(f"[server-setup] Legacy registry: {len(entries)} managed application(s) in {path}")
for entry in entries:
    if not isinstance(entry, dict):
        print("  - <invalid registry entry>")
        continue
    name = str(entry.get("name") or "<unnamed>")
    domain = str(entry.get("domain") or "<no domain>")
    print(f"  - {name}: {domain}")
PY
}

print_legacy_units() {
  local found=0
  local unit
  for unit in "${LEGACY_UNITS[@]}"; do
    if unit_exists "$unit"; then
      found=1
      if systemctl is-active --quiet "$unit"; then
        log "Legacy unit: $unit (active)"
      else
        log "Legacy unit: $unit (inactive)"
      fi
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    log "Legacy units: none found"
  fi
}

dokploy_busy_ports() {
  local busy=()
  local port
  for port in 80 443 3000; do
    if port_in_use "$port"; then
      busy+=("$port")
    fi
  done
  printf '%s' "${busy[*]}"
}

run_cutover_preflight() {
  local busy
  log "Cut-over preflight (read-only)"
  log "Pinned Dokploy release: $DOKPLOY_VERSION"
  print_legacy_inventory
  print_legacy_units
  busy="$(dokploy_busy_ports)"
  if [[ -n "$busy" ]]; then
    log "Occupied Dokploy ports: $busy"
  else
    log "Dokploy ports 80, 443, and 3000 are free"
  fi
  if existing_swarm_active && ! dokploy_installed; then
    log "Blocking condition: an unrelated Docker Swarm is active"
  fi
  log "No changes were made."
}

require_legacy_cutover_ready() {
  print_legacy_inventory
  print_legacy_units
  if [[ "$CONFIRM_LEGACY_CUTOVER_READY" -ne 1 ]]; then
    die "--replace-legacy requires --confirm-legacy-cutover-ready after you have reviewed the inventory, backed up application data and configuration, prepared Dokploy deployment definitions, preserved rollback access, and scheduled downtime."
  fi
}

stop_legacy_edge() {
  ACTIVE_LEGACY_UNITS=()
  local unit
  for unit in "${LEGACY_UNITS[@]}"; do
    if unit_exists "$unit" && systemctl is-active --quiet "$unit"; then
      ACTIVE_LEGACY_UNITS+=("$unit")
      log "Stopping legacy unit for cut-over: $unit"
      if ! run systemctl stop "$unit"; then
        restart_active_legacy_edge
        die "Unable to stop $unit. Previously active legacy units were restarted where possible."
      fi
    fi
  done
}

restart_active_legacy_edge() {
  local unit
  for unit in "${ACTIVE_LEGACY_UNITS[@]}"; do
    if systemctl start "$unit"; then
      log "Rollback restored legacy unit: $unit"
    else
      printf '[server-setup] ERROR: rollback could not restart %s\n' "$unit" >&2
    fi
  done
}

disable_legacy_edge() {
  local unit
  for unit in "${LEGACY_UNITS[@]}"; do
    if unit_exists "$unit"; then
      log "Disabling legacy unit after verified Dokploy installation: $unit"
      run systemctl disable "$unit"
    fi
  done
}

dokploy_installed() {
  command -v docker >/dev/null 2>&1 || return 1
  docker service inspect dokploy >/dev/null 2>&1 && return 0
  docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '(^|-)dokploy($|-)' && return 0
  return 1
}

dokploy_ready() {
  dokploy_installed || return 1
  port_in_use 80 && port_in_use 443 && port_in_use 3000
}

wait_for_dokploy_ready() {
  local attempt
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if dokploy_ready; then
      return 0
    fi
    sleep 2
  done
  return 1
}

existing_swarm_active() {
  command -v docker >/dev/null 2>&1 || return 1
  [[ "$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || true)" == "active" ]]
}

assert_dokploy_ports_free() {
  local busy
  busy="$(dokploy_busy_ports)"
  if [[ -n "$busy" ]]; then
    die "Dokploy requires free ports 80, 443, and 3000; currently busy: $busy. Stop the owning services or use the documented legacy cut-over flow."
  fi
}

install_dokploy() {
  if dokploy_installed; then
    if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
      die "Dokploy is already installed; --replace-legacy only guards the initial same-server installation. Verify the running Dokploy edge before changing legacy units manually."
    fi
    log "Dokploy is already installed; leaving it in place."
    return
  fi

  if [[ "$INSTALL_DOKPLOY" -eq 0 ]]; then
    if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
      die "--replace-legacy cannot be combined with --skip-dokploy."
    fi
    log "Skipping Dokploy installation by request."
    return
  fi

  if existing_swarm_active; then
    die "An existing Docker Swarm is active but Dokploy is not detected. Dokploy's standard installer reinitializes Swarm. Install Dokploy manually into that Swarm, then rerun with --skip-dokploy."
  fi

  if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
    require_legacy_cutover_ready
  else
    assert_dokploy_ports_free
  fi

  log "Installing pinned Dokploy release $DOKPLOY_VERSION."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_cmd curl -fsSL "$DOKPLOY_INSTALL_URL" -o /tmp/server-setup-dokploy-install.sh
    if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
      stop_legacy_edge
      log "Dry run: skipping the post-stop port assertion because legacy units were not actually stopped."
    fi
    print_cmd bash /tmp/server-setup-dokploy-install.sh
    return
  fi

  local installer
  installer="$(mktemp)"
  if ! curl -fsSL "$DOKPLOY_INSTALL_URL" -o "$installer"; then
    rm -f "$installer"
    die "Unable to download the pinned Dokploy installer from $DOKPLOY_INSTALL_URL; the legacy edge was not changed."
  fi

  if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
    stop_legacy_edge
    local busy
    busy="$(dokploy_busy_ports)"
    if [[ -n "$busy" ]]; then
      restart_active_legacy_edge
      rm -f "$installer"
      die "Ports are still occupied after stopping legacy units: $busy. The previously active legacy units were restarted."
    fi
  fi

  if ! bash "$installer"; then
    if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
      restart_active_legacy_edge
    fi
    rm -f "$installer"
    die "Dokploy installation failed. Restarting the previously active legacy units was attempted; review any rollback errors above."
  fi
  rm -f "$installer"

  if ! wait_for_dokploy_ready; then
    if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
      restart_active_legacy_edge
    fi
    die "Dokploy did not become ready on ports 80, 443, and 3000. Restarting the previously active legacy units was attempted; review any rollback errors above and use console access if the partial installation still owns a port."
  fi

  if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
    disable_legacy_edge
  fi
}
ensure_docker_compose() {
  if [[ "$DRY_RUN" -eq 1 && "$INSTALL_DOKPLOY" -eq 1 ]] && ! command -v docker >/dev/null 2>&1; then
    return
  fi
  command -v docker >/dev/null 2>&1 || die "Docker is required. Install Dokploy normally or install Docker before using --skip-dokploy."
  docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is required."
}

validate_service_config() {
  ensure_env_file

  if [[ "$PUBLIC_OBSERVABILITY" -eq 1 ]]; then
    local uptime_host beszel_host
    uptime_host="$(env_value UPTIME_KUMA_HOST)"
    beszel_host="$(env_value BESZEL_HOST)"
    [[ -n "$uptime_host" ]] || die "Set UPTIME_KUMA_HOST in services/.env before using --public-observability."
    [[ -n "$beszel_host" ]] || die "Set BESZEL_HOST in services/.env before using --public-observability."
    if [[ "$DRY_RUN" -eq 0 ]]; then
      docker network inspect dokploy-network >/dev/null 2>&1 || die "dokploy-network is missing; install Dokploy before enabling public observability."
    fi
    local app_url
    app_url="$(env_value BESZEL_APP_URL)"
    if [[ -z "$app_url" || "$app_url" == "http://127.0.0.1:8090" ]]; then
      export BESZEL_APP_URL="https://${beszel_host}"
    fi
  fi

  if [[ "$WITH_BESZEL_AGENT" -eq 1 ]]; then
    [[ -n "$(env_value BESZEL_KEY)" ]] || die "Set BESZEL_KEY in services/.env before using --with-beszel-agent."
    [[ -n "$(env_value BESZEL_TOKEN)" ]] || die "Set BESZEL_TOKEN in services/.env before using --with-beszel-agent."
  fi
}

compose_cmd() {
  COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$BASE_COMPOSE")
  if [[ "$PUBLIC_OBSERVABILITY" -eq 1 ]]; then
    COMPOSE+=(-f "$PUBLIC_COMPOSE")
  fi
}

start_services() {
  validate_service_config
  compose_cmd

  if [[ "$INSTALL_OBSERVABILITY" -eq 1 ]]; then
    run "${COMPOSE[@]}" pull uptime-kuma beszel
    run "${COMPOSE[@]}" up -d uptime-kuma beszel
  else
    log "Skipping Uptime Kuma and Beszel hub by request."
  fi

  if [[ "$WITH_BESZEL_AGENT" -eq 1 ]]; then
    run "${COMPOSE[@]}" --profile agent pull beszel-agent
    run "${COMPOSE[@]}" --profile agent up -d beszel-agent
  fi

  run "${COMPOSE[@]}" --profile tools pull dnscontrol
}

apply_hardening() {
  if [[ "$APPLY_HARDENING" -eq 0 ]]; then
    log "Skipping host hardening by request."
    return
  fi
  local cmd=(python3 "$ROOT_DIR/scripts/harden_server.py")
  if [[ "$WITH_SSH_HARDENING" -eq 1 ]]; then
    cmd+=(--configure-ssh)
  fi
  run "${cmd[@]}"
}

print_summary() {
  local uptime_port beszel_port
  uptime_port="$(env_value UPTIME_KUMA_PORT)"
  beszel_port="$(env_value BESZEL_PORT)"
  uptime_port="${uptime_port:-3001}"
  beszel_port="${beszel_port:-8090}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '\nDry run complete; no mutating commands above were executed.\n'
    return
  fi

  cat <<EOF

Connected-services setup complete.

Control plane:
  Dokploy:     http://<server-ip>:3000

Local operations UIs:
  Uptime Kuma: http://127.0.0.1:${uptime_port}
  Beszel:      http://127.0.0.1:${beszel_port}

DNSControl:
  cd "$SERVICES_DIR"
  docker compose --env-file .env -f compose.yml --profile tools run --rm dnscontrol preview

Security note:
  Dokploy initially publishes port 3000 through Docker. UFW alone may not block Docker-published ports.
  Create the Dokploy admin account, configure an HTTPS domain, verify it, then follow Dokploy's recommendation
  to remove the direct 3000 publication (or block it with your VPS/provider firewall).

Architecture boundary:
  Application domains, TLS, Git sources, auto-deploy webhooks, logs, and rollback now belong in Dokploy.
  DNS declarations live in services/dnscontrol/dnsconfig.js; credentials belong in ignored creds.json.
EOF
}

main() {
  parse_args "$@"
  if [[ "$CUTOVER_PREFLIGHT" -eq 1 ]]; then
    run_cutover_preflight
    return
  fi
  require_root
  install_host_baseline
  install_dokploy
  ensure_docker_compose
  start_services
  apply_hardening
  print_summary
}

main "$@"
