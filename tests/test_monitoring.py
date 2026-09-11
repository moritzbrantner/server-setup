from __future__ import annotations

import unittest
from typing import Mapping, Sequence

from server_setup.config import parse_config
from server_setup.core import ServerSetupCore
from server_setup.modules import ModuleApplyError, default_modules
from server_setup.modules.monitoring import COMPOSE_PATH, MonitoringModule
from server_setup.system import CommandResult
from tests.fake_system import FakeSystem


MONITORING_CONFIG = '''version = 1
[host]
timezone = "UTC"
unattended_upgrades = false
[security]
firewall = false
fail2ban = false
ssh_hardening = false
[dokploy]
enabled = true
version = "v0.30.2"
[dns]
enabled = false
[monitoring]
uptime_kuma = true
beszel = false
'''

MONITORING_WITHOUT_DOKPLOY = MONITORING_CONFIG.replace(
    "enabled = true\nversion = \"v0.30.2\"",
    "enabled = false\nversion = \"v0.30.2\"",
)

DISABLED_CONFIG = MONITORING_WITHOUT_DOKPLOY.replace("uptime_kuma = true", "uptime_kuma = false")


class MonitoringFakeSystem(FakeSystem):
    def __init__(self) -> None:
        super().__init__()
        self.files[str(COMPOSE_PATH)] = "name: server-setup-services\nservices: {}\n"
        self.running_monitoring: set[str] = set()

    def _run(self, args: tuple[str, ...]) -> CommandResult:
        if args[:3] == ("docker", "compose", "version"):
            return CommandResult(0, "Docker Compose version test\n") if self.docker_available else CommandResult(127)
        if args[:2] == ("docker", "compose") and "ps" in args and "--services" in args:
            if not self.docker_available:
                return CommandResult(127)
            return CommandResult(0, "".join(f"{service}\n" for service in sorted(self.running_monitoring)))
        if args[:2] == ("docker", "compose") and "up" in args:
            if not self.docker_available:
                return CommandResult(127)
            self.running_monitoring.add(args[-1])
            return CommandResult(0)
        if args[:2] == ("docker", "compose") and "stop" in args:
            if not self.docker_available:
                return CommandResult(127)
            self.running_monitoring.discard(args[-1])
            return CommandResult(0)
        return super()._run(args)

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        return super().run(args, check=check, env=env)


class MonitoringTests(unittest.TestCase):
    def test_monitoring_can_plan_before_dokploy_installs_docker(self) -> None:
        system = MonitoringFakeSystem()
        module = MonitoringModule(system)
        desired = module.desired(parse_config(MONITORING_CONFIG))

        plan = module.plan(module.inspect(), desired)

        self.assertEqual([change.action for change in plan], ["start-service"])
        self.assertEqual([change.target for change in plan], ["uptime-kuma"])

    def test_monitoring_without_dokploy_blocks_before_any_host_mutation(self) -> None:
        system = MonitoringFakeSystem()
        config = parse_config(MONITORING_WITHOUT_DOKPLOY)
        core = ServerSetupCore(config, default_modules(system))

        plan = core.plan()

        self.assertTrue(any(change.module == "monitoring" and change.action == "blocked" for change in plan.changes))
        with self.assertRaises(ModuleApplyError):
            core.apply(plan)
        self.assertFalse(system.installed)

    def test_dokploy_and_monitoring_converge_in_one_shared_apply(self) -> None:
        system = MonitoringFakeSystem()
        config = parse_config(MONITORING_CONFIG)
        core = ServerSetupCore(config, default_modules(system))

        plan = core.plan()
        core.apply(plan)
        report = core.validate()

        self.assertTrue(system.dokploy_installed)
        self.assertIn("uptime-kuma", system.running_monitoring)
        self.assertTrue(report.ok)
        self.assertFalse(core.plan().has_changes)

    def test_disabling_monitoring_stops_service_without_deleting_data(self) -> None:
        system = MonitoringFakeSystem()
        system.docker_available = True
        system.commands.add("docker")
        system.running_monitoring.add("uptime-kuma")
        module = MonitoringModule(system)
        desired = module.desired(parse_config(DISABLED_CONFIG))

        plan = module.plan(module.inspect(), desired)
        module.apply(plan)

        self.assertEqual([change.action for change in plan], ["stop-service"])
        self.assertNotIn("uptime-kuma", system.running_monitoring)
        stop_calls = [call for call in system.calls if "stop" in call]
        self.assertEqual(len(stop_calls), 1)
        self.assertNotIn("down", stop_calls[0])
        self.assertNotIn("-v", stop_calls[0])

    def test_enabled_monitoring_fails_validation_without_compose(self) -> None:
        system = MonitoringFakeSystem()
        module = MonitoringModule(system)
        desired = module.desired(parse_config(MONITORING_CONFIG))

        report = module.validate(desired)

        self.assertTrue(any(result.status.value == "fail" and "Compose" in result.summary for result in report))


if __name__ == "__main__":
    unittest.main()
