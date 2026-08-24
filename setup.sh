#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICES_DIR="$ROOT_DIR/services"
ENV_FILE="$SERVICES_DIR/.env"
BASE_COMPOSE="$SERVICES_DIR/compose.yml"
PUBLIC_COMPOSE="$SERVICES_DIR/compose.public.yml"
DOKPLOY_INSTALL_URL="${DOKPLOY_INSTALL_URL:-https://dokploy.com/install.sh}"

DRY_RUN=0
INSTALL_DOKPLOY=1
INSTALL_OBSERVABILITY=1
APPLY_HARDENING=1
REPLACE_LEGACY=0
PUBLIC_OBSERVABILITY=0
WITH_BESZEL_AGENT=0
WITH_SSH_HARDENING=0

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
  --replace-legacy          Stop legacy nginx/webhook/status units if they block Dokploy.
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
      --replace-legacy) REPLACE_LEGACY=1 ;;
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

stop_legacy_edge() {
  local unit
  for unit in nginx.service site-webhook-receiver.service server-setup-status-webapp.service; do
    if unit_exists "$unit"; then
      log "Stopping legacy unit: $unit"
      run systemctl disable --now "$unit"
    fi
  done
}

dokploy_installed() {
  command -v docker >/dev/null 2>&1 || return 1
  docker service inspect dokploy >/dev/null 2>&1 && return 0
  docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '(^|-)dokploy($|-)' && return 0
  return 1
}

existing_swarm_active() {
  command -v docker >/dev/null 2>&1 || return 1
  [[ "$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || true)" == "active" ]]
}

assert_dokploy_ports_free() {
  local busy=()
  local port
  for port in 80 443 3000; do
    if port_in_use "$port"; then
      busy+=("$port")
    fi
  done
  if [[ "${#busy[@]}" -gt 0 ]]; then
    die "Dokploy requires free ports 80, 443, and 3000; currently busy: ${busy[*]}. Stop the owning services or rerun with --replace-legacy when the conflict is the old server-setup stack."
  fi
}

install_dokploy() {
  if dokploy_installed; then
    log "Dokploy is already installed; leaving it in place."
    return
  fi

  if [[ "$INSTALL_DOKPLOY" -eq 0 ]]; then
    log "Skipping Dokploy installation by request."
    return
  fi

  if existing_swarm_active; then
    die "An existing Docker Swarm is active but Dokploy is not detected. Dokploy's standard installer reinitializes Swarm. Install Dokploy manually into that Swarm, then rerun with --skip-dokploy."
  fi

  if [[ "$REPLACE_LEGACY" -eq 1 ]]; then
    stop_legacy_edge
  fi
  if [[ "$DRY_RUN" -eq 1 && "$REPLACE_LEGACY" -eq 1 ]]; then
    log "Dry run: skipping the post-stop port assertion because legacy units were not actually stopped."
  else
    assert_dokploy_ports_free
  fi

  log "Installing Dokploy with its upstream stable installer."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_cmd curl -fsSL "$DOKPLOY_INSTALL_URL" -o /tmp/server-setup-dokploy-install.sh
    print_cmd bash /tmp/server-setup-dokploy-install.sh
    return
  fi

  local installer
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL "$DOKPLOY_INSTALL_URL" -o "$installer"
  bash "$installer"
  rm -f "$installer"
  trap - EXIT
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
  require_root
  install_host_baseline
  install_dokploy
  ensure_docker_compose
  start_services
  apply_hardening
  print_summary
}

main "$@"
