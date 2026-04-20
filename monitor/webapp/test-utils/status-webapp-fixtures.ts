import { chmod, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

export type StatusWebappFixture = {
  tmpDir: string;
  rootDir: string;
  scriptsDir: string;
  binDir: string;
  logsDir: string;
  stateDir: string;
  configPath: string;
  automationEnvPath: string;
  sshConfigPath: string;
  env: Record<string, string>;
};

export async function withEnv<T>(
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

export async function withMockedFetch<T>(run: () => Promise<T>): Promise<T> {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("ok", {
      status: 200,
      headers: {
        "content-type": "text/plain",
      },
    });

  try {
    return await run();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

export async function writeExecutable(filePath: string, body: string): Promise<void> {
  await writeFile(filePath, body, "utf-8");
  await chmod(filePath, 0o755);
}

export function adminRequest(url: string, init: RequestInit = {}): Request {
  return new Request(url, {
    ...init,
    headers: {
      ...(init.headers || {}),
      "x-status-admin-token": "test-token",
    },
  });
}

export async function readJson<T = Record<string, unknown>>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

export async function readLog(logsDir: string, fileName: string): Promise<string> {
  return readFile(path.join(logsDir, fileName), "utf-8");
}

export async function createStatusWebappFixture(): Promise<StatusWebappFixture> {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "status-webapp-fixture-"));
  const rootDir = path.join(tmpDir, "root");
  const scriptsDir = path.join(rootDir, "scripts");
  const binDir = path.join(tmpDir, "bin");
  const logsDir = path.join(tmpDir, "logs");
  const stateDir = path.join(tmpDir, "state");
  const configPath = path.join(rootDir, "deploy", "registry.json");
  const automationEnvPath = path.join(tmpDir, "site-automation");
  const sshConfigPath = path.join(tmpDir, "sshd.conf");

  await mkdir(scriptsDir, { recursive: true });
  await mkdir(binDir);
  await mkdir(logsDir);
  await mkdir(stateDir);
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(
    configPath,
    JSON.stringify(
      [
        {
          name: "app",
          repo_url: "https://github.com/example/app.git",
          webhook_repo: "example/app",
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
            nginx: {
              tls_hostnames: ["app.example.com"],
            },
            dns: {
              provider: "porkbun",
              zone: "example.com",
            },
          },
        },
      ],
      null,
      2
    ),
    "utf-8"
  );
  await writeFile(
    path.join(stateDir, "app.json"),
    JSON.stringify({
      current_release: "/srv/releases/app/20260331-120000",
      last_deploy_status: "success",
      last_deploy_timestamp: "2026-03-31T11:58:00Z",
      last_health_check: {
        status: "ok",
        message: "healthy",
      },
    }),
    "utf-8"
  );
  await writeFile(
    automationEnvPath,
    "WEBHOOK_SECRET=test-secret\nDEFAULT_TLS_EMAIL=ops@example.com\nWEBHOOK_ALLOWED_REPOS=example/app\nWEBHOOK_ALLOWED_BRANCHES=main\n",
    "utf-8"
  );
  await writeFile(sshConfigPath, "PasswordAuthentication no\nPermitRootLogin no\n", "utf-8");
  await writeFile(path.join(scriptsDir, "deploy_repo.py"), "print('stub deploy')\n", "utf-8");
  await writeFile(path.join(scriptsDir, "manage_github_secrets.py"), "print('stub secrets')\n", "utf-8");
  await writeFile(path.join(scriptsDir, "manage_dns_records.py"), "print('stub dns')\n", "utf-8");

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
    printf 'active\\n'
    exit 0
    ;;
esac
`
  );
  await writeExecutable(
    path.join(binDir, "ufw"),
    `#!/usr/bin/env bash
printf 'Status: active\\nOpenSSH ALLOW Anywhere\\n80/tcp ALLOW Anywhere\\n443/tcp ALLOW Anywhere\\n'
`
  );
  await writeExecutable(
    path.join(binDir, "python3"),
    `#!/usr/bin/env bash
script="$1"
shift
printf '%s %s\\n' "$script" "$*" >>"${logsDir}/python3.log"
case "$(basename "$script")" in
  deploy_repo.py)
    if printf '%s\\n' "$*" | grep -Fq 'https://github.com/example/new-app.git'; then
      mkdir -p "$(dirname "$REGISTRY_PATH")"
      cat >"$REGISTRY_PATH" <<'JSON'
[
  {
    "name": "app",
    "repo_url": "https://github.com/example/app.git",
    "webhook_repo": "example/app",
    "branch": "main",
    "checkout_path": "/srv/apps/app",
    "deploy_config": {
      "name": "app",
      "domain": "app.example.com",
      "runtime": {
        "mode": "service",
        "port": 3001,
        "health_endpoint": "/healthz"
      },
      "service": {
        "name": "app.service"
      },
      "dns": {
        "provider": "porkbun",
        "zone": "example.com"
      }
    }
  },
  {
    "name": "new-app",
    "repo_url": "https://github.com/example/new-app.git",
    "branch": "main",
    "checkout_path": "/srv/apps/new-app",
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
    fi
    printf 'deploy ok\\n'
    ;;
  manage_github_secrets.py)
    command="$1"
    case "$command" in
      list)
        cat <<'JSON'
{"action":"list","repo":"example/app","siteName":"app","checkoutPath":"/srv/apps/app","envFilePath":"/srv/apps/app/.env","workflowFiles":[".github/workflows/deploy.yml"],"secrets":[{"name":"API_KEY","configured":true,"presentInEnvFile":true,"requiredByWorkflows":[".github/workflows/deploy.yml"]}]}
JSON
        ;;
      set)
        cat <<'JSON'
{"action":"set","repo":"example/app","siteName":"app","message":"Updated repository secret API_KEY in /srv/apps/app/.env."}
JSON
        ;;
      delete)
        cat <<'JSON'
{"action":"delete","repo":"example/app","siteName":"app","message":"Deleted repository secret API_KEY from /srv/apps/app/.env."}
JSON
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  manage_dns_records.py)
    command=""
    for arg in "$@"; do
      case "$arg" in
        list|create|update|delete)
          command="$arg"
          break
          ;;
      esac
    done
    case "$command" in
      list)
        cat <<'JSON'
{"action":"list","siteName":"app","domain":"app.example.com","provider":"porkbun","zone":"example.com","records":[{"id":"1","type":"A","name":"app","content":"203.0.113.10","ttl":600,"prio":null}]}
JSON
        ;;
      create)
        cat <<'JSON'
{"action":"create","message":"Created A record app in example.com.","siteName":"app","domain":"app.example.com","provider":"porkbun","zone":"example.com","records":[{"id":"1","type":"A","name":"app","content":"203.0.113.10","ttl":600,"prio":null}]}
JSON
        ;;
      update)
        cat <<'JSON'
{"action":"update","message":"Updated A record app in example.com.","siteName":"app","domain":"app.example.com","provider":"porkbun","zone":"example.com","records":[{"id":"1","type":"A","name":"app","content":"203.0.113.11","ttl":600,"prio":null}]}
JSON
        ;;
      delete)
        cat <<'JSON'
{"action":"delete","message":"Deleted DNS record 1 from example.com.","siteName":"app","domain":"app.example.com","provider":"porkbun","zone":"example.com","records":[]}
JSON
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac
`
  );

  return {
    tmpDir,
    rootDir,
    scriptsDir,
    binDir,
    logsDir,
    stateDir,
    configPath,
    automationEnvPath,
    sshConfigPath,
    env: {
      SERVER_SETUP_ROOT: rootDir,
      STATUS_CONFIG_PATH: configPath,
      STATUS_STATE_DIR: stateDir,
      PATH: `${binDir}:${process.env.PATH || ""}`,
      STATUS_AUTOMATION_ENV_FILE: automationEnvPath,
      STATUS_SSH_HARDENING_CONFIG: sshConfigPath,
      STATUS_LETSENCRYPT_LIVE_DIR: path.join(tmpDir, "letsencrypt"),
      STATUS_WEBAPP_ADMIN_TOKEN: "test-token",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  };
}
