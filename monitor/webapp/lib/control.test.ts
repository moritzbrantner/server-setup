import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import {
  chmod,
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
} from "node:fs/promises";

import {
  adminControlsEnabled,
  requestHasAdminAccess,
} from "./auth";
import {
  deleteGithubSecret,
  listGithubSecrets,
  readEditableConfig,
  runDashboardAction,
  saveEditableConfig,
  setGithubSecret,
  updateSiteDeploymentSettings,
} from "./control";

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

test("admin access requires the configured token", async () => {
  await withEnv({ STATUS_WEBAPP_ADMIN_TOKEN: "secret-token" }, async () => {
    assert.equal(adminControlsEnabled(), true);
    assert.equal(
      requestHasAdminAccess(
        new Request("http://example.test/api/config", {
          headers: {
            "x-status-admin-token": "secret-token",
          },
        })
      ),
      true
    );
    assert.equal(
      requestHasAdminAccess(
        new Request("http://example.test/api/config", {
          headers: {
            "x-status-admin-token": "wrong-token",
          },
        })
      ),
      false
    );
  });
});

test("saveEditableConfig persists a valid config array", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const configPath = path.join(tmpDir, "sites.json");

  await withEnv(
    {
      SERVER_SETUP_ROOT: tmpDir,
      STATUS_CONFIG_PATH: configPath,
    },
    async () => {
      const saved = await saveEditableConfig(
        JSON.stringify(
          [
            {
              name: "app",
              url: "https://example.com",
            },
          ],
          null,
          2
        )
      );

      assert.equal(saved.path, configPath);
      assert.equal(saved.kind, "monitor");

      const reloaded = await readEditableConfig();
      assert.match(reloaded.raw, /"name": "app"/);
    }
  );
});

