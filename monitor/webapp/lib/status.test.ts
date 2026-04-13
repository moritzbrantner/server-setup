import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import {
  mkdtemp,
  mkdir,
  writeFile,
  chmod,
} from "node:fs/promises";

import {
  classifyCertificateExpiry,
  getDashboardSnapshot,
  inspectSshHardeningConfig,
  loadSites,
  parseUfwStatus,
} from "./status";

async function withEnv<T>(
  values: Record<string, string | undefined>,
  run: () => Promise<T>
): Promise<T> {
  const previous = new Map<string, string | undefined>();

  for (const [key, value] of Object.entries(values)) {
    previous.set(key, process.env[key]);
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }

  try {
    return await run();
  } finally {
    for (const [key, value] of previous.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

async function writeExecutable(filePath: string, body: string): Promise<void> {
  await writeFile(filePath, body, "utf-8");
  await chmod(filePath, 0o755);
}

test("loadSites uses canonical service.name for service runtimes", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-"));
  const configPath = path.join(tmpDir, "sites.json");
  const stateDir = path.join(tmpDir, "state");

  await mkdir(stateDir);
  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "simple-site",
        domain: "simple.localhost",
        runtime: {
          mode: "service",
          port: 3000,
          health_endpoint: "/healthz",
        },
        service: {
          name: "simple-site.service",
        },
      },
    ]),
    "utf-8"
  );
  await writeFile(
    path.join(stateDir, "simple-site.json"),
    JSON.stringify({
      current_release: "/srv/releases/simple-site/20260313-120000",
      last_deploy_status: "success",
    }),
    "utf-8"
  );

  const sites = await loadSites(configPath, stateDir);

  assert.equal(sites.length, 1);
  assert.equal(sites[0]?.serviceName, "simple-site.service");
  assert.equal(sites[0]?.checkUrl, "http://127.0.0.1:3000/healthz");
});

test("loadSites falls back to legacy app-name units when service.name is absent", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-"));
  const configPath = path.join(tmpDir, "sites.json");

  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "legacy-app",
        domain: "legacy.localhost",
        runtime: {
          mode: "service",
          port: 4100,
        },
      },
    ]),
    "utf-8"
  );

  const sites = await loadSites(configPath, null);

  assert.equal(sites[0]?.serviceName, "app-legacy-app.service");
});

test("dashboard snapshot returns setup categories and site alerts", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-"));
  const configPath = path.join(tmpDir, "sites.json");
  const stateDir = path.join(tmpDir, "state");
  const binDir = path.join(tmpDir, "bin");
  const automationEnvPath = path.join(tmpDir, "site-automation");
  const sshConfigPath = path.join(tmpDir, "sshd.conf");

  await mkdir(stateDir);
  await mkdir(binDir);
  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "api",
        domain: "api.example.com",
        runtime: {
          mode: "service",
          port: 3001,
          health_endpoint: "/healthz",
        },
        service: {
          name: "api.service",
        },
        nginx: {
          tls_hostnames: ["api.example.com"],
        },
      },
    ]),
    "utf-8"
  );
  await writeFile(
    path.join(stateDir, "api.json"),
    JSON.stringify({
      current_release: "/srv/releases/api/20260331-120000",
      last_deploy_status: "failed",
      last_deploy_timestamp: "2026-03-31T11:58:00Z",
      last_failure_reason: "systemd restart failed",
      last_health_check: {
        status: "failing",
        message: "health endpoint timed out",
      },
    }),
    "utf-8"
  );
  await writeFile(
    automationEnvPath,
    "WEBHOOK_SECRET=\n",
    "utf-8"
  );
  await writeFile(
    sshConfigPath,
    "PasswordAuthentication no\nPermitRootLogin no\n",
    "utf-8"
  );
  await writeExecutable(
    path.join(binDir, "systemctl"),
    `#!/usr/bin/env bash
unit="$2"
case "$unit" in
  nginx|docker|server-setup-status-webapp.service|site-webhook-receiver.service|site-discovery-deploy.timer|certbot.timer|fail2ban|unattended-upgrades)
    printf 'active\\n'
    exit 0
    ;;
  site-apps-watcher.service)
    printf 'inactive\\n'
    exit 3
    ;;
  api.service)
    printf 'failed\\n'
    exit 3
    ;;
  *)
    printf 'unknown\\n'
    exit 3
    ;;
esac
`
  );
  await writeExecutable(
    path.join(binDir, "ufw"),
    `#!/usr/bin/env bash
printf 'Status: active\\nTo                         Action      From\\nOpenSSH                    ALLOW       Anywhere\\n80/tcp                     ALLOW       Anywhere\\n443/tcp                    ALLOW       Anywhere\\n'
`
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("down", {
      status: 503,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    const snapshot = await withEnv(
      {
        PATH: `${binDir}:${process.env.PATH || ""}`,
        STATUS_AUTOMATION_ENV_FILE: automationEnvPath,
        STATUS_SSH_HARDENING_CONFIG: sshConfigPath,
        STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      },
      () =>
        getDashboardSnapshot({
          configPath,
          stateDir,
          now: new Date("2026-03-31T12:00:00Z"),
        })
    );

    assert.equal(snapshot.setup.categories.length, 4);
    assert.equal(snapshot.setup.overallStatus, "warning");
    assert.ok(
      snapshot.alerts.some((alert) => alert.title.includes("Automation: Apps watcher"))
    );
    assert.ok(snapshot.alerts.some((alert) => alert.title.includes("api: service not active")));
    assert.ok(snapshot.alerts.some((alert) => alert.title.includes("api: last deploy failed")));
    assert.equal(snapshot.applications[0]?.serviceName, "api.service");
    assert.equal(snapshot.applications[0]?.lastFailureReason, "systemd restart failed");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("parseUfwStatus recognizes required rules", () => {
  const summary = parseUfwStatus(
    "Status: active\nOpenSSH ALLOW Anywhere\n80/tcp ALLOW Anywhere\n443/tcp ALLOW Anywhere\n"
  );

  assert.equal(summary.active, true);
  assert.equal(summary.hasOpenSsh, true);
  assert.equal(summary.hasHttp, true);
  assert.equal(summary.hasHttps, true);
});

test("inspectSshHardeningConfig detects disabled password auth and root login", () => {
  const summary = inspectSshHardeningConfig(
    "# managed\nPasswordAuthentication no\nPermitRootLogin no\n"
  );

  assert.equal(summary.passwordAuthenticationDisabled, true);
  assert.equal(summary.permitRootLoginDisabled, true);
});

test("classifyCertificateExpiry marks near-term expiry as critical", () => {
  const classification = classifyCertificateExpiry(
    new Date("2026-04-05T00:00:00Z"),
    new Date("2026-03-31T00:00:00Z")
  );

  assert.equal(classification.status, "critical");
  assert.equal(classification.daysRemaining, 5);
});
