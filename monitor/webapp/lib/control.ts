import { execFile, spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { getDashboardSnapshot, loadSites, type DashboardSnapshot } from "@/lib/status";

const execFileAsync = promisify(execFile);

export type EditableConfigDocument = {
  path: string;
  kind: "registry" | "monitor";
  raw: string;
};

export type DashboardActionRequest =
  | { action: "start-nginx" }
  | { action: "restart-nginx" }
  | { action: "reload-nginx" }
  | { action: "restart-webhook" }
  | { action: "restart-status-webapp" }
  | { action: "restart-site-service"; siteName: string }
  | { action: "retry-deploy"; siteName: string }
  | { action: "repair-site"; siteName: string }
  | {
      action: "add-site";
      repoUrl: string;
      branch: string;
      checkoutPath: string;
      email: string;
      skipGithubHook: boolean;
    };

export type DashboardActionResult = {
  action: DashboardActionRequest["action"];
  target: string | null;
  summary: string;
  output: string | null;
  finishedAt: string;
};

export type SiteDeploymentSettings = {
  siteName: string;
  repoUrl: string;
  webhookRepo: string;
  branch: string;
  checkoutPath: string;
};

export type GithubSecretRecord = {
  name: string;
  configured: boolean;
  presentInEnvFile: boolean;
  requiredByWorkflows: string[];
};

export type GithubSecretsDocument = {
  siteName: string | null;
  repo: string | null;
  checkoutPath: string;
  envFilePath: string;
  workflowFiles: string[];
  secrets: GithubSecretRecord[];
  fetchedAt: string;
};

export type GithubSecretMutationResult = {
  action: "set" | "delete";
  siteName: string | null;
  repo: string | null;
  name: string;
  summary: string;
  finishedAt: string;
};

export type DomainDnsRecord = {
  id: string;
  type: string;
  name: string;
  content: string;
  ttl: number;
  prio: number | null;
};

export type DomainRecordsDocument = {
  siteName: string | null;
  domain: string | null;
  provider: string;
  zone: string;
  records: DomainDnsRecord[];
  fetchedAt: string;
};

export type DomainRecordMutation = Omit<DomainDnsRecord, "id"> & {
  id?: string;
};

export type DomainRecordMutationResult = {
  action: "create" | "update" | "delete";
  siteName: string | null;
  domain: string | null;
  provider: string;
  zone: string;
  summary: string;
  finishedAt: string;
};

type JsonRecord = Record<string, unknown>;
type CommandOptions = {
  timeout?: number;
  env?: NodeJS.ProcessEnv;
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

async function defaultConfigPath(): Promise<string> {
  const configured = process.env.STATUS_CONFIG_PATH;
  if (configured) {
    return resolveRepoPath(configured);
  }

  const registryPath = resolveRepoPath("deploy/registry.json");
  if (await pathExists(registryPath)) {
    return registryPath;
  }

  return resolveRepoPath("monitor/websites.json");
}

async function readJsonFile(filePath: string): Promise<unknown> {
  const raw = await fs.readFile(filePath, "utf-8");
  return JSON.parse(raw) as unknown;
}

function deriveGithubRepoFullName(repoUrl: string): string {
  const trimmed = repoUrl.trim();
  if (!trimmed) {
    return "";
  }

  if (trimmed.startsWith("git@github.com:")) {
    return trimmed.replace(/^git@github\.com:/, "").replace(/\.git$/, "").trim();
  }

  try {
    const parsed = new URL(trimmed);
    if (parsed.hostname !== "github.com") {
      return "";
    }
    return parsed.pathname.replace(/^\/+/, "").replace(/\.git$/, "").trim();
  } catch {
    return "";
  }
}

function inferConfigKind(payload: unknown, configPath: string): EditableConfigDocument["kind"] {
  if (path.basename(configPath) === "registry.json") {
    return "registry";
  }

  if (Array.isArray(payload)) {
    const hasRegistryShape = payload.some((entry) => {
      const record = typeof entry === "object" && entry !== null ? (entry as JsonRecord) : null;
      return Boolean(record && ("deploy_config" in record || "repo_url" in record));
    });
    if (hasRegistryShape) {
      return "registry";
    }
  }

  return "monitor";
}

async function atomicWriteFile(filePath: string, raw: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tempPath = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`
  );
  await fs.writeFile(tempPath, raw, "utf-8");
  await fs.rename(tempPath, filePath);
}

function outputTail(stdout: string, stderr: string): string | null {
  const combined = [stdout.trim(), stderr.trim()].filter(Boolean).join("\n");
  if (!combined) {
    return null;
  }

  const lines = combined.split("\n");
  return lines.slice(-16).join("\n");
}

function readSiteName(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("A site name is required for this action.");
  }
  return value.trim();
}

function readRequiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} is required.`);
  }
  return value.trim();
}

