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
  updatedAt: string | null;
  visibility: string | null;
  numSelectedRepos: number | null;
};

export type GithubSecretsDocument = {
  siteName: string | null;
  repo: string;
  secrets: GithubSecretRecord[];
  fetchedAt: string;
};

export type GithubSecretMutationResult = {
  action: "set" | "delete";
  siteName: string | null;
  repo: string;
  name: string;
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
      ) as NodeJS.ErrnoException & {
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

async function runJsonScript(
  args: string[],
  options: CommandOptions & { input?: string } = {}
): Promise<JsonRecord> {
  try {
    const { stdout } = await runProcess(
      "python3",
      [resolveRepoPath("scripts/manage_github_secrets.py"), ...args],
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

function parseGithubSecretsDocument(payload: JsonRecord): GithubSecretsDocument {
  const repo = typeof payload.repo === "string" ? payload.repo.trim() : "";
  if (!repo) {
    throw new Error("GitHub secret response did not include a repository name.");
  }

  const secrets = Array.isArray(payload.secrets)
    ? payload.secrets
        .map((entry) => asRecord(entry))
        .filter((entry) => typeof entry.name === "string" && entry.name.trim())
        .map((entry) => ({
          name: String(entry.name).trim(),
          updatedAt: typeof entry.updatedAt === "string" && entry.updatedAt.trim() ? entry.updatedAt.trim() : null,
          visibility: typeof entry.visibility === "string" && entry.visibility.trim() ? entry.visibility.trim() : null,
          numSelectedRepos:
            typeof entry.numSelectedRepos === "number" && Number.isFinite(entry.numSelectedRepos)
              ? entry.numSelectedRepos
              : null,
        }))
    : [];

  return {
    siteName: typeof payload.siteName === "string" && payload.siteName.trim() ? payload.siteName.trim() : null,
    repo,
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
  const payload = await runJsonScript(["list", "--site", readSiteName(siteName), "--json"]);
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

  const payload = await runJsonScript(["set", trimmedName, "--site", trimmedSiteName, "--json"], {
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
          : `Updated GitHub secret ${trimmedName}.`,
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

  const payload = await runJsonScript(["delete", trimmedName, "--site", trimmedSiteName, "--json"]);
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
          : `Deleted GitHub secret ${trimmedName}.`,
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
        siteName
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
          env: {
            ...process.env,
            REGISTRY_PATH: registryPath,
          },
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
