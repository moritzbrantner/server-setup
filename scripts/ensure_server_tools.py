#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BUN_INSTALL = "/root/.bun"


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This script must run as root or with sudo available.")


def run_checked(cmd: list[str], env: dict[str, str] | None = None, allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, env=env, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(result.returncode)
    return result


def install_pkgs(packages: list[str]) -> None:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_checked(["apt-get", "install", "-y", *packages], env=env)


def bun_env() -> dict[str, str]:
    env = os.environ.copy()
    bun_install = env.get("BUN_INSTALL", DEFAULT_BUN_INSTALL)
    bun_bin = f"{bun_install}/bin"
    env["BUN_INSTALL"] = bun_install
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if bun_bin not in path_entries:
        env["PATH"] = f"{bun_bin}{os.pathsep}{env['PATH']}" if env.get("PATH") else bun_bin
    return env


def ensure_bun_symlink(env: dict[str, str]) -> None:
    link_path = Path("/usr/local/bin/bun")
    target = Path(env["BUN_INSTALL"]) / "bin" / "bun"
    if not target.is_file() or not link_path.parent.is_dir():
        return
    try:
        if link_path.is_symlink():
            if link_path.resolve() != target.resolve():
                link_path.unlink()
                link_path.symlink_to(target)
            return
        if not link_path.exists():
            link_path.symlink_to(target)
    except OSError:
        pass


def configure_docker_repo_for_apt() -> None:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        raise SystemExit("Cannot configure Docker apt repository: /etc/os-release missing.")
    values: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    os_id = values.get("ID", "")
    codename = values.get("VERSION_CODENAME", "")
    if os_id not in {"ubuntu", "debian"}:
        raise SystemExit(f"Apt detected but unsupported distro id for Docker CE repo: '{os_id}'.")
    if not codename and shutil.which("lsb_release"):
        result = run_checked(["lsb_release", "-cs"], allow_fail=True)
        codename = result.stdout.strip()
    if not codename:
        raise SystemExit("Unable to determine distro codename for Docker apt repository setup.")
    arch = run_checked(["dpkg", "--print-architecture"]).stdout.strip()

    install_pkgs(["ca-certificates", "curl", "gnupg"])
    keyring_dir = Path("/etc/apt/keyrings")
    keyring_dir.mkdir(parents=True, exist_ok=True)
    keyring_path = keyring_dir / "docker.asc"
    if not keyring_path.exists() or keyring_path.stat().st_size == 0:
        result = subprocess.run(
            ["curl", "-fsSL", f"https://download.docker.com/linux/{os_id}/gpg"],
            check=True,
            capture_output=True,
        )
        keyring_path.write_bytes(result.stdout)
        keyring_path.chmod(0o644)
    repo_file = Path("/etc/apt/sources.list.d/docker.list")
    repo_file.write_text(
        f"deb [arch={arch} signed-by={keyring_path}] https://download.docker.com/linux/{os_id} {codename} stable\n",
        encoding="utf-8",
    )
    run_checked(["apt-get", "update", "-y"])


def validate_docker_install() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("Docker validation failed: docker binary not found in PATH.")
    if shutil.which("systemctl") and run_checked(["systemctl", "is-active", "--quiet", "docker"], allow_fail=True).returncode != 0:
        raise SystemExit("Docker validation failed: docker service is not active.")


def install_and_enable_docker() -> None:
    log("Ensuring Docker engine is installed")
    configure_docker_repo_for_apt()
    install_pkgs(["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"])
    if shutil.which("systemctl"):
        run_checked(["systemctl", "enable", "--now", "docker"])
    validate_docker_install()


def ensure_postgres_enabled() -> None:
    if not shutil.which("systemctl"):
        return
    units = run_checked(["systemctl", "list-unit-files"], allow_fail=True).stdout.splitlines()
    if any(line.startswith("postgresql.service") or line.startswith("postgresql ") for line in units):
        run_checked(["systemctl", "enable", "--now", "postgresql"], allow_fail=True)
        return
    for line in units:
        if line.startswith("postgresql-") and ".service" in line:
            unit = line.split()[0]
            run_checked(["systemctl", "enable", "--now", unit], allow_fail=True)
            return


def install_or_update_bun() -> None:
    env = bun_env()
    bun_path = Path(env["BUN_INSTALL"]) / "bin" / "bun"
    if bun_path.is_file() or shutil.which("bun", path=env["PATH"]):
        log("Updating bun")
        run_checked(["bun", "upgrade"], env=env, allow_fail=True)
    else:
        log("Installing bun")
        installer = subprocess.run(["curl", "-fsSL", "https://bun.sh/install"], capture_output=True, check=True)
        subprocess.run(["bash"], input=installer.stdout, env=env, check=True)
    ensure_bun_symlink(env)


def install_or_update_gh() -> None:
    log("Installing GitHub CLI via apt")
    install_pkgs(["gh"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install baseline server tools.")
    parser.add_argument("--skip-docker", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    require_root()
    if shutil.which("apt-get") is None:
        raise SystemExit("This script supports Ubuntu LTS (apt) only.")

    log("Using package manager: apt")
    log("Refreshing package indexes")
    run_checked(["apt-get", "update", "-y"])

    log("Upgrading installed system packages")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_checked(["apt-get", "upgrade", "-y"], env=env)

    install_pkgs(
        [
            "ca-certificates",
            "curl",
            "git",
            "jq",
            "unzip",
            "build-essential",
            "nginx",
            "postgresql",
            "postgresql-client",
            "inotify-tools",
        ]
    )
    install_or_update_bun()
    install_or_update_gh()
    ensure_postgres_enabled()

    if args.skip_docker:
        log("Skipping Docker installation because --skip-docker was supplied.")
    else:
        install_and_enable_docker()

    log("Finished: tools, nginx, postgres, and docker bootstrap steps are complete.")


if __name__ == "__main__":
    main()