function readRepoUrl(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("A non-empty repository URL is required.");
  }
  return value.trim();
}

function asRecord(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null ? (value as JsonRecord) : {};
}

async function findRegistryEntry(siteName: string): Promise<JsonRecord> {
  const configPath = await defaultConfigPath();
  const payload = await readJsonFile(configPath);
  if (!Array.isArray(payload)) {
    throw new Error("The active config file is not a JSON array.");
  }

  const match = payload.find((entry) => {
    const record = asRecord(entry);
    return typeof record.name === "string" && record.name.trim() === siteName;
  });
  if (!match) {
    throw new Error(`No config entry named '${siteName}' was found.`);
  }

  const record = asRecord(match);
  if (typeof record.repo_url !== "string" || !record.repo_url.trim()) {
    throw new Error(`'${siteName}' does not have deploy metadata in the active config.`);
  }
  return record;
}

async function runCommand(
  command: string,
  args: string[],
  successSummary: string,
  target: string | null,
  options: CommandOptions = {}
): Promise<DashboardActionResult> {
  try {
    const { stdout = "", stderr = "" } = await execFileAsync(command, args, {
      cwd: repoRoot(),
      timeout: options.timeout ?? 15 * 60 * 1000,
      maxBuffer: 1024 * 1024,
      env: options.env,
    });
    return {
      action: "reload-nginx",
      target,
      summary: successSummary,
      output: outputTail(stdout, stderr),
      finishedAt: new Date().toISOString(),
    };
  } catch (error) {
    const failure = error as NodeJS.ErrnoException & {
      stdout?: string;
      stderr?: string;
      code?: string | number;
    };
    const detail = outputTail(failure.stdout || "", failure.stderr || "");
    const reason =
      typeof failure.code === "number"
        ? `Command exited with status ${failure.code}.`
        : failure.message || "Command execution failed.";
    throw new Error(detail ? `${reason}\n${detail}` : reason);
  }
}

async function runTypedCommand(
  action: DashboardActionRequest["action"],
  command: string,
  args: string[],
  successSummary: string,
  target: string | null,
  options?: CommandOptions
): Promise<DashboardActionResult> {
  const result = await runCommand(command, args, successSummary, target, options);
  return {
    ...result,
    action,
  };
}

async function runProcess(
  command: string,
  args: string[],
  options: CommandOptions & { input?: string } = {}
): Promise<{ stdout: string; stderr: string }> {
  if (options.input === undefined) {
    const { stdout = "", stderr = "" } = await execFileAsync(command, args, {
      cwd: repoRoot(),
      timeout: options.timeout ?? 5 * 60 * 1000,
      maxBuffer: 1024 * 1024,
      env: options.env,
    });
    return { stdout, stderr };
  }

  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: repoRoot(),
      env: options.env,
      stdio: "pipe",
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timeoutMs = options.timeout ?? 5 * 60 * 1000;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr });
        return;
      }

      const failure = new Error(
        timedOut ? `Command timed out after ${timeoutMs}ms.` : `Command exited with status ${code ?? "unknown"}.`
      ) as Error & {
        stdout?: string;
        stderr?: string;
        code?: number | string;
      };
      failure.stdout = stdout;
      failure.stderr = stderr;
      failure.code = code ?? "spawn_failed";
      reject(failure);
    });

    child.stdin.end(options.input);
  });
}

