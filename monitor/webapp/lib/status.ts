import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

type JsonRecord = Record<string, unknown>;

export type DeployState = {
  current_release?: string;
  last_attempted_release?: string;
  last_deploy_status?: string;
  last_deploy_timestamp?: string;
  last_failure_reason?: string | null;
  last_health_check?: {
    status?: string;
    url?: string;
    message?: string;
    checked_at?: string;
  };
};

export type LoadedSite = {
  name: string;
  domain: string | null;
  url: string;
  checkUrl: string;
  timeoutSeconds: number;
  runtimeMode: string;
  serviceName: string | null;
  deploy: DeployState;
};

export type SiteCheck = LoadedSite & {
  ok: boolean;
  statusCode: number | null;
  latencyMs: number;
  error: string | null;
  serviceStatus: string | null;
};

export type DashboardSnapshot = {
  generatedAt: string;
  summary: {
    totalSites: number;
    healthySites: number;
    degradedSites: number;
    managedServices: number;
    activeServices: number;
  };
  system: {
    hostname: string;
    load: {
      oneMinute: number;
      fiveMinutes: number;
      fifteenMinutes: number;
    };
    disk: {
      totalGb: number | null;
      usedGb: number | null;
      usedPercent: number | null;
    };
    memory: {
      totalMb: number | null;
      usedMb: number | null;
      usedPercent: number | null;
    };
    services: {
      nginx: string;
      docker: string;
    };
  };
  applications: SiteCheck[];
};

function repoRoot(): string {
  return process.env.SERVER_SETUP_ROOT || path.resolve(process.cwd(), "..", "..");
}

function resolveRepoPath(targetPath: string): string {
  if (path.isAbsolute(targetPath)) {
    return targetPath;
  }

  return path.resolve(repoRoot(), targetPath);
}

async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readJsonFile<T>(filePath: string): Promise<T> {
  const raw = await fs.readFile(filePath, "utf-8");
  return JSON.parse(raw) as T;
}

async function defaultConfigPath(): Promise<string> {
  const configured = process.env.STATUS_CONFIG_PATH;
  if (configured) {
    return resolveRepoPath(configured);
  }

  const deployConfig = resolveRepoPath("deploy/sites.json");
  if (await pathExists(deployConfig)) {
    return deployConfig;
  }

  return resolveRepoPath("monitor/websites.json");
}

async function defaultStateDir(): Promise<string | null> {
  const configured = process.env.STATUS_STATE_DIR || process.env.STATE_DIR;
  const candidate = configured ? resolveRepoPath(configured) : "/var/lib/server-setup/state";
  if (await pathExists(candidate)) {
    return candidate;
  }

  return null;
}

async function readDeployState(stateDir: string | null, siteName: string): Promise<DeployState> {
  if (!stateDir) {
    return {};
  }

  const statePath = path.join(stateDir, `${siteName}.json`);
  if (!(await pathExists(statePath))) {
    return {};
  }

  return readJsonFile<DeployState>(statePath);
}

function asObject(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null ? (value as JsonRecord) : {};
}

