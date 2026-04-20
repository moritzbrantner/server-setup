import test from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";

import type { DashboardSnapshot } from "@/lib/status";
import { Dashboard } from "./dashboard";

test("dashboard renders important issues and setup details", () => {
  const snapshot: DashboardSnapshot = {
    generatedAt: "2026-03-31T12:00:00Z",
    summary: {
      totalSites: 1,
      healthySites: 0,
      degradedSites: 1,
      managedServices: 1,
      activeServices: 0,
    },
    alerts: [
      {
        id: "host-automation-watcher",
        level: "warning",
        scope: "host",
        title: "Automation: Apps watcher",
        summary: "Apps watcher is inactive.",
      },
      {
        id: "site-service-api",
        level: "critical",
        scope: "site",
        siteName: "api",
        title: "api: service not active",
        summary: "api.service is failed.",
      },
    ],
    setup: {
      overallStatus: "critical",
      categories: [
        {
          id: "core",
          label: "Core",
          status: "critical",
          checks: [
            {
              id: "core-nginx",
              label: "Nginx",
              status: "ok",
              summary: "Nginx is active.",
            },
          ],
        },
        {
          id: "automation",
          label: "Automation",
          status: "warning",
          checks: [
            {
              id: "automation-watcher",
              label: "Apps watcher",
              status: "warning",
              summary: "Apps watcher is inactive.",
            },
          ],
        },
        {
          id: "tls",
          label: "TLS",
          status: "warning",
          checks: [
            {
              id: "tls-api",
              label: "api.example.com",
              status: "warning",
              summary: "No TLS certificate was found for api.example.com.",
            },
          ],
        },
        {
          id: "hardening",
          label: "Hardening",
          status: "ok",
          checks: [
            {
              id: "hardening-ssh",
              label: "SSH hardening",
              status: "ok",
              summary: "SSH hardening config file is present.",
            },
          ],
        },
      ],
    },
    system: {
      hostname: "test-host",
      load: {
        oneMinute: 0.1,
        fiveMinutes: 0.2,
        fifteenMinutes: 0.3,
      },
      disk: {
        totalGb: 100,
        usedGb: 30,
        usedPercent: 30,
      },
      memory: {
        totalMb: 2048,
        usedMb: 1024,
        usedPercent: 50,
      },
      services: {
        nginx: "active",
        docker: "active",
      },
    },
    applications: [
      {
        name: "api",
        domain: "api.example.com",
        url: "https://api.example.com",
        checkUrl: "http://127.0.0.1:3001/healthz",
        timeoutSeconds: 5,
        runtimeMode: "service",
        serviceName: "api.service",
        deploy: {
          current_release: "/srv/releases/api/20260331-120000",
          last_deploy_status: "failed",
          last_deploy_timestamp: "2026-03-31T11:58:00Z",
          last_failure_reason: "systemd restart failed",
          last_health_check: {
            status: "failing",
            message: "health endpoint timed out",
          },
        },
        lastFailureReason: "systemd restart failed",
        lastHealthMessage: "health endpoint timed out",
        lastDeployTimestamp: "2026-03-31T11:58:00Z",
        tlsDomains: ["api.example.com"],
        checkoutPath: "/srv/apps/api",
        ok: false,
        statusCode: 503,
        latencyMs: 25,
        error: "Service Unavailable",
        serviceStatus: "failed",
      },
    ],
  };

  const markup = renderToStaticMarkup(
    <Dashboard adminControlsEnabled={true} initialSnapshot={snapshot} />
  );

  assert.match(markup, /What needs attention/);
  assert.match(markup, /Config and recovery controls/);
  assert.match(markup, /Start Nginx/);
  assert.match(markup, /Restart Nginx/);
  assert.match(markup, /Reload Nginx/);
  assert.match(markup, /Add website/);
  assert.match(markup, /Repository secrets/);
  assert.match(markup, /Porkbun DNS/);
  assert.match(markup, /Create record/);
  assert.match(markup, /Push to Main/);
  assert.match(markup, /Retry deploy/);
  assert.match(markup, /The repository must already include a valid root `server\.conf`\./);
  assert.match(markup, /Automation: Apps watcher/);
  assert.match(markup, /Bootstrap and operations signals/);
  assert.match(markup, /api\.service \(failed\)/);
  assert.match(markup, /Last failure: systemd restart failed/);
});