async function runPythonJsonScript(
  scriptName: string,
  args: string[],
  options: CommandOptions & { input?: string } = {}
): Promise<JsonRecord> {
  try {
    const { stdout } = await runProcess(
      "python3",
      [resolveRepoPath(`scripts/${scriptName}`), ...args],
      options
    );
    const parsed = JSON.parse(stdout || "{}") as unknown;
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("Expected a JSON object response.");
    }
    return parsed as JsonRecord;
  } catch (error) {
    const failure = error as NodeJS.ErrnoException & {
      stdout?: string;
      stderr?: string;
      code?: string | number;
    };
    const detail = outputTail(failure.stdout || "", failure.stderr || "");
    const reason =
      typeof failure.code === "number"
        ? `Command exited with status ${failure.code}.`
        : failure.message || "Command execution failed.";
    throw new Error(detail ? `${reason}\n${detail}` : reason);
  }
}

async function runGithubJsonScript(
  args: string[],
  options: CommandOptions & { input?: string } = {}
): Promise<JsonRecord> {
  const registryPath = await resolveDeployRegistryPath();
  return runPythonJsonScript("manage_github_secrets.py", [...args, "--config", registryPath], {
    ...options,
    env: deployRegistryCommandEnv(registryPath, options.env),
  });
}

async function runDnsJsonScript(args: string[]): Promise<JsonRecord> {
  const configPath = await defaultConfigPath();
  return runPythonJsonScript("manage_dns_records.py", ["--registry", configPath, "--json", ...args], {
    env: {
      ...process.env,
      REGISTRY_PATH: configPath,
    },
  });
}

function parseGithubSecretsDocument(payload: JsonRecord): GithubSecretsDocument {
  const repo = typeof payload.repo === "string" && payload.repo.trim() ? payload.repo.trim() : null;
  const checkoutPath =
    typeof payload.checkoutPath === "string" && payload.checkoutPath.trim() ? payload.checkoutPath.trim() : "";
  const envFilePath =
    typeof payload.envFilePath === "string" && payload.envFilePath.trim() ? payload.envFilePath.trim() : "";
  if (!checkoutPath || !envFilePath) {
    throw new Error("Repository secret response did not include checkout or env-file metadata.");
  }

  const workflowFiles = Array.isArray(payload.workflowFiles)
    ? payload.workflowFiles.filter((entry): entry is string => typeof entry === "string" && Boolean(entry.trim()))
    : [];

  const secrets = Array.isArray(payload.secrets)
    ? payload.secrets
        .map((entry) => asRecord(entry))
        .filter((entry) => typeof entry.name === "string" && Boolean(entry.name.trim()))
        .map((entry) => ({
          name: String(entry.name).trim(),
          configured: entry.configured === true,
          presentInEnvFile: entry.presentInEnvFile === true,
          requiredByWorkflows: Array.isArray(entry.requiredByWorkflows)
            ? entry.requiredByWorkflows.filter(
                (item): item is string => typeof item === "string" && Boolean(item.trim())
              )
            : [],
        }))
    : [];

  return {
    siteName: typeof payload.siteName === "string" && payload.siteName.trim() ? payload.siteName.trim() : null,
    repo,
    checkoutPath,
    envFilePath,
    workflowFiles,
    secrets,
    fetchedAt: new Date().toISOString(),
  };
}

