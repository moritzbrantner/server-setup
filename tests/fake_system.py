from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence

from server_setup.system import CommandResult


class FakeSystem:
    def __init__(self) -> None:
        self.files: dict[str, str] = {
            "/etc/os-release": 'ID="debian"\nVERSION_ID="12"\n',
            "/etc/timezone": "UTC\n",
            "/etc/passwd": "root:x:0:0:root:/root:/bin/bash\nuser:x:1000:1000:user:/home/user:/bin/bash\n",
            "/proc/meminfo": "MemTotal:       4194304 kB\n",
        }
        self.modes: dict[str, int] = {}
        self.installed: set[str] = set()
        self.services: set[str] = set()
        self.commands = {"dpkg-query", "apt-get", "timedatectl", "systemctl", "ufw", "sshd", "ss", "curl", "bash", "df"}
        self.env: dict[str, str] = {"USER": "root"}
        self.euid = 0
        self.timezone = "UTC"
        self.ufw_active = False
        self.ufw_rules: set[str] = set()
        self.docker_available = False
        self.dokploy_installed = False
        self.dokploy_version: str | None = None
        self.swarm_active = False
        self.network_exists = False
        self.ports: set[int] = set()
        self.download_version: str | None = None
        self.calls: list[tuple[str, ...]] = []
        self.sshd_valid = True

    def run(self, args: Sequence[str], *, check: bool = False, env: Mapping[str, str] | None = None) -> CommandResult:
        argv = tuple(args); self.calls.append(argv); result = self._run(argv)
        if check and result.returncode != 0:
            from server_setup.system import CommandError
            raise CommandError(argv, result)
        return result

    def _run(self, args: tuple[str, ...]) -> CommandResult:
        if not args: return CommandResult(0)
        if args[0] == "dpkg-query":
            return CommandResult(0, "install ok installed") if args[-1] in self.installed else CommandResult(1)
        if args[:3] == ("timedatectl", "show", "--property=Timezone"): return CommandResult(0, self.timezone + "\n")
        if args[:2] == ("timedatectl", "set-timezone"):
            self.timezone = args[2]; self.files["/etc/timezone"] = args[2] + "\n"; return CommandResult(0)
        if args[:2] == ("apt-get", "update"): return CommandResult(0)
        if args[:2] == ("apt-get", "install"):
            for value in args[2:]:
                if value != "-y": self.installed.add(value)
            return CommandResult(0)
        if args[:2] == ("systemctl", "is-active"): return CommandResult(0 if args[-1] in self.services else 3)
        if args[:2] == ("systemctl", "is-enabled"): return CommandResult(0 if args[-1] in self.services else 1)
        if args[:3] == ("systemctl", "enable", "--now"): self.services.add(args[-1]); return CommandResult(0)
        if args[:2] == ("systemctl", "restart"): self.services.add(args[-1]); return CommandResult(0)
        if args[:2] == ("systemctl", "reload"): return CommandResult(0)
        if args[:2] == ("ufw", "status"):
            lines = [f"Status: {'active' if self.ufw_active else 'inactive'}"] + [f"{rule} ALLOW Anywhere" for rule in sorted(self.ufw_rules)]
            return CommandResult(0, "\n".join(lines) + "\n")
        if args[:2] == ("ufw", "allow"): self.ufw_rules.add(args[2]); return CommandResult(0)
        if args[-1:] == ("enable",) and args[0] == "ufw": self.ufw_active = True; return CommandResult(0)
        if args[-1:] == ("reload",) and args[0] == "ufw": return CommandResult(0)
        if args[0] == "ufw" and "default" in args: return CommandResult(0)
        if args[:2] == ("sshd", "-t"): return CommandResult(0) if self.sshd_valid else CommandResult(1, stderr="invalid sshd config")
        if args[0] == "ss": return CommandResult(0, "\n".join(f"LISTEN 0 128 0.0.0.0:{p} 0.0.0.0:*" for p in sorted(self.ports)))
        if args[0] == "docker":
            if not self.docker_available: return CommandResult(127)
            if args[1:4] == ("service", "inspect", "dokploy"):
                if not self.dokploy_installed: return CommandResult(1)
                if "--format" in args:
                    return CommandResult(0, f"dokploy/dokploy:{self.dokploy_version or 'latest'}@sha256:test\n")
                return CommandResult(0, "{}")
            if args[1:3] == ("ps", "--format"): return CommandResult(0, "dokploy.1.test\n" if self.dokploy_installed else "")
            if args[1:3] == ("info", "--format"): return CommandResult(0, "active\n" if self.swarm_active else "inactive\n")
            if args[1:3] == ("network", "inspect"): return CommandResult(0 if self.network_exists else 1)
        if args[0] == "curl":
            url = next((value for value in args if value.startswith("https://")), "")
            match = re.search(r"/releases/download/([^/]+)/install\.sh", url); self.download_version = match.group(1) if match else None
            return CommandResult(0)
        if args[0] == "bash" and args[1:2] == ("/tmp/server-setup-dokploy-install.sh",):
            self.docker_available = True; self.commands.add("docker"); self.dokploy_installed = True; self.dokploy_version = self.download_version
            self.swarm_active = True; self.network_exists = True; self.ports.update({80, 443, 3000}); return CommandResult(0)
        if args[0] == "df": return CommandResult(0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/fake 104857600 1 52428800 1% /\n")
        return CommandResult(0)

    def read_text(self, path: Path | str) -> str | None: return self.files.get(str(path))
    def write_text(self, path: Path | str, content: str, *, mode: int | None = None) -> None:
        self.files[str(path)] = content
        if mode is not None: self.modes[str(path)] = mode
    def remove(self, path: Path | str) -> None: self.files.pop(str(path), None)
    def exists(self, path: Path | str) -> bool: return str(path) in self.files
    def command_exists(self, name: str) -> bool: return self.docker_available if name == "docker" else name in self.commands
    def getenv(self, name: str, default: str = "") -> str: return self.env.get(name, default)
    def geteuid(self) -> int: return self.euid
    def sleep(self, seconds: float) -> None: return None
