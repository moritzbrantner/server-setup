import { X509Certificate } from "node:crypto";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

type JsonRecord = Record<string, unknown>;

export type StatusLevel = "ok" | "warning" | "critical" | "unknown";

export type DeployState = {
  current_release?: string;
  last_attempted_release?: string;
  last_deploy_status?: string;
  last_deploy_timestamp?: string;
  last_failure_reason?: string | null;
  last_failure_at?: string;
  last_success_at?: string;
  last_rollback_timestamp?: string;
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
  lastFailureReason: string | null;
  lastHealthMessage: string | null;
  lastDeployTimestamp: string | null;
  tlsDomains: string[];
};

export type SiteCheck = LoadedSite & {
  ok: boolean;
  statusCode: number | null;
  latencyMs: number;
  error: string | null;
  serviceStatus: string | null;
};

export type SetupCheck = {
  id: string;
  label: string;
  status: StatusLevel;
  summary: string;
  detail?: string | null;
};

export type SetupCategory = {
  id: "core" | "automation" | "tls" | "hardening";
  label: string;
  status: StatusLevel;
  checks: SetupCheck[];
};

export type DashboardAlert = {
  id: string;
  level: "critical" | "warning";
  scope: "host" | "site";
  title: string;
  summary: string;
  siteName?: string | null;
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
  alerts: DashboardAlert[];
  setup: {
    overallStatus: StatusLevel;
    categories: SetupCategory[];
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

export type DashboardSnapshotOptions = {
  configPath?: string;
  stateDir?: string | null;
  now?: Date;
};

type UfwSummary = {
  active: boolean;
  hasOpenSsh: boolean;
  hasHttp: boolean;
  hasHttps: boolean;
};

type SshHardeningSummary = {
  passwordAuthenticationDisabled: boolean;
  permitRootLoginDisabled: boolean;
};

type ParsedEnvFile = Record<string, string>;

function repoRoot(): string {
  return process.env.SERVER_SETUP_ROOT || path.resolve(process.cwd(), "..", "..");
}

function resolveRepoPath(targetPath: string): string {
  if (path.isAbsolute(targetPath)) {
    return targetPath;
  }

  return path.resolve(repoRoot(), targetPath);
}

function systemPath(envName: string, defaultPath: string): string {
  return resolveRepoPath(process.env[envName] || defaultPath);
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

async function readTextFile(filePath: string): Promise<string | null> {
  try {
    return await fs.readFile(filePath, "utf-8");
  } catch {
    return null;
  }
}

async function defaultConfigPath(): Promise<string> {
  const configured = process.env.STATUS_CONFIG_PATH;
  if (configured) {
    return resolveRepoPath(configured);
  }

  const deployConfig = resolveRepoPath("deploy/registry.json");
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
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function pickNumber(record: JsonRecord, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeHealthEndpoint(value: string | null): string {
  if (!value) {
    return "/";
  }

  return value.startsWith("/") ? value : `/${value}`;
}

function normalizeTlsDomains(site: JsonRecord, domain: string | null): string[] {
  const nginx = asObject(site.nginx);
  const tlsHostnames = Array.isArray(nginx.tls_hostnames)
    ? nginx.tls_hostnames.filter((entry): entry is string => typeof entry === "string" && entry.trim().length > 0)
    : [];

  if (tlsHostnames.length > 0) {
    return [...new Set(tlsHostnames.map((entry) => entry.trim()))];
  }

  return domain ? [domain] : [];
}

function deriveServiceName(site: JsonRecord, name: string, runtimeMode: string): string | null {
  if (runtimeMode !== "service") {
    return null;
  }

  const service = asObject(site.service);
  return pickString(service, "name") || `app-${name}.service`;
}

function deriveLastDeployTimestamp(deploy: DeployState): string | null {
  return (
    deploy.last_deploy_timestamp ||
    deploy.last_success_at ||
    deploy.last_failure_at ||
    deploy.last_rollback_timestamp ||
    null
  );
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
      const timeoutSeconds = pickNumber(site, "timeout") ?? 5;
      const runtimePort = pickNumber(runtime, "port");
      const healthEndpoint = normalizeHealthEndpoint(pickString(runtime, "health_endpoint"));
      const deploy = await readDeployState(resolvedStateDir, name);

      let checkUrl = url;
      if (runtimeMode === "service" && runtimePort !== null) {
        checkUrl = `http://127.0.0.1:${runtimePort}${healthEndpoint}`;
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
        serviceName: deriveServiceName(site, name, runtimeMode),
        deploy,
        lastFailureReason: deploy.last_failure_reason ?? null,
        lastHealthMessage: deploy.last_health_check?.message ?? null,
        lastDeployTimestamp: deriveLastDeployTimestamp(deploy),
        tlsDomains: normalizeTlsDomains(site, domain),
      };
    })
  );
}

async function checkSite(site: LoadedSite): Promise<SiteCheck> {
  const startedAt = performance.now();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), site.timeoutSeconds * 1000);
  const serviceStatus = await getManagedServiceStatus(site);

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
      serviceStatus,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      ...site,
      ok: false,
      statusCode: null,
      latencyMs: Number((performance.now() - startedAt).toFixed(1)),
      error: message,
      serviceStatus,
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function getManagedServiceStatus(site: LoadedSite): Promise<string | null> {
  if (site.runtimeMode !== "service" || !site.serviceName) {
    return null;
  }

  return getUnitStatus(site.serviceName);
}

async function getUnitStatus(unitName: string): Promise<string> {
  try {
    const { stdout } = await execFileAsync("systemctl", ["is-active", unitName], {
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

async function execTextFile(command: string, args: string[]): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(command, args, { timeout: 3000 });
    return stdout;
  } catch (error) {
    const failure = error as NodeJS.ErrnoException & { stdout?: string };
    if (failure.code === "ENOENT") {
      return null;
    }

    return failure.stdout ?? null;
  }
}

async function getDiskUsage(): Promise<DashboardSnapshot["system"]["disk"]> {
  try {
    const { stdout } = await execFileAsync("df", ["-kP", "/"], { timeout: 3000 });
    const lines = stdout.trim().split("\n");
    if (lines.length < 2) {
      throw new Error("Unexpected df output.");
    }

    const parts = lines[1]?.trim().split(/\s+/) ?? [];
    const totalKb = Number(parts[1]);
    const usedKb = Number(parts[2]);
    const usedPercent = Number((parts[4] || "").replace("%", ""));

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
          const [key, rest = "0"] = line.split(":");
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
    getUnitStatus("nginx"),
    getUnitStatus("docker"),
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

function statusWeight(status: StatusLevel): number {
  switch (status) {
    case "critical":
      return 4;
    case "warning":
      return 3;
    case "ok":
      return 2;
    default:
      return 1;
  }
}

function highestStatus(statuses: StatusLevel[]): StatusLevel {
  let best: StatusLevel = "unknown";
  for (const status of statuses) {
    if (statusWeight(status) > statusWeight(best)) {
      best = status;
    }
  }
  return best;
}

function formatUnitSummary(label: string, state: string): string {
  return `${label} is ${state}.`;
}

function unitCheck(
  id: string,
  label: string,
  state: string,
  badState: StatusLevel,
  unknownSummary: string
): SetupCheck {
  if (state === "active") {
    return {
      id,
      label,
      status: "ok",
      summary: formatUnitSummary(label, state),
    };
  }

  if (state === "unknown") {
    return {
      id,
      label,
      status: "unknown",
      summary: unknownSummary,
    };
  }

  return {
    id,
    label,
    status: badState,
    summary: formatUnitSummary(label, state),
  };
}

export function parseUfwStatus(raw: string): UfwSummary {
  const active = /Status:\s*active/i.test(raw);
  return {
    active,
    hasOpenSsh: /OpenSSH/i.test(raw),
    hasHttp: /(80\/tcp|Nginx Full)/i.test(raw),
    hasHttps: /(443\/tcp|Nginx Full)/i.test(raw),
  };
}

export function inspectSshHardeningConfig(raw: string): SshHardeningSummary {
  const settings = new Map<string, string>();

  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const [key, ...rest] = trimmed.split(/\s+/);
    if (!key || rest.length === 0) {
      continue;
    }

    settings.set(key.toLowerCase(), rest.join(" ").toLowerCase());
  }

  return {
    passwordAuthenticationDisabled: settings.get("passwordauthentication") === "no",
    permitRootLoginDisabled: settings.get("permitrootlogin") === "no",
  };
}

export function classifyCertificateExpiry(validTo: Date, now = new Date()): {
  status: StatusLevel;
  daysRemaining: number;
  summary: string;
} {
  const daysRemaining = Math.ceil((validTo.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));

  if (daysRemaining < 0) {
    return {
      status: "critical",
      daysRemaining,
      summary: "Certificate has expired.",
    };
  }

  if (daysRemaining <= 7) {
    return {
      status: "critical",
      daysRemaining,
      summary: `Certificate expires in ${daysRemaining} days.`,
    };
  }

  if (daysRemaining <= 21) {
    return {
      status: "warning",
      daysRemaining,
      summary: `Certificate expires in ${daysRemaining} days.`,
    };
  }

  return {
    status: "ok",
    daysRemaining,
    summary: `Certificate expires in ${daysRemaining} days.`,
  };
}

function parseEnvFile(raw: string): ParsedEnvFile {
  const parsed: ParsedEnvFile = {};

  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separator = trimmed.indexOf("=");
    if (separator < 0) {
      continue;
    }

    const key = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim().replace(/^['"]|['"]$/g, "");
    if (key) {
      parsed[key] = value;
    }
  }

  return parsed;
}

async function getAutomationChecks(): Promise<SetupCheck[]> {
  const webhook = await getUnitStatus("site-webhook-receiver.service");
  const envFilePath = systemPath("STATUS_AUTOMATION_ENV_FILE", "/etc/default/site-automation");
  const envFileExists = await pathExists(envFilePath);
  const envRaw = envFileExists ? await readTextFile(envFilePath) : null;
  const parsedEnv = envRaw ? parseEnvFile(envRaw) : {};
  const webhookSecretConfigured = Boolean(parsedEnv.WEBHOOK_SECRET);

  return [
    unitCheck(
      "automation-webhook",
      "Webhook receiver",
      webhook,
      "warning",
      "systemctl is unavailable, so webhook status could not be determined."
    ),
    {
      id: "automation-env-file",
      label: "Automation env file",
      status: envFileExists ? "ok" : "warning",
      summary: envFileExists
        ? "Automation environment file is present."
        : "Automation environment file is missing.",
      detail: envFileExists ? envFilePath : null,
    },
    {
      id: "automation-webhook-secret",
      label: "Webhook secret",
      status: envFileExists ? (webhookSecretConfigured ? "ok" : "warning") : "unknown",
      summary: envFileExists
        ? webhookSecretConfigured
          ? "Webhook secret is configured."
          : "Webhook secret is missing."
        : "Webhook secret could not be checked because the automation env file is missing.",
    },
  ];
}

async function getTlsChecks(sites: LoadedSite[], now: Date): Promise<SetupCheck[]> {
  const checks: SetupCheck[] = [
    unitCheck(
      "tls-certbot-timer",
      "Certbot renewal timer",
      await getUnitStatus("certbot.timer"),
      "warning",
      "systemctl is unavailable, so certbot timer status could not be determined."
    ),
  ];
  const letsencryptLiveDir = systemPath("STATUS_LETSENCRYPT_LIVE_DIR", "/etc/letsencrypt/live");
  const seenDomains = new Set<string>();

  for (const site of sites) {
    for (const domain of site.tlsDomains) {
      if (!domain || seenDomains.has(domain)) {
        continue;
      }
      seenDomains.add(domain);

      const certPath = path.join(letsencryptLiveDir, domain, "fullchain.pem");
      if (!(await pathExists(certPath))) {
        checks.push({
          id: `tls-${domain}`,
          label: domain,
          status: "warning",
          summary: `No TLS certificate was found for ${domain}.`,
        });
        continue;
      }

      try {
        const certificatePem = await fs.readFile(certPath, "utf-8");
        const certificate = new X509Certificate(certificatePem);
        const validTo = new Date(certificate.validTo);
        if (Number.isNaN(validTo.getTime())) {
          throw new Error("invalid certificate expiry");
        }

        const classification = classifyCertificateExpiry(validTo, now);
        checks.push({
          id: `tls-${domain}`,
          label: domain,
          status: classification.status,
          summary: classification.summary,
          detail: `Certificate path: ${certPath}`,
        });
      } catch {
        checks.push({
          id: `tls-${domain}`,
          label: domain,
          status: "unknown",
          summary: `Certificate data for ${domain} could not be parsed.`,
        });
      }
    }
  }

  return checks;
}

async function getHardeningChecks(): Promise<SetupCheck[]> {
  const sshHardeningPath = systemPath(
    "STATUS_SSH_HARDENING_CONFIG",
    "/etc/ssh/sshd_config.d/99-server-setup-hardening.conf"
  );
  const sshHardeningRaw = await readTextFile(sshHardeningPath);
  const sshSummary = sshHardeningRaw ? inspectSshHardeningConfig(sshHardeningRaw) : null;
  const fail2ban = await getUnitStatus("fail2ban");
  const unattended = await getUnitStatus("unattended-upgrades");
  const ufwRaw = await execTextFile("ufw", ["status"]);
  const ufwSummary = ufwRaw ? parseUfwStatus(ufwRaw) : null;

  return [
    {
      id: "hardening-ssh-config",
      label: "SSH hardening",
      status: sshHardeningRaw ? "ok" : "warning",
      summary: sshHardeningRaw
        ? "SSH hardening config file is present."
        : "SSH hardening config file is missing.",
      detail: sshHardeningRaw ? sshHardeningPath : null,
    },
    {
      id: "hardening-password-auth",
      label: "Password authentication",
      status: sshSummary
        ? sshSummary.passwordAuthenticationDisabled
          ? "ok"
          : "critical"
        : "unknown",
      summary: sshSummary
        ? sshSummary.passwordAuthenticationDisabled
          ? "Password authentication is disabled."
          : "Password authentication is not disabled."
        : "Password authentication setting could not be determined.",
    },
    {
      id: "hardening-root-login",
      label: "Root SSH login",
      status: sshSummary
        ? sshSummary.permitRootLoginDisabled
          ? "ok"
          : "critical"
        : "unknown",
      summary: sshSummary
        ? sshSummary.permitRootLoginDisabled
          ? "Root SSH login is disabled."
          : "Root SSH login is not disabled."
        : "Root login setting could not be determined.",
    },
    unitCheck(
      "hardening-fail2ban",
      "fail2ban",
      fail2ban,
      "warning",
      "systemctl is unavailable, so fail2ban status could not be determined."
    ),
    unitCheck(
      "hardening-unattended-upgrades",
      "Unattended upgrades",
      unattended,
      "warning",
      "systemctl is unavailable, so unattended-upgrades status could not be determined."
    ),
    {
      id: "hardening-ufw",
      label: "UFW firewall",
      status: ufwSummary
        ? ufwSummary.active && ufwSummary.hasOpenSsh && ufwSummary.hasHttp && ufwSummary.hasHttps
          ? "ok"
          : "warning"
        : "unknown",
      summary: ufwSummary
        ? ufwSummary.active
          ? ufwSummary.hasOpenSsh && ufwSummary.hasHttp && ufwSummary.hasHttps
            ? "UFW is active with SSH, HTTP, and HTTPS rules present."
            : "UFW is active but one or more required allow rules are missing."
          : "UFW is inactive."
        : "UFW status could not be determined.",
    },
  ];
}

async function getSetupHealth(
  sites: LoadedSite[],
  system: DashboardSnapshot["system"],
  now: Date
): Promise<DashboardSnapshot["setup"]> {
  const [automationChecks, tlsChecks, hardeningChecks, statusWebapp] = await Promise.all([
    getAutomationChecks(),
    getTlsChecks(sites, now),
    getHardeningChecks(),
    getUnitStatus("server-setup-status-webapp.service"),
  ]);

  const categories: SetupCategory[] = [
    {
      id: "core",
      label: "Core",
      checks: [
        unitCheck(
          "core-nginx",
          "Nginx",
          system.services.nginx,
          "critical",
          "systemctl is unavailable, so nginx status could not be determined."
        ),
        unitCheck(
          "core-docker",
          "Docker",
          system.services.docker,
          "warning",
          "systemctl is unavailable, so docker status could not be determined."
        ),
        unitCheck(
          "core-status-webapp",
          "Status webapp",
          statusWebapp,
          "critical",
          "systemctl is unavailable, so status webapp service state could not be determined."
        ),
      ],
      status: "unknown",
    },
    {
      id: "automation",
      label: "Automation",
      checks: automationChecks,
      status: "unknown",
    },
    {
      id: "tls",
      label: "TLS",
      checks: tlsChecks,
      status: "unknown",
    },
    {
      id: "hardening",
      label: "Hardening",
      checks: hardeningChecks,
      status: "unknown",
    },
  ];

  for (const category of categories) {
    category.status = highestStatus(category.checks.map((check) => check.status));
  }

  return {
    overallStatus: highestStatus(categories.map((category) => category.status)),
    categories,
  };
}

function alertLevelFromStatus(status: StatusLevel): "critical" | "warning" | null {
  if (status === "critical") {
    return "critical";
  }

  if (status === "warning") {
    return "warning";
  }

  return null;
}

function buildHostAlerts(setup: DashboardSnapshot["setup"]): DashboardAlert[] {
  const alerts: DashboardAlert[] = [];

  for (const category of setup.categories) {
    for (const check of category.checks) {
      const level = alertLevelFromStatus(check.status);
      if (!level) {
        continue;
      }

      alerts.push({
        id: `host-${check.id}`,
        level,
        scope: "host",
        title: `${category.label}: ${check.label}`,
        summary: check.summary,
      });
    }
  }

  return alerts;
}

function isPassingHealthStatus(status: string | undefined): boolean {
  return ["passing", "success", "ok", "healthy", "not-applicable"].includes(
    (status || "").toLowerCase()
  );
}

function buildSiteAlerts(applications: SiteCheck[]): DashboardAlert[] {
  const alerts: DashboardAlert[] = [];

  for (const application of applications) {
    if (!application.ok) {
      alerts.push({
        id: `site-http-${application.name}`,
        level: "critical",
        scope: "site",
        siteName: application.name,
        title: `${application.name}: HTTP check failing`,
        summary: application.error || `HTTP check to ${application.checkUrl} did not succeed.`,
      });
    }

    if (application.runtimeMode === "service" && application.serviceName && application.serviceStatus !== "active") {
      alerts.push({
        id: `site-service-${application.name}`,
        level: "critical",
        scope: "site",
        siteName: application.name,
        title: `${application.name}: service not active`,
        summary: `${application.serviceName} is ${application.serviceStatus || "unknown"}.`,
      });
    }

    if (application.deploy.last_deploy_status === "failed") {
      alerts.push({
        id: `site-deploy-${application.name}`,
        level: "critical",
        scope: "site",
        siteName: application.name,
        title: `${application.name}: last deploy failed`,
        summary: application.lastFailureReason || "The last deployment failed.",
      });
    } else if (application.lastFailureReason) {
      alerts.push({
        id: `site-failure-${application.name}`,
        level: "warning",
        scope: "site",
        siteName: application.name,
        title: `${application.name}: last failure recorded`,
        summary: application.lastFailureReason,
      });
    }

    const healthStatus = application.deploy.last_health_check?.status;
    if (healthStatus && !isPassingHealthStatus(healthStatus)) {
      alerts.push({
        id: `site-health-${application.name}`,
        level: "warning",
        scope: "site",
        siteName: application.name,
        title: `${application.name}: health check warning`,
        summary: application.lastHealthMessage || `Latest health check status is ${healthStatus}.`,
      });
    }
  }

  return alerts;
}

function sortAlerts(alerts: DashboardAlert[]): DashboardAlert[] {
  return [...alerts].sort((left, right) => {
    const leftScore = left.level === "critical" ? 0 : 1;
    const rightScore = right.level === "critical" ? 0 : 1;
    if (leftScore !== rightScore) {
      return leftScore - rightScore;
    }

    const leftScope = left.scope === "host" ? 0 : 1;
    const rightScope = right.scope === "host" ? 0 : 1;
    if (leftScope !== rightScope) {
      return leftScope - rightScope;
    }

    return left.title.localeCompare(right.title);
  });
}

export async function getDashboardSnapshot(
  options: DashboardSnapshotOptions = {}
): Promise<DashboardSnapshot> {
  const now = options.now ?? new Date();
  const sites = await loadSites(options.configPath, options.stateDir);
  const [system, applications] = await Promise.all([
    getSystemSummary(),
    Promise.all(sites.map((site) => checkSite(site))),
  ]);
  const setup = await getSetupHealth(applications, system, now);
  const alerts = sortAlerts([...buildHostAlerts(setup), ...buildSiteAlerts(applications)]);

  const healthySites = applications.filter((site) => site.ok).length;
  const managedServices = applications.filter((site) => site.serviceStatus !== null).length;
  const activeServices = applications.filter((site) => site.serviceStatus === "active").length;

  return {
    generatedAt: now.toISOString(),
    summary: {
      totalSites: applications.length,
      healthySites,
      degradedSites: applications.length - healthySites,
      managedServices,
      activeServices,
    },
    alerts,
    setup,
    system,
    applications: applications.sort((left, right) => left.name.localeCompare(right.name)),
  };
}