async function resolveDeployRegistryPath(): Promise<string> {
  const configPath = await defaultConfigPath();
  const payload = (await pathExists(configPath)) ? await readJsonFile(configPath) : [];
  const configKind = inferConfigKind(payload, configPath);

  if (configKind === "registry") {
    return configPath;
  }

  if (process.env.STATUS_CONFIG_PATH?.trim()) {
    throw new Error(
      "Website creation requires the deploy registry as the active status source. Point STATUS_CONFIG_PATH at deploy/registry.json or remove the override."
    );
  }

  return resolveRepoPath("deploy/registry.json");
}

function deployRegistryCommandEnv(
  registryPath: string,
  baseEnv: NodeJS.ProcessEnv = process.env
): NodeJS.ProcessEnv {
  return {
    ...baseEnv,
    REGISTRY_PATH: registryPath,
  };
}

export async function readEditableConfig(): Promise<EditableConfigDocument> {
  const configPath = await defaultConfigPath();
  const raw = (await pathExists(configPath)) ? await fs.readFile(configPath, "utf-8") : "[]\n";
  const parsed = JSON.parse(raw) as unknown;

  return {
    path: configPath,
    kind: inferConfigKind(parsed, configPath),
    raw,
  };
}

export async function saveEditableConfig(raw: string): Promise<EditableConfigDocument> {
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error("Config content cannot be empty.");
  }

  const configPath = await defaultConfigPath();
  const parsed = JSON.parse(raw) as unknown;
  if (!Array.isArray(parsed)) {
    throw new Error("Config must be a JSON array.");
  }

  const validationPath = path.join(os.tmpdir(), `status-webapp-config-${process.pid}-${Date.now()}.json`);
  await fs.writeFile(validationPath, raw, "utf-8");
  try {
    await loadSites(validationPath, null);
  } finally {
    await fs.unlink(validationPath).catch(() => undefined);
  }

  await atomicWriteFile(configPath, raw.endsWith("\n") ? raw : `${raw}\n`);
  return readEditableConfig();
}

function updateStringField(record: JsonRecord, key: string, value: string): void {
  const trimmed = value.trim();
  if (trimmed) {
    record[key] = trimmed;
  } else {
    delete record[key];
  }
}

export async function updateSiteDeploymentSettings(
  settings: SiteDeploymentSettings
): Promise<{ config: EditableConfigDocument; snapshot: DashboardSnapshot }> {
  const configPath = await defaultConfigPath();
  const payload = await readJsonFile(configPath);
  if (!Array.isArray(payload)) {
    throw new Error("The active config file is not a JSON array.");
  }
  if (inferConfigKind(payload, configPath) !== "registry") {
    throw new Error("Per-site deployment settings can only be edited when the active config is the deploy registry.");
  }

  let found = false;
  const nextPayload = payload.map((entry) => {
    const record = asRecord(entry);
    if (String(record.name || "").trim() !== settings.siteName) {
      return entry;
    }

    found = true;
    const nextRecord: JsonRecord = { ...record };
    updateStringField(nextRecord, "repo_url", settings.repoUrl);
    const webhookRepo = settings.webhookRepo.trim() || deriveGithubRepoFullName(settings.repoUrl);
    updateStringField(nextRecord, "webhook_repo", webhookRepo);
    updateStringField(nextRecord, "branch", settings.branch);
    updateStringField(nextRecord, "checkout_path", settings.checkoutPath);
    return nextRecord;
  });

  if (!found) {
    throw new Error(`No config entry named '${settings.siteName}' was found.`);
  }

  const raw = `${JSON.stringify(nextPayload, null, 2)}\n`;
  await saveEditableConfig(raw);
  return {
    config: await readEditableConfig(),
    snapshot: await getDashboardSnapshot(),
  };
}

export async function listGithubSecrets(siteName: string): Promise<GithubSecretsDocument> {
  const payload = await runGithubJsonScript(["list", "--site", readSiteName(siteName), "--json"]);
  return parseGithubSecretsDocument(payload);
}

