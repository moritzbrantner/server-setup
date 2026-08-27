from __future__ import annotations

import unittest
from dataclasses import replace

from server_setup.config import ServerSetupConfig
from server_setup.core import ServerSetupCore
from server_setup.modules import ModuleApplyError
from server_setup.modules.dokploy import DokployModule
from server_setup.modules.host import BASE_PACKAGES, HostModule
from server_setup.modules.security import SecurityModule
from server_setup.plan import ChangeKind
from tests.fake_system import FakeSystem

class HostModuleTests(unittest.TestCase):
    def test_base_host_reconciles_and_then_has_empty_plan(self) -> None:
        system = FakeSystem(); system.timezone = "Europe/London"; module = HostModule(system)
        config = replace(ServerSetupConfig(), host=replace(ServerSetupConfig().host, timezone="Europe/Berlin")); core = ServerSetupCore(config, [module])
        first = core.plan(); self.assertTrue(first.has_changes); self.assertIn("install-packages", {c.action for c in first.changes}); self.assertIn("set-timezone", {c.action for c in first.changes})
        core.apply(first); self.assertTrue(set(BASE_PACKAGES).issubset(system.installed)); self.assertEqual(system.timezone, "Europe/Berlin"); self.assertFalse(core.plan().has_changes); self.assertTrue(core.validate().ok)
    def test_unsupported_host_is_blocked(self) -> None:
        system = FakeSystem(); system.files["/etc/os-release"] = 'ID="ubuntu"\nVERSION_ID="22.04"\n'; module = HostModule(system); plan = ServerSetupCore(ServerSetupConfig(), [module]).plan(); self.assertTrue(plan.has_dangerous_changes)
        with self.assertRaises(ModuleApplyError): module.apply(plan.changes)

class SecurityModuleTests(unittest.TestCase):
    def test_security_reconciles_unattended_fail2ban_and_firewall(self) -> None:
        system = FakeSystem(); module = SecurityModule(system); desired = module.desired(ServerSetupConfig()); changes = module.plan(module.inspect(), desired); self.assertIn(ChangeKind.DANGEROUS, {c.kind for c in changes}); module.apply(changes)
        self.assertTrue(system.ufw_active); self.assertEqual(system.ufw_rules, {"22/tcp", "80/tcp", "443/tcp"}); self.assertIn("fail2ban", system.services); self.assertIn("unattended-upgrades", system.services); self.assertEqual(module.plan(module.inspect(), desired), ()); self.assertTrue(all(r.status.value in {"pass", "skip"} for r in module.validate(desired)))
    def test_ssh_hardening_refuses_remote_lockout(self) -> None:
        system = FakeSystem(); system.env.update({"USER": "user", "SSH_CONNECTION": "1 2 3 4"}); module = SecurityModule(system); config = replace(ServerSetupConfig(), security=replace(ServerSetupConfig().security, ssh_hardening=True)); changes = module.plan(module.inspect(), module.desired(config)); ssh_change = tuple(c for c in changes if c.action == "configure-ssh")
        with self.assertRaisesRegex(ModuleApplyError, "authorized_keys"): module.apply(ssh_change)

class DokployModuleTests(unittest.TestCase):
    def test_fresh_dokploy_install_uses_pinned_release_and_becomes_idempotent(self) -> None:
        system = FakeSystem(); module = DokployModule(system); desired = module.desired(ServerSetupConfig()); changes = module.plan(module.inspect(), desired); self.assertEqual([c.action for c in changes], ["install"]); module.apply(changes); self.assertEqual(system.dokploy_version, ServerSetupConfig().dokploy.version); self.assertTrue(module.inspect().healthy); self.assertEqual(module.plan(module.inspect(), desired), ()); self.assertTrue(all(r.status.value != "fail" for r in module.validate(desired)))
    def test_install_is_blocked_when_edge_ports_are_occupied(self) -> None:
        system = FakeSystem(); system.ports.add(443); module = DokployModule(system); changes = module.plan(module.inspect(), module.desired(ServerSetupConfig())); self.assertEqual(changes[0].action, "blocked"); self.assertEqual(changes[0].kind, ChangeKind.DANGEROUS)
    def test_install_is_blocked_for_unrelated_swarm(self) -> None:
        system = FakeSystem(); system.docker_available = True; system.commands.add("docker"); system.swarm_active = True; module = DokployModule(system); changes = module.plan(module.inspect(), module.desired(ServerSetupConfig())); self.assertEqual(changes[0].action, "blocked")
    def test_install_rechecks_ports_at_apply_time(self) -> None:
        system = FakeSystem(); module = DokployModule(system); changes = module.plan(module.inspect(), module.desired(ServerSetupConfig())); system.ports.add(80)
        with self.assertRaisesRegex(ModuleApplyError, "ports now occupied"): module.apply(changes)

if __name__ == "__main__": unittest.main()
