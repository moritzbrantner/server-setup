#!/usr/bin/env python3
from __future__ import annotations

from registry_contract import ManagedSite, load_managed_sites


AUTOMATION_UNITS = ("site-webhook-receiver.service",)

RESET_UNITS = AUTOMATION_UNITS + (
    "server-setup-status-webapp.service",
    "server-setup-example-apps.service",
)