export async function setGithubSecret(
  siteName: string,
  name: string,
  value: string
): Promise<{ document: GithubSecretsDocument; result: GithubSecretMutationResult }> {
  const trimmedSiteName = readSiteName(siteName);
  const trimmedName = readRequiredString(name, "Secret name");
  if (!value) {
    throw new Error("A non-empty secret value is required.");
  }

  const payload = await runGithubJsonScript(["set", trimmedName, "--site", trimmedSiteName, "--json"], {
    input: value,
  });
  const document = await listGithubSecrets(trimmedSiteName);
  return {
    document,
    result: {
      action: "set",
      siteName: typeof payload.siteName === "string" && payload.siteName.trim() ? payload.siteName.trim() : null,
      repo: typeof payload.repo === "string" ? payload.repo.trim() : document.repo,
      name: trimmedName,
      summary:
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : `Updated repository secret ${trimmedName}.`,
      finishedAt: new Date().toISOString(),
    },
  };
}

export async function deleteGithubSecret(
  siteName: string,
  name: string
): Promise<{ document: GithubSecretsDocument; result: GithubSecretMutationResult }> {
  const trimmedSiteName = readSiteName(siteName);
  const trimmedName = readRequiredString(name, "Secret name");

  const payload = await runGithubJsonScript(["delete", trimmedName, "--site", trimmedSiteName, "--json"]);
  const document = await listGithubSecrets(trimmedSiteName);
  return {
    document,
    result: {
      action: "delete",
      siteName: typeof payload.siteName === "string" && payload.siteName.trim() ? payload.siteName.trim() : null,
      repo: typeof payload.repo === "string" ? payload.repo.trim() : document.repo,
      name: trimmedName,
      summary:
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : `Deleted repository secret ${trimmedName}.`,
      finishedAt: new Date().toISOString(),
    },
  };
}

function parseDomainRecordsDocument(payload: JsonRecord): DomainRecordsDocument {
  const provider = typeof payload.provider === "string" && payload.provider.trim() ? payload.provider.trim() : "";
  const zone = typeof payload.zone === "string" && payload.zone.trim() ? payload.zone.trim() : "";
  if (!provider || !zone) {
    throw new Error("DNS response did not include provider and zone metadata.");
  }

  const records = Array.isArray(payload.records)
    ? payload.records
        .map((entry) => asRecord(entry))
        .filter((entry) => typeof entry.id === "string" && entry.id.trim())
        .map((entry) => ({
          id: String(entry.id).trim(),
          type: typeof entry.type === "string" ? entry.type.trim().toUpperCase() : "",
          name: typeof entry.name === "string" && entry.name.trim() ? entry.name.trim() : "@",
          content: typeof entry.content === "string" ? entry.content.trim() : "",
          ttl: typeof entry.ttl === "number" && Number.isFinite(entry.ttl) ? entry.ttl : 0,
          prio: typeof entry.prio === "number" && Number.isFinite(entry.prio) ? entry.prio : null,
        }))
    : [];

  return {
    siteName: typeof payload.siteName === "string" && payload.siteName.trim() ? payload.siteName.trim() : null,
    domain: typeof payload.domain === "string" && payload.domain.trim() ? payload.domain.trim() : null,
    provider,
    zone,
    records,
    fetchedAt: new Date().toISOString(),
  };
}

function normalizeDomainRecordMutation(record: DomainRecordMutation, requireId: boolean): DomainDnsRecord {
  const id = typeof record.id === "string" ? record.id.trim() : "";
  if (requireId && !id) {
    throw new Error("DNS record ID is required.");
  }

  const type = readRequiredString(record.type, "Record type").toUpperCase();
  const name = readRequiredString(record.name, "Record name");
  const content = readRequiredString(record.content, "Record content");
  const ttl = Number(record.ttl);
  if (!Number.isFinite(ttl) || ttl < 0) {
    throw new Error("TTL must be zero or greater.");
  }
  const prio = record.prio === null || record.prio === undefined || Number.isNaN(Number(record.prio))
    ? null
    : Number(record.prio);
  if (prio !== null && (!Number.isFinite(prio) || prio < 0)) {
    throw new Error("Priority must be zero or greater.");
  }

  return {
    id,
    type,
    name,
    content,
    ttl,
    prio,
  };
}

