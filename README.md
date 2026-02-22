# server-setup

Idempotent bootstrap script for server environments.

## What it does

`scripts/ensure-server-tools.sh` will:

- Detect the available package manager (`apt`, `dnf`, `yum`, `apk`, `pacman`, `zypper`).
- Refresh package metadata and upgrade installed system packages.
- Ensure baseline tools are installed: `curl`, `git`, `jq`, `unzip`, build toolchain, and PostgreSQL.
- Install Bun if missing, or run `bun upgrade` if already installed.
- Install/update GitHub CLI (`gh`).
- Attempt to enable and start PostgreSQL using `systemctl` when available.

The script is safe to run repeatedly.

## Usage

```bash
bash scripts/ensure-server-tools.sh
```

If not running as `root`, the script uses `sudo` for package management operations.
