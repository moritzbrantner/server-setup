import test from "node:test";
import assert from "node:assert/strict";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createServer, type Server } from "node:http";
import { promises as fs } from "node:fs";
import net from "node:net";
import path from "node:path";

import {
  createStatusWebappFixture,
  readLog,
} from "@/test-utils/status-webapp-fixtures";

async function freePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Unable to allocate a free port."));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

async function listen(server: Server, port: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.on("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
}

async function closeServer(server: Server): Promise<void> {
  await new Promise((resolve) => server.close(resolve));
}

async function waitForServer(baseUrl: string, child: ChildProcessWithoutNullStreams): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`Next server exited early with code ${child.exitCode}.`);
    }
    try {
      const response = await fetch(baseUrl, {
        cache: "no-store",
      });
      if (response.ok) {
        return;
      }
      lastError = new Error(`Server responded with ${response.status}.`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw lastError instanceof Error ? lastError : new Error("Next server did not become ready.");
}

async function stopProcess(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null) {
    return;
  }
  child.kill("SIGTERM");
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      if (child.exitCode === null) {
        child.kill("SIGKILL");
      }
      resolve();
    }, 5000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function jsonRequest<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...(init.headers || {}),
      "content-type": "application/json",
      "x-status-admin-token": "test-token",
    },
  });
  if (!response.ok) {
    assert.fail(`${init.method || "GET"} ${url} returned ${response.status}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

test("status webapp e2e serves dashboard and controls through real HTTP routes", { timeout: 120_000 }, async () => {
  const fixture = await createStatusWebappFixture();
  const appPort = await freePort();
  const sitePort = await freePort();
  const baseUrl = `http://127.0.0.1:${appPort}`;
  const distDir = `.next-e2e-${process.pid}-${appPort}`;
  const tsconfigPath = path.join(process.cwd(), "tsconfig.json");
  const nextEnvPath = path.join(process.cwd(), "next-env.d.ts");
  const originalTsconfig = await fs.readFile(tsconfigPath, "utf-8");
  const originalNextEnv = await fs.readFile(nextEnvPath, "utf-8");
  const siteServer = createServer((_request, response) => {
    response.writeHead(200, {
      "content-type": "text/plain",
    });
    response.end("ok");
  });

  let nextProcess: ChildProcessWithoutNullStreams | null = null;
  let nextOutput = "";

  try {
    const rawConfig = await fs.readFile(fixture.configPath, "utf-8");
    await fs.writeFile(fixture.configPath, rawConfig.replace('"port": 3001', `"port": ${sitePort}`), "utf-8");
    await listen(siteServer, sitePort);

    nextProcess = spawn(
      process.execPath,
      [path.join(process.cwd(), "node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", String(appPort)],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          ...fixture.env,
          NEXT_TELEMETRY_DISABLED: "1",
          STATUS_WEBAPP_TEST_DIST_DIR: distDir,
        },
      }
    );
    nextProcess.stdout.on("data", (chunk: Buffer) => {
      nextOutput += chunk.toString();
    });
    nextProcess.stderr.on("data", (chunk: Buffer) => {
      nextOutput += chunk.toString();
    });

    await waitForServer(baseUrl, nextProcess);

    const pageResponse = await fetch(baseUrl);
    assert.equal(pageResponse.status, 200);
    const page = await pageResponse.text();
    assert.match(page, /Server Setup Monitor/);
    assert.match(page, /Config and recovery controls/);
    assert.match(page, /Deployed sites/);
    assert.match(page, /app\.example\.com/);

    const unauthorized = await fetch(`${baseUrl}/api/config`);
    assert.equal(unauthorized.status, 401);
    assert.match(await unauthorized.text(), /Admin controls are locked/);

    const config = await jsonRequest<{ kind: string; raw: string }>(`${baseUrl}/api/config`);
    assert.equal(config.kind, "registry");
    assert.match(config.raw, /"name": "app"/);

    const action = await jsonRequest<{ result: { action: string } }>(`${baseUrl}/api/actions`, {
      method: "POST",
      body: JSON.stringify({ action: "reload-nginx" }),
    });
    assert.equal(action.result.action, "reload-nginx");

    const siteSettings = await jsonRequest<{ snapshot: { applications: Array<{ branch: string }> } }>(
      `${baseUrl}/api/site-settings`,
      {
        method: "PUT",
        body: JSON.stringify({
          siteName: "app",
          repoUrl: "https://github.com/example/app.git",
          webhookRepo: "example/app",
          branch: "release",
          checkoutPath: "/srv/apps/app",
        }),
      }
    );
    assert.equal(siteSettings.snapshot.applications[0]?.branch, "release");

    const secrets = await jsonRequest<{ secrets: Array<{ name: string }> }>(
      `${baseUrl}/api/github-secrets?siteName=app`
    );
    assert.equal(secrets.secrets[0]?.name, "API_KEY");
    const setSecret = await jsonRequest<{ result: { action: string } }>(`${baseUrl}/api/github-secrets`, {
      method: "PUT",
      body: JSON.stringify({ siteName: "app", name: "API_KEY", value: "secret-value" }),
    });
    assert.equal(setSecret.result.action, "set");

    const records = await jsonRequest<{ records: Array<{ content: string }> }>(
      `${baseUrl}/api/domain-records?siteName=app`
    );
    assert.equal(records.records[0]?.content, "203.0.113.10");
    const updateRecord = await jsonRequest<{ result: { action: string } }>(`${baseUrl}/api/domain-records`, {
      method: "PUT",
      body: JSON.stringify({
        siteName: "app",
        id: "1",
        type: "A",
        name: "app",
        content: "203.0.113.11",
        ttl: 600,
      }),
    });
    assert.equal(updateRecord.result.action, "update");

    const overview = await jsonRequest<{ summary: { totalSites: number } }>(`${baseUrl}/api/overview`);
    assert.equal(overview.summary.totalSites, 1);
  } catch (error) {
    if (nextOutput.trim()) {
      throw new Error(`${error instanceof Error ? error.message : String(error)}\n\nNext output:\n${nextOutput}`);
    }
    throw error;
  } finally {
    if (nextProcess) {
      await stopProcess(nextProcess);
    }
    await closeServer(siteServer);
    await fs.writeFile(tsconfigPath, originalTsconfig, "utf-8");
    await fs.writeFile(nextEnvPath, originalNextEnv, "utf-8");
    await fs.rm(path.join(process.cwd(), distDir), {
      recursive: true,
      force: true,
    });
  }

  const systemctlLog = await readLog(fixture.logsDir, "systemctl.log");
  assert.match(systemctlLog, /^reload nginx$/m);
  const pythonLog = await readLog(fixture.logsDir, "python3.log");
  assert.match(pythonLog, /manage_github_secrets\.py set API_KEY --site app --json/);
  assert.match(pythonLog, /manage_dns_records\.py --registry .* --json update --site app --id 1 --type A --name app --content 203\.0\.113\.11 --ttl 600/);
});