function domainRecordArgs(record: DomainDnsRecord, includeId: boolean): string[] {
  const args = [
    "--type",
    record.type,
    "--name",
    record.name,
    "--content",
    record.content,
    "--ttl",
    String(record.ttl),
  ];
  if (record.prio !== null) {
    args.push("--prio", String(record.prio));
  }
  if (includeId) {
    args.unshift("--id", record.id);
  }
  return args;
}

export async function listDomainRecords(siteName: string): Promise<DomainRecordsDocument> {
  const payload = await runDnsJsonScript(["list", "--site", readSiteName(siteName)]);
  return parseDomainRecordsDocument(payload);
}

export async function createDomainRecord(
  siteName: string,
  mutation: DomainRecordMutation
): Promise<{ document: DomainRecordsDocument; result: DomainRecordMutationResult }> {
  const trimmedSiteName = readSiteName(siteName);
  const record = normalizeDomainRecordMutation(mutation, false);
  const payload = await runDnsJsonScript([
    "create",
    "--site",
    trimmedSiteName,
    ...domainRecordArgs(record, false),
  ]);
  const document = parseDomainRecordsDocument(payload);
  return {
    document,
    result: {
      action: "create",
      siteName: document.siteName,
      domain: document.domain,
      provider: document.provider,
      zone: document.zone,
      summary:
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : `Created ${record.type} record ${record.name}.`,
      finishedAt: new Date().toISOString(),
    },
  };
}

export async function updateDomainRecord(
  siteName: string,
  mutation: DomainRecordMutation
): Promise<{ document: DomainRecordsDocument; result: DomainRecordMutationResult }> {
  const trimmedSiteName = readSiteName(siteName);
  const record = normalizeDomainRecordMutation(mutation, true);
  const payload = await runDnsJsonScript([
    "update",
    "--site",
    trimmedSiteName,
    ...domainRecordArgs(record, true),
  ]);
  const document = parseDomainRecordsDocument(payload);
  return {
    document,
    result: {
      action: "update",
      siteName: document.siteName,
      domain: document.domain,
      provider: document.provider,
      zone: document.zone,
      summary:
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : `Updated ${record.type} record ${record.name}.`,
      finishedAt: new Date().toISOString(),
    },
  };
}

export async function deleteDomainRecord(
  siteName: string,
  id: string
): Promise<{ document: DomainRecordsDocument; result: DomainRecordMutationResult }> {
  const trimmedSiteName = readSiteName(siteName);
  const trimmedId = readRequiredString(id, "Record ID");
  const payload = await runDnsJsonScript(["delete", "--site", trimmedSiteName, "--id", trimmedId]);
  const document = parseDomainRecordsDocument(payload);
  return {
    document,
    result: {
      action: "delete",
      siteName: document.siteName,
      domain: document.domain,
      provider: document.provider,
      zone: document.zone,
      summary:
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : `Deleted DNS record ${trimmedId}.`,
      finishedAt: new Date().toISOString(),
    },
  };
}

