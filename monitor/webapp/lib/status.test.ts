import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";

import { loadSites } from "./status";

test("loadSites derives url from domain and attaches deploy state", async () => {
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
          mode: "static",
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
  assert.equal(sites[0]?.url, "https://simple.localhost");
  assert.equal(sites[0]?.runtimeMode, "static");
  assert.equal(sites[0]?.serviceName, "simple-site.service");
  assert.equal(
    sites[0]?.deploy.current_release,
    "/srv/releases/simple-site/20260313-120000"
  );
});

test("loadSites accepts explicit site_url when present", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-"));
  const configPath = path.join(tmpDir, "sites.json");

  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "api",
        site_url: "https://api.example.com",
      },
    ]),
    "utf-8"
  );

  const sites = await loadSites(configPath, null);

  assert.equal(sites[0]?.url, "https://api.example.com");
});