test("runDashboardAction retries deploys from registry metadata", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const rootDir = path.join(tmpDir, "root");
  const scriptsDir = path.join(rootDir, "scripts");
  const binDir = path.join(tmpDir, "bin");
  const logsDir = path.join(tmpDir, "logs");
  const configPath = path.join(rootDir, "deploy", "registry.json");
  const automationEnvPath = path.join(tmpDir, "site-automation");
  const sshConfigPath = path.join(tmpDir, "sshd.conf");

  await mkdir(scriptsDir, { recursive: true });
  await mkdir(binDir);
  await mkdir(logsDir);
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "app",
        repo_url: "https://github.com/example/app.git",
        branch: "main",
        checkout_path: "/srv/apps/app",
        deploy_config: {
          name: "app",
          domain: "app.example.com",
          runtime: {
            mode: "service",
            port: 3001,
            health_endpoint: "/healthz",
          },
          service: {
            name: "app.service",
          },
        },
      },
    ]),
    "utf-8"
  );
  await writeFile(path.join(scriptsDir, "deploy_repo.py"), "print('stub deploy')\n", "utf-8");
  await writeFile(automationEnvPath, "WEBHOOK_SECRET=test\n", "utf-8");
  await writeFile(sshConfigPath, "PasswordAuthentication no\nPermitRootLogin no\n", "utf-8");

  await writeExecutable(
    path.join(binDir, "systemctl"),
    `#!/usr/bin/env bash
command="$1"
unit="$2"
case "$command" in
  is-active)
    printf 'active\\n'
    exit 0
    ;;
  restart|reload)
    printf '%s %s\\n' "$command" "$unit" >>"${logsDir}/systemctl.log"
    exit 0
    ;;
  *)
    printf 'active\\n'
    exit 0
    ;;
esac
`
  );
  await writeExecutable(
    path.join(binDir, "python3"),
    `#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${logsDir}/python3.log"
exit 0
`
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("ok", {
      status: 200,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    await withEnv(
      {
        SERVER_SETUP_ROOT: rootDir,
        STATUS_CONFIG_PATH: configPath,
        PATH: `${binDir}:${process.env.PATH || ""}`,
        STATUS_AUTOMATION_ENV_FILE: automationEnvPath,
        STATUS_SSH_HARDENING_CONFIG: sshConfigPath,
        STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      },
      async () => {
        const response = await runDashboardAction({
          action: "retry-deploy",
          siteName: "app",
        });

        assert.equal(response.result.action, "retry-deploy");
        assert.equal(response.snapshot.applications[0]?.name, "app");
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  const loggedCommand = await readFile(path.join(logsDir, "python3.log"), "utf-8");
  assert.match(loggedCommand, /scripts\/deploy_repo\.py --repo-url https:\/\/github\.com\/example\/app\.git --dest \/srv\/apps\/app --branch main --skip-github-hook/);
});

test("runDashboardAction can start and restart nginx", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const rootDir = path.join(tmpDir, "root");
  const binDir = path.join(tmpDir, "bin");
  const logsDir = path.join(tmpDir, "logs");
  const configPath = path.join(rootDir, "deploy", "registry.json");
  const automationEnvPath = path.join(tmpDir, "site-automation");
  const sshConfigPath = path.join(tmpDir, "sshd.conf");

  await mkdir(path.dirname(configPath), { recursive: true });
  await mkdir(binDir);
  await mkdir(logsDir);
  await writeFile(configPath, "[]\n", "utf-8");
  await writeFile(automationEnvPath, "WEBHOOK_SECRET=test\n", "utf-8");
  await writeFile(sshConfigPath, "PasswordAuthentication no\nPermitRootLogin no\n", "utf-8");

  await writeExecutable(
    path.join(binDir, "systemctl"),
    `#!/usr/bin/env bash
command="$1"
unit="$2"
case "$command" in
  is-active)
    printf 'active\\n'
    exit 0
    ;;
  start|restart|reload)
    printf '%s %s\\n' "$command" "$unit" >>"${logsDir}/systemctl.log"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
`
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("ok", {
      status: 200,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    await withEnv(
      {
        SERVER_SETUP_ROOT: rootDir,
        STATUS_CONFIG_PATH: configPath,
        PATH: `${binDir}:${process.env.PATH || ""}`,
        STATUS_AUTOMATION_ENV_FILE: automationEnvPath,
        STATUS_SSH_HARDENING_CONFIG: sshConfigPath,
        STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      },
      async () => {
        const startResponse = await runDashboardAction({ action: "start-nginx" });
        assert.equal(startResponse.result.action, "start-nginx");
        assert.equal(startResponse.result.target, "nginx");

        const restartResponse = await runDashboardAction({ action: "restart-nginx" });
        assert.equal(restartResponse.result.action, "restart-nginx");
        assert.equal(restartResponse.result.target, "nginx");
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  const systemctlLog = await readFile(path.join(logsDir, "systemctl.log"), "utf-8");
  assert.match(systemctlLog, /^start nginx$/m);
  assert.match(systemctlLog, /^restart nginx$/m);
});

test("runDashboardAction can add a new site through deploy_repo", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const rootDir = path.join(tmpDir, "root");
  const scriptsDir = path.join(rootDir, "scripts");
  const binDir = path.join(tmpDir, "bin");
  const logsDir = path.join(tmpDir, "logs");
  const registryPath = path.join(rootDir, "deploy", "registry.json");
  const automationEnvPath = path.join(tmpDir, "site-automation");
  const sshConfigPath = path.join(tmpDir, "sshd.conf");

  await mkdir(scriptsDir, { recursive: true });
  await mkdir(binDir);
  await mkdir(logsDir);
  await writeFile(path.join(scriptsDir, "deploy_repo.py"), "print('stub deploy')\n", "utf-8");
  await writeFile(automationEnvPath, "WEBHOOK_SECRET=test\nDEFAULT_TLS_EMAIL=ops@example.com\n", "utf-8");
  await writeFile(sshConfigPath, "PasswordAuthentication no\nPermitRootLogin no\n", "utf-8");

  await writeExecutable(
    path.join(binDir, "systemctl"),
    `#!/usr/bin/env bash
command="$1"
case "$command" in
  is-active)
    printf 'active\\n'
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
`
  );
  await writeExecutable(
    path.join(binDir, "python3"),
    `#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${logsDir}/python3.log"
mkdir -p "$(dirname "$REGISTRY_PATH")"
cat >"$REGISTRY_PATH" <<'JSON'
[
  {
    "name": "new-app",
    "repo_url": "https://github.com/example/new-app.git",
    "branch": "main",
    "checkout_path": "/srv/apps/new-app",
    "domain": "new-app.example.com",
    "webhook_repo": "example/new-app",
    "deploy_config": {
      "name": "new-app",
      "domain": "new-app.example.com",
      "runtime": {
        "mode": "static"
      }
    }
  }
]
JSON
`
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("ok", {
      status: 200,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    await withEnv(
      {
        SERVER_SETUP_ROOT: rootDir,
        PATH: `${binDir}:${process.env.PATH || ""}`,
        STATUS_AUTOMATION_ENV_FILE: automationEnvPath,
        STATUS_SSH_HARDENING_CONFIG: sshConfigPath,
        STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      },
      async () => {
        const response = await runDashboardAction({
          action: "add-site",
          repoUrl: "https://github.com/example/new-app.git",
          branch: "main",
          checkoutPath: "/srv/apps/new-app",
          email: "ops@example.com",
          skipGithubHook: true,
        });

        assert.equal(response.result.action, "add-site");
        assert.equal(response.snapshot.applications[0]?.name, "new-app");

        const config = await readEditableConfig();
        assert.equal(config.path, registryPath);
        assert.equal(config.kind, "registry");
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  const loggedCommand = await readFile(path.join(logsDir, "python3.log"), "utf-8");
  assert.match(loggedCommand, /scripts\/deploy_repo\.py --repo-url https:\/\/github\.com\/example\/new-app\.git --dest \/srv\/apps\/new-app --branch main --email ops@example\.com --skip-github-hook/);
});

test("updateSiteDeploymentSettings patches registry metadata", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const configPath = path.join(tmpDir, "registry.json");

  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "app",
        domain: "app.example.com",
        deploy_config: {
          name: "app",
          domain: "app.example.com",
          runtime: {
            mode: "static",
          },
          service: {
            name: "app.service",
          },
        },
      },
    ]),
    "utf-8"
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("ok", {
      status: 200,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    await withEnv(
      {
        SERVER_SETUP_ROOT: tmpDir,
        STATUS_CONFIG_PATH: configPath,
        STATUS_AUTOMATION_ENV_FILE: path.join(tmpDir, "site-automation"),
        STATUS_SSH_HARDENING_CONFIG: path.join(tmpDir, "sshd.conf"),
        STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      },
      async () => {
        await writeFile(path.join(tmpDir, "site-automation"), "WEBHOOK_SECRET=test\nDEFAULT_TLS_EMAIL=ops@example.com\n", "utf-8");
        await writeFile(path.join(tmpDir, "sshd.conf"), "PasswordAuthentication no\nPermitRootLogin no\n", "utf-8");

        const response = await updateSiteDeploymentSettings({
          siteName: "app",
          repoUrl: "https://github.com/example/app.git",
          webhookRepo: "",
          branch: "main",
          checkoutPath: "/srv/apps/app",
        });

        assert.equal(response.snapshot.applications[0]?.repoUrl, "https://github.com/example/app.git");
        assert.equal(response.snapshot.applications[0]?.branch, "main");
        assert.equal(response.snapshot.applications[0]?.checkoutPath, "/srv/apps/app");
        assert.equal(response.snapshot.applications[0]?.webhookRepo, "example/app");
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  const raw = await readFile(configPath, "utf-8");
  assert.match(raw, /"repo_url": "https:\/\/github\.com\/example\/app\.git"/);
  assert.match(raw, /"webhook_repo": "example\/app"/);
  assert.match(raw, /"branch": "main"/);
  assert.match(raw, /"checkout_path": "\/srv\/apps\/app"/);
});

test("listGithubSecrets reads secrets for a managed site", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const rootDir = path.join(tmpDir, "root");
  const scriptsDir = path.join(rootDir, "scripts");
  const binDir = path.join(tmpDir, "bin");
  const configPath = path.join(rootDir, "deploy", "registry.json");

  await mkdir(scriptsDir, { recursive: true });
  await mkdir(binDir);
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "app",
        repo_url: "https://github.com/example/app.git",
        webhook_repo: "example/app",
      },
    ]),
    "utf-8"
  );
  await writeFile(path.join(scriptsDir, "manage_github_secrets.py"), "print('stub')\n", "utf-8");
  await writeExecutable(
    path.join(binDir, "python3"),
    `#!/usr/bin/env bash
if [[ "$2" == "list" ]]; then
  cat <<'JSON'
{"action":"list","repo":"example/app","siteName":"app","secrets":[{"name":"API_KEY","updatedAt":"2026-04-01T10:00:00Z","visibility":"private","numSelectedRepos":0}]}
JSON
  exit 0
fi
exit 1
`
  );

  await withEnv(
    {
      SERVER_SETUP_ROOT: rootDir,
      STATUS_CONFIG_PATH: configPath,
      PATH: `${binDir}:${process.env.PATH || ""}`,
    },
    async () => {
      const document = await listGithubSecrets("app");
      assert.equal(document.repo, "example/app");
      assert.equal(document.siteName, "app");
      assert.equal(document.secrets[0]?.name, "API_KEY");
    }
  );
});

test("setGithubSecret and deleteGithubSecret refresh the secret list", async () => {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-control-"));
  const rootDir = path.join(tmpDir, "root");
  const scriptsDir = path.join(rootDir, "scripts");
  const binDir = path.join(tmpDir, "bin");
  const logsDir = path.join(tmpDir, "logs");
  const configPath = path.join(rootDir, "deploy", "registry.json");

  await mkdir(scriptsDir, { recursive: true });
  await mkdir(binDir);
  await mkdir(logsDir);
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(
    configPath,
    JSON.stringify([
      {
        name: "app",
        repo_url: "https://github.com/example/app.git",
        webhook_repo: "example/app",
      },
    ]),
    "utf-8"
  );
  await writeFile(path.join(scriptsDir, "manage_github_secrets.py"), "print('stub')\n", "utf-8");
  await writeExecutable(
    path.join(binDir, "python3"),
    `#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${logsDir}/python3.log"
if [[ "$2" == "set" ]]; then
  cat <<'JSON'
{"action":"set","repo":"example/app","siteName":"app","message":"Updated GitHub secret API_KEY for example/app."}
JSON
  exit 0
fi
if [[ "$2" == "delete" ]]; then
  cat <<'JSON'
{"action":"delete","repo":"example/app","siteName":"app","message":"Deleted GitHub secret API_KEY from example/app."}
JSON
  exit 0
fi
if [[ "$2" == "list" ]]; then
  cat <<'JSON'
{"action":"list","repo":"example/app","siteName":"app","secrets":[{"name":"API_KEY","updatedAt":"2026-04-01T10:00:00Z","visibility":"private","numSelectedRepos":0}]}
JSON
  exit 0
fi
exit 1
`
  );

  await withEnv(
    {
      SERVER_SETUP_ROOT: rootDir,
      STATUS_CONFIG_PATH: configPath,
      PATH: `${binDir}:${process.env.PATH || ""}`,
    },
    async () => {
      const setResponse = await setGithubSecret("app", "API_KEY", "secret-value");
      assert.equal(setResponse.result.action, "set");
      assert.equal(setResponse.document.secrets[0]?.name, "API_KEY");

      const deleteResponse = await deleteGithubSecret("app", "API_KEY");
      assert.equal(deleteResponse.result.action, "delete");
      assert.equal(deleteResponse.document.repo, "example/app");
    }
  );

  const loggedCommand = await readFile(path.join(logsDir, "python3.log"), "utf-8");
  assert.match(loggedCommand, /manage_github_secrets\.py set API_KEY --site app --json/);
  assert.match(loggedCommand, /manage_github_secrets\.py delete API_KEY --site app --json/);
  assert.match(loggedCommand, /manage_github_secrets\.py list --site app --json/);
});