export async function runDashboardAction(
  request: DashboardActionRequest
): Promise<{ result: DashboardActionResult; snapshot: DashboardSnapshot }> {
  let result: DashboardActionResult;

  switch (request.action) {
    case "start-nginx":
      result = await runTypedCommand(
        "start-nginx",
        "systemctl",
        ["start", "nginx"],
        "Nginx start requested.",
        "nginx"
      );
      break;
    case "restart-nginx":
      result = await runTypedCommand(
        "restart-nginx",
        "systemctl",
        ["restart", "nginx"],
        "Nginx restart requested.",
        "nginx"
      );
      break;
    case "reload-nginx":
      result = await runTypedCommand(
        "reload-nginx",
        "systemctl",
        ["reload", "nginx"],
        "Nginx reload requested.",
        "nginx"
      );
      break;
    case "restart-webhook":
      result = await runTypedCommand(
        "restart-webhook",
        "systemctl",
        ["restart", "site-webhook-receiver.service"],
        "Webhook receiver restarted.",
        "site-webhook-receiver.service"
      );
      break;
    case "restart-status-webapp":
      result = await runTypedCommand(
        "restart-status-webapp",
        "systemctl",
        ["restart", "server-setup-status-webapp.service"],
        "Status webapp service restarted.",
        "server-setup-status-webapp.service"
      );
      break;
    case "restart-site-service": {
      const siteName = readSiteName(request.siteName);
      const sites = await loadSites();
      const site = sites.find((entry) => entry.name === siteName);
      if (!site?.serviceName) {
        throw new Error(`'${siteName}' does not have a managed service to restart.`);
      }
      result = await runTypedCommand(
        "restart-site-service",
        "systemctl",
        ["restart", site.serviceName],
        `${site.serviceName} restarted.`,
        site.serviceName
      );
      break;
    }
    case "retry-deploy": {
      const siteName = readSiteName(request.siteName);
      const registryPath = await resolveDeployRegistryPath();
      const entry = await findRegistryEntry(siteName);
      const branch = typeof entry.branch === "string" && entry.branch.trim() ? entry.branch.trim() : "main";
      const checkoutPath =
        typeof entry.checkout_path === "string" && entry.checkout_path.trim()
          ? entry.checkout_path.trim()
          : "";
      if (!checkoutPath) {
        throw new Error(`'${siteName}' is missing checkout_path in the active config.`);
      }

      result = await runTypedCommand(
        "retry-deploy",
        "python3",
        [
          resolveRepoPath("scripts/deploy_repo.py"),
          "--repo-url",
          String(entry.repo_url),
          "--dest",
          checkoutPath,
          "--branch",
          branch,
          "--skip-github-hook",
        ],
        `Deploy retry finished for ${siteName}.`,
        siteName,
        {
          timeout: 30 * 60 * 1000,
          env: deployRegistryCommandEnv(registryPath),
        }
      );
      break;
    }
    case "repair-site": {
      const siteName = readSiteName(request.siteName);
      const registryPath = await resolveDeployRegistryPath();

      result = await runTypedCommand(
        "repair-site",
        "python3",
        [
          resolveRepoPath("scripts/repair_site.py"),
          "--site",
          siteName,
          "--config",
          registryPath,
        ],
        `Repair finished for ${siteName}.`,
        siteName,
        {
          timeout: 30 * 60 * 1000,
          env: deployRegistryCommandEnv(registryPath),
        }
      );
      break;
    }
    case "add-site": {
      const repoUrl = readRepoUrl(request.repoUrl);
      const branch = request.branch.trim();
      const checkoutPath = request.checkoutPath.trim();
      const email = request.email.trim();
      const registryPath = await resolveDeployRegistryPath();

      const args = [resolveRepoPath("scripts/deploy_repo.py"), "--repo-url", repoUrl];
      if (checkoutPath) {
        args.push("--dest", checkoutPath);
      }
      if (branch) {
        args.push("--branch", branch);
      }
      if (email) {
        args.push("--email", email);
      }
      if (request.skipGithubHook) {
        args.push("--skip-github-hook");
      }

      result = await runTypedCommand(
        "add-site",
        "python3",
        args,
        "Website deployment finished.",
        repoUrl,
        {
          timeout: 30 * 60 * 1000,
          env: deployRegistryCommandEnv(registryPath),
        }
      );
      break;
    }
  }

  return {
    result,
    snapshot: await getDashboardSnapshot(),
  };
}