function pickString(record: JsonRecord, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export async function loadSites(configPath?: string, stateDir?: string | null): Promise<LoadedSite[]> {
  const resolvedConfig = configPath ? resolveRepoPath(configPath) : await defaultConfigPath();
  const resolvedStateDir = stateDir === undefined ? await defaultStateDir() : stateDir;
  const parsed = await readJsonFile<unknown>(resolvedConfig);

  if (!Array.isArray(parsed)) {
    throw new Error("Status config must be a JSON array.");
  }

  return Promise.all(
    parsed.map(async (entry, index) => {
      const site = asObject(entry);
      const name = pickString(site, "name");
      if (!name) {
        throw new Error(`Site at index ${index} is missing a name.`);
      }

      const domain = pickString(site, "domain");
      const url =
        pickString(site, "url") ||
        pickString(site, "site_url") ||
        (domain ? `https://${domain}` : null);

      if (!url) {
        throw new Error(`Site '${name}' must define url, site_url, or domain.`);
      }

      const runtime = asObject(site.runtime);
      const runtimeMode = pickString(runtime, "mode") || "static";
      const timeoutValue = site.timeout;
      const timeoutSeconds =
        typeof timeoutValue === "number" && Number.isFinite(timeoutValue) ? timeoutValue : 5;
      const runtimePortValue = runtime.port;
      const runtimePort =
        typeof runtimePortValue === "number" && Number.isFinite(runtimePortValue)
          ? runtimePortValue
          : null;
      const healthEndpoint = pickString(runtime, "health_endpoint");
      const normalizedHealthEndpoint = healthEndpoint
        ? healthEndpoint.startsWith("/")
          ? healthEndpoint
          : `/${healthEndpoint}`
        : "/";

      let checkUrl = url;
      if (runtimeMode === "service" && runtimePort !== null) {
        checkUrl = `http://127.0.0.1:${runtimePort}${normalizedHealthEndpoint}`;
      } else if (runtimeMode === "static" && domain) {
        checkUrl = `http://${domain}/`;
      }

      return {
        name,
        domain,
        url,
        checkUrl,
        timeoutSeconds,
        runtimeMode,
        serviceName: runtimeMode === "service" ? `app-${name}.service` : null,
        deploy: await readDeployState(resolvedStateDir, name),
      };
    })
  );
}

async function checkSite(site: LoadedSite): Promise<SiteCheck> {
  const startedAt = performance.now();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), site.timeoutSeconds * 1000);

  try {
    const response = await fetch(site.checkUrl, {
      cache: "no-store",
      headers: {
        "user-agent": "server-setup-status-webapp/1.0",
      },
      signal: controller.signal,
    });

    return {
      ...site,
      ok: response.status >= 200 && response.status < 400,
      statusCode: response.status,
      latencyMs: Number((performance.now() - startedAt).toFixed(1)),
      error: null,
      serviceStatus: await getManagedServiceStatus(site),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      ...site,
      ok: false,
      statusCode: null,
      latencyMs: Number((performance.now() - startedAt).toFixed(1)),
      error: message,
      serviceStatus: await getManagedServiceStatus(site),
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function getManagedServiceStatus(site: LoadedSite): Promise<string | null> {
  if (site.runtimeMode !== "service" || !site.serviceName) {
    return null;
  }

  return getServiceStatus(site.serviceName);
}

async function getServiceStatus(serviceName: string): Promise<string> {
  try {
    const { stdout } = await execFileAsync("systemctl", ["is-active", serviceName], {
      timeout: 3000,
    });
    const value = stdout.trim();
    return value || "unknown";
  } catch (error) {
    const failure = error as NodeJS.ErrnoException & { stdout?: string };
    if (failure.code === "ENOENT") {
      return "unknown";
    }

    const output = failure.stdout?.trim();
    return output || "inactive";
  }
}

async function getDiskUsage(): Promise<DashboardSnapshot["system"]["disk"]> {
  try {
    const { stdout } = await execFileAsync("df", ["-kP", "/"], { timeout: 3000 });
    const lines = stdout.trim().split("\n");
    if (lines.length < 2) {
      throw new Error("Unexpected df output.");
    }

    const parts = lines[1].trim().split(/\s+/);
    const totalKb = Number(parts[1]);
    const usedKb = Number(parts[2]);
    const usedPercent = Number(parts[4].replace("%", ""));

    return {
      totalGb: Number((totalKb / 1024 / 1024).toFixed(1)),
      usedGb: Number((usedKb / 1024 / 1024).toFixed(1)),
      usedPercent: Number.isFinite(usedPercent) ? usedPercent : null,
    };
  } catch {
    return { totalGb: null, usedGb: null, usedPercent: null };
  }
}

async function getMemoryUsage(): Promise<DashboardSnapshot["system"]["memory"]> {
  try {
    const raw = await fs.readFile("/proc/meminfo", "utf-8");
    const lines = Object.fromEntries(
      raw
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const [key, rest] = line.split(":");
          const value = Number(rest.trim().split(/\s+/)[0]);
          return [key, value];
        })
    );

    const totalKb = Number(lines.MemTotal);
    const availableKb = Number(lines.MemAvailable);
    const usedKb = Math.max(totalKb - availableKb, 0);
    const usedPercent = totalKb > 0 ? (usedKb / totalKb) * 100 : 0;

    return {
      totalMb: Number((totalKb / 1024).toFixed(0)),
      usedMb: Number((usedKb / 1024).toFixed(0)),
      usedPercent: Number(usedPercent.toFixed(1)),
    };
  } catch {
    const totalMb = os.totalmem() / 1024 / 1024;
    const usedMb = totalMb - os.freemem() / 1024 / 1024;
    const usedPercent = totalMb > 0 ? (usedMb / totalMb) * 100 : 0;

    return {
      totalMb: Number(totalMb.toFixed(0)),
      usedMb: Number(usedMb.toFixed(0)),
      usedPercent: Number(usedPercent.toFixed(1)),
    };
  }
}

async function getSystemSummary(): Promise<DashboardSnapshot["system"]> {
  const [disk, memory, nginx, docker] = await Promise.all([
    getDiskUsage(),
    getMemoryUsage(),
    getServiceStatus("nginx"),
    getServiceStatus("docker"),
  ]);
  const [oneMinute, fiveMinutes, fifteenMinutes] = os.loadavg();

  return {
    hostname: os.hostname(),
    load: {
      oneMinute: Number(oneMinute.toFixed(2)),
      fiveMinutes: Number(fiveMinutes.toFixed(2)),
      fifteenMinutes: Number(fifteenMinutes.toFixed(2)),
    },
    disk,
    memory,
    services: {
      nginx,
      docker,
    },
  };
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  const sites = await loadSites();
  const [system, applications] = await Promise.all([
    getSystemSummary(),
    Promise.all(sites.map((site) => checkSite(site))),
  ]);

  const healthySites = applications.filter((site) => site.ok).length;
  const managedServices = applications.filter((site) => site.serviceStatus !== null).length;
  const activeServices = applications.filter((site) => site.serviceStatus === "active").length;

  return {
    generatedAt: new Date().toISOString(),
    summary: {
      totalSites: applications.length,
      healthySites,
      degradedSites: applications.length - healthySites,
      managedServices,
      activeServices,
    },
    system,
    applications: applications.sort((left, right) => left.name.localeCompare(right.name)),
  };
}
