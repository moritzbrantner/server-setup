from __future__ import annotations

import unittest

from server_setup.config import ServerSetupConfig
from server_setup.core import CoreError, ServerSetupCore
from server_setup.plan import Change, ChangeKind, Plan, ValidationResult, ValidationStatus


class FakeModule:
    def __init__(self, name: str, change_kind: ChangeKind = ChangeKind.UPDATE) -> None:
        self.name = name
        self.change_kind = change_kind
        self.applied: list[tuple[Change, ...]] = []
        self.calls: list[str] = []

    def inspect(self) -> object:
        self.calls.append("inspect")
        return {"current": self.name}

    def desired(self, config: ServerSetupConfig) -> object:
        self.calls.append("desired")
        return {"desired": config.version}

    def plan(self, current: object, desired: object) -> tuple[Change, ...]:
        self.calls.append("plan")
        return (Change(self.name, self.change_kind, f"reconcile {self.name}"),)

    def apply(self, changes: tuple[Change, ...]) -> None:
        self.calls.append("apply")
        self.applied.append(changes)

    def validate(self, desired: object) -> tuple[ValidationResult, ...]:
        self.calls.append("validate")
        return (ValidationResult(self.name, ValidationStatus.PASS, f"{self.name} is valid"),)


class ServerSetupCoreTests(unittest.TestCase):
    def test_plan_preserves_module_order_and_is_read_only(self) -> None:
        first = FakeModule("baseline")
        second = FakeModule("security")
        core = ServerSetupCore(ServerSetupConfig(), [first, second])

        plan = core.plan()

        self.assertEqual([change.module for change in plan.changes], ["baseline", "security"])
        self.assertEqual(first.calls, ["inspect", "desired", "plan"])
        self.assertEqual(second.calls, ["inspect", "desired", "plan"])
        self.assertEqual(first.applied, [])
        self.assertEqual(second.applied, [])
        self.assertTrue(plan.has_changes)

    def test_apply_uses_existing_plan_without_replanning(self) -> None:
        module = FakeModule("security")
        core = ServerSetupCore(ServerSetupConfig(), [module])
        plan = Plan((Change("security", ChangeKind.UPDATE, "update firewall"),))

        returned = core.apply(plan)

        self.assertIs(returned, plan)
        self.assertEqual(module.calls, ["apply"])
        self.assertEqual(module.applied, [(plan.changes[0],)])

    def test_apply_skips_noop_entries(self) -> None:
        module = FakeModule("baseline", ChangeKind.NOOP)
        core = ServerSetupCore(ServerSetupConfig(), [module])

        plan = core.plan()
        core.apply(plan)

        self.assertFalse(plan.has_changes)
        self.assertNotIn("apply", module.calls)

    def test_validate_aggregates_results(self) -> None:
        first = FakeModule("baseline")
        second = FakeModule("dokploy")
        core = ServerSetupCore(ServerSetupConfig(), [first, second])

        report = core.validate()

        self.assertTrue(report.ok)
        self.assertEqual([result.module for result in report.results], ["baseline", "dokploy"])

    def test_duplicate_module_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(CoreError, "unique"):
            ServerSetupCore(ServerSetupConfig(), [FakeModule("security"), FakeModule("security")])

    def test_foreign_changes_are_rejected(self) -> None:
        class BrokenModule(FakeModule):
            def plan(self, current: object, desired: object) -> tuple[Change, ...]:
                return (Change("someone-else", ChangeKind.UPDATE, "wrong owner"),)

        core = ServerSetupCore(ServerSetupConfig(), [BrokenModule("security")])
        with self.assertRaisesRegex(CoreError, "returned a change"):
            core.plan()

    def test_unknown_plan_module_is_rejected(self) -> None:
        core = ServerSetupCore(ServerSetupConfig(), [FakeModule("security")])
        plan = Plan((Change("unknown", ChangeKind.UPDATE, "unknown change"),))

        with self.assertRaisesRegex(CoreError, "unknown module"):
            core.apply(plan)


if __name__ == "__main__":
    unittest.main()
