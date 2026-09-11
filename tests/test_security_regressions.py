from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Mapping, Sequence

from server_setup.config import ServerSetupConfig
from server_setup.modules.security import (
    LEGACY_SSHD_HARDENING_PATH,
    SSHD_HARDENING,
    SSHD_HARDENING_PATH,
    SecurityModule,
)
from server_setup.system import CommandResult
from tests.fake_system import FakeSystem


class EffectiveSshSystem(FakeSystem):
    def __init__(self) -> None:
        super().__init__()
        self.effective_settings = {
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
            "pubkeyauthentication": "yes",
            "permitrootlogin": "no",
            "permitemptypasswords": "no",
            "x11forwarding": "no",
            "maxauthtries": "3",
            "logingracetime": "30",
            "clientaliveinterval": "300",
            "clientalivecountmax": "2",
        }

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        argv = tuple(args)
        if argv == ("sshd", "-T"):
            self.calls.append(argv)
            lines = ["port 22", *(f"{key} {value}" for key, value in self.effective_settings.items())]
            return CommandResult(0, "\n".join(lines) + "\n")
        return super().run(args, check=check, env=env)


class SecurityRegressionTests(unittest.TestCase):
    def test_firewall_does_not_accept_similarly_numbered_rules(self) -> None:
        system = FakeSystem()
        system.installed.add("ufw")
        system.ufw_active = True
        system.ufw_rules = {"122/tcp", "8080/tcp", "8443/tcp"}

        state = SecurityModule(system).inspect()

        self.assertFalse(state.firewall_ready)

    def test_managed_ssh_file_is_not_enough_when_effective_config_is_permissive(self) -> None:
        system = EffectiveSshSystem()
        system.files[SSHD_HARDENING_PATH] = SSHD_HARDENING
        system.effective_settings["passwordauthentication"] = "yes"

        state = SecurityModule(system).inspect()

        self.assertFalse(state.ssh_hardening_ready)

    def test_ssh_hardening_migrates_to_early_dropin(self) -> None:
        system = EffectiveSshSystem()
        system.files[LEGACY_SSHD_HARDENING_PATH] = SSHD_HARDENING
        module = SecurityModule(system)
        config = replace(ServerSetupConfig(), security=replace(ServerSetupConfig().security, ssh_hardening=True))
        changes = tuple(
            change
            for change in module.plan(module.inspect(), module.desired(config))
            if change.action == "configure-ssh"
        )

        module.apply(changes)

        self.assertEqual(system.files[SSHD_HARDENING_PATH], SSHD_HARDENING)
        self.assertNotIn(LEGACY_SSHD_HARDENING_PATH, system.files)
        self.assertTrue(module.inspect().ssh_hardening_ready)


if __name__ == "__main__":
    unittest.main()
