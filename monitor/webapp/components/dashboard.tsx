"use client";

import {
  startTransition,
  useEffect,
  useEffectEvent,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import type {
  DashboardActionRequest,
  DashboardActionResult,
  EditableConfigDocument,
} from "@/lib/control";
import type { DashboardSnapshot, StatusLevel } from "@/lib/status";

type DashboardProps = {
  initialSnapshot: DashboardSnapshot;
  adminControlsEnabled: boolean;
};

function formatMetric(value: number | null, suffix = ""): string {
  if (value === null || Number.isNaN(value)) {
    return "n/a";
  }

  return `${value}${suffix}`;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "n/a";
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }

  return `${timestamp.toLocaleString("en-US", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZone: "UTC",
  })} UTC`;
}

function formatStatus(value: string): string {
  return value.replace(/[-_]/g, " ");
}

function pillTone(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "ok" || normalized === "active" || normalized === "up") {
    return "pill pill-ok";
  }
  if (normalized === "warning") {
    return "pill pill-warn";
  }
  if (normalized === "unknown" || normalized === "n/a" || normalized === "not managed") {
    return "pill pill-muted";
  }
  return "pill pill-bad";
}

function countAlerts(snapshot: DashboardSnapshot, level: "critical" | "warning"): number {
  return snapshot.alerts.filter((alert) => alert.level === level).length;
}

function setupOverview(status: StatusLevel): string {
  if (status === "critical") {
    return "Needs attention";
  }
  if (status === "warning") {
    return "Warnings present";
  }
  if (status === "ok") {
    return "Healthy";
  }
  return "Unknown";
}

function adminHeaders(adminToken: string): HeadersInit {
  return {
    "content-type": "application/json",
    "x-status-admin-token": adminToken,
  };
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    if (typeof body.error === "string" && body.error.trim()) {
      return body.error;
    }
  } catch {
    // Ignore JSON parsing errors and use the generic fallback below.
  }

  return `Request failed with ${response.status}.`;
}

function actionLabel(action: DashboardActionRequest["action"]): string {
  switch (action) {
    case "reload-nginx":
      return "Reload Nginx";
    case "restart-webhook":
      return "Restart webhook receiver";
    case "restart-status-webapp":
      return "Restart status webapp";
    case "restart-site-service":
      return "Restart service";
    case "retry-deploy":
      return "Retry deploy";
  }
}

function actionKey(action: DashboardActionRequest): string {
  if ("siteName" in action) {
    return `${action.action}:${action.siteName}`;
  }
  return action.action;
}

function configModeLabel(kind: EditableConfigDocument["kind"]): string {
  return kind === "registry" ? "deploy registry" : "monitor list";
}

export function Dashboard({ initialSnapshot, adminControlsEnabled }: DashboardProps) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [error, setError] = useState<string | null>(null);
  const [configDocument, setConfigDocument] = useState<EditableConfigDocument | null>(null);
  const [configDraft, setConfigDraft] = useState("");
  const [configMessage, setConfigMessage] = useState<string | null>(null);
  const [adminMessage, setAdminMessage] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<DashboardActionResult | null>(null);
  const [adminToken, setAdminToken] = useState("");
  const [adminUnlocked, setAdminUnlocked] = useState(false);
  const [busyActionKey, setBusyActionKey] = useState<string | null>(null);
  const [configBusy, setConfigBusy] = useState(false);

  const refreshSnapshot = useEffectEvent(async () => {
    try {
      const response = await fetch("/api/overview", {
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`Refresh failed with ${response.status}`);
      }

      const nextSnapshot = (await response.json()) as DashboardSnapshot;
      startTransition(() => {
        setSnapshot(nextSnapshot);
        setError(null);
      });
    } catch (refreshError) {
      const message =
        refreshError instanceof Error ? refreshError.message : "Unable to refresh status.";
      startTransition(() => {
        setError(message);
      });
    }
  });

  const loadConfig = useEffectEvent(async (token: string) => {
    try {
      const response = await fetch("/api/config", {
        cache: "no-store",
        headers: adminHeaders(token),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const nextConfig = (await response.json()) as EditableConfigDocument;
      startTransition(() => {
        setConfigDocument(nextConfig);
        setConfigDraft(nextConfig.raw);
        setConfigMessage(null);
        setAdminMessage(null);
        setAdminUnlocked(true);
      });
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : "Unable to load the active config.";
      startTransition(() => {
        setAdminUnlocked(false);
        setAdminMessage(message);
      });
    }
  });

  useEffect(() => {
    const storedToken = window.localStorage.getItem("status-webapp-admin-token") || "";
    if (!storedToken) {
      return;
    }

    setAdminToken(storedToken);
    void loadConfig(storedToken);
  }, [loadConfig]);

  useEffect(() => {
    const intervalId = setInterval(() => {
      void refreshSnapshot();
    }, 30000);

    return () => clearInterval(intervalId);
  }, [refreshSnapshot]);

  const unlockAdminControls = useEffectEvent(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = adminToken.trim();
    if (!trimmed) {
      setAdminMessage("Enter the admin token configured for the status webapp.");
      return;
    }

    window.localStorage.setItem("status-webapp-admin-token", trimmed);
    await loadConfig(trimmed);
  });

  const saveConfig = useEffectEvent(async () => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }

    setConfigBusy(true);
    try {
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({ raw: configDraft }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        config: EditableConfigDocument;
        snapshot: DashboardSnapshot;
      };
      startTransition(() => {
        setConfigDocument(payload.config);
        setConfigDraft(payload.config.raw);
        setSnapshot(payload.snapshot);
        setConfigMessage(`Saved ${payload.config.path} and refreshed the dashboard.`);
        setAdminMessage(null);
        setError(null);
      });
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "Unable to save the active config.";
      startTransition(() => {
        setConfigMessage(message);
      });
    } finally {
      setConfigBusy(false);
    }
  });

  const runAction = useEffectEvent(async (action: DashboardActionRequest) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }

    const currentActionKey = actionKey(action);
    setBusyActionKey(currentActionKey);
    try {
      const response = await fetch("/api/actions", {
        method: "POST",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify(action),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        result: DashboardActionResult;
        snapshot: DashboardSnapshot;
      };
      startTransition(() => {
        setSnapshot(payload.snapshot);
        setActionResult(payload.result);
        setAdminMessage(null);
        setError(null);
      });
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "Unable to execute the requested action.";
      startTransition(() => {
        setAdminMessage(message);
      });
    } finally {
      setBusyActionKey(null);
    }
  });

  const discardConfigChanges = useEffectEvent(() => {
    if (!configDocument) {
      return;
    }
    setConfigDraft(configDocument.raw);
    setConfigMessage("Local edits discarded.");
  });

  const resetAdminSession = useEffectEvent(() => {
    window.localStorage.removeItem("status-webapp-admin-token");
    setAdminToken("");
    setAdminUnlocked(false);
    setConfigDocument(null);
    setConfigDraft("");
    setConfigMessage(null);
    setActionResult(null);
    setAdminMessage("Admin token cleared from this browser session.");
  });

  const configDirty = configDocument !== null && configDraft !== configDocument.raw;

  return (
    <main className="shell">
      <section className="hero panel">
        <div>
          <p className="eyebrow">Server Setup Monitor</p>
          <h1>Setup health and live application status</h1>
          <p className="lead">
            Public-safe checks cover deploy automation, TLS, hardening, host services, and per-site
            reachability so failures are visible without opening a shell.
          </p>
        </div>
        <div className="hero-meta">
          <div className="meta-block">
            <span>Updated</span>
            <strong>{formatTimestamp(snapshot.generatedAt)}</strong>
          </div>
          <div className="meta-block">
            <span>Host</span>
            <strong>{snapshot.system.hostname}</strong>
          </div>
          <div className="meta-block">
            <span>Setup</span>
            <strong>{setupOverview(snapshot.setup.overallStatus)}</strong>
          </div>
          <div className="meta-actions">
            <button className="ghost-button" onClick={() => void refreshSnapshot()} type="button">
              Refresh now
            </button>
          </div>
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      </section>

      <section className="summary-grid">
        <article className="panel stat-card">
          <span>Total sites</span>
          <strong>{snapshot.summary.totalSites}</strong>
        </article>
        <article className="panel stat-card">
          <span>Healthy</span>
          <strong>{snapshot.summary.healthySites}</strong>
        </article>
        <article className="panel stat-card">
          <span>Critical issues</span>
          <strong>{countAlerts(snapshot, "critical")}</strong>
        </article>
        <article className="panel stat-card">
          <span>Warnings</span>
          <strong>{countAlerts(snapshot, "warning")}</strong>
        </article>
        <article className="panel stat-card">
          <span>Services active</span>
          <strong>
            {snapshot.summary.activeServices}/{snapshot.summary.managedServices}
          </strong>
        </article>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Config and recovery controls</h2>
          </div>
        </div>
        <div className="panel-content admin-grid">
          <article className="admin-card">
            <div className="admin-card-head">
              <div>
                <h3>Unlock admin controls</h3>
                <p>
                  Config reads, saves, reloads, and deploy retries require the shared admin token
                  from the status webapp environment.
                </p>
              </div>
              <mark className={pillTone(adminUnlocked ? "ok" : "unknown")}>
                {adminUnlocked ? "unlocked" : "locked"}
              </mark>
            </div>
            <form className="token-form" onSubmit={(event) => void unlockAdminControls(event)}>
              <label className="token-field">
                <span>Admin token</span>
                <input
                  autoComplete="off"
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setAdminToken(event.target.value)}
                  placeholder={adminControlsEnabled ? "Paste STATUS_WEBAPP_ADMIN_TOKEN" : "Token not configured yet"}
                  type="password"
                  value={adminToken}
                />
              </label>
              <div className="button-row">
                <button className="primary-button" type="submit">
                  Unlock
                </button>
                <button className="ghost-button" onClick={() => void resetAdminSession()} type="button">
                  Clear token
                </button>
              </div>
            </form>
            {!adminControlsEnabled ? (
              <p className="inline-note">
                `STATUS_WEBAPP_ADMIN_TOKEN` is not configured in the webapp environment yet.
              </p>
            ) : null}
            {adminMessage ? <p className="error-text">{adminMessage}</p> : null}
          </article>

          <article className="admin-card">
            <div className="admin-card-head">
              <div>
                <h3>Quick actions</h3>
                <p>Run the supported recovery commands without leaving the dashboard.</p>
              </div>
            </div>
            <div className="button-grid">
              <button
                className="secondary-button"
                disabled={!adminUnlocked || busyActionKey !== null}
                onClick={() => void runAction({ action: "reload-nginx" })}
                type="button"
              >
                {busyActionKey === "reload-nginx" ? "Reloading..." : "Reload Nginx"}
              </button>
              <button
                className="secondary-button"
                disabled={!adminUnlocked || busyActionKey !== null}
                onClick={() => void runAction({ action: "restart-webhook" })}
                type="button"
              >
                {busyActionKey === "restart-webhook" ? "Restarting..." : "Restart webhook"}
              </button>
              <button
                className="secondary-button"
                disabled={!adminUnlocked || busyActionKey !== null}
                onClick={() => void runAction({ action: "restart-status-webapp" })}
                type="button"
              >
                {busyActionKey === "restart-status-webapp" ? "Restarting..." : "Restart webapp"}
              </button>
            </div>
            {actionResult ? (
              <div className="action-result">
                <p>
                  <strong>{actionLabel(actionResult.action)}</strong>
                  {actionResult.target ? ` · ${actionResult.target}` : ""} ·{" "}
                  {formatTimestamp(actionResult.finishedAt)}
                </p>
                <p>{actionResult.summary}</p>
                {actionResult.output ? <pre>{actionResult.output}</pre> : null}
              </div>
            ) : null}
          </article>
        </div>
        <div className="panel-content">
          <article className="config-card">
            <div className="admin-card-head">
              <div>
                <p className="eyebrow">Configuration</p>
                <h3>Active config file</h3>
                <p>
                  Edit the raw JSON that powers the current status source, then save and refresh the
                  dashboard in one step.
                </p>
              </div>
              <mark className={pillTone(configDocument?.kind || "unknown")}>
                {configDocument ? configModeLabel(configDocument.kind) : "locked"}
              </mark>
            </div>
            <div className="config-meta">
              <span>Path</span>
              <strong>{configDocument?.path || "Unlock admin controls to read the config file."}</strong>
            </div>
            <textarea
              className="config-editor"
              disabled={!adminUnlocked}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setConfigDraft(event.target.value)}
              spellCheck={false}
              value={configDraft}
            />
            <div className="button-row">
              <button
                className="primary-button"
                disabled={!adminUnlocked || configBusy || !configDirty}
                onClick={() => void saveConfig()}
                type="button"
              >
                {configBusy ? "Saving..." : "Save config"}
              </button>
              <button
                className="ghost-button"
                disabled={!adminUnlocked || configBusy}
                onClick={() => void discardConfigChanges()}
                type="button"
              >
                Discard edits
              </button>
              <button
                className="ghost-button"
                disabled={!adminUnlocked || configBusy}
                onClick={() => void loadConfig(adminToken.trim())}
                type="button"
              >
                Reload from disk
              </button>
            </div>
            {configMessage ? <p className="inline-note">{configMessage}</p> : null}
          </article>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Important issues</p>
            <h2>What needs attention</h2>
          </div>
        </div>
        <div className="alert-list">
          {snapshot.alerts.length > 0 ? (
            snapshot.alerts.map((alert) => (
              <article className={`alert-card alert-${alert.level}`} key={alert.id}>
                <div className="alert-head">
                  <div>
                    <h3>{alert.title}</h3>
                    <p>{alert.summary}</p>
                  </div>
                  <mark className={pillTone(alert.level)}>{alert.level}</mark>
                </div>
              </article>
            ))
          ) : (
            <article className="alert-card alert-ok">
              <div className="alert-head">
                <div>
                  <h3>No current issues</h3>
                  <p>Setup checks and application checks did not report any warnings or failures.</p>
                </div>
                <mark className={pillTone("ok")}>ok</mark>
              </div>
            </article>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Setup health</p>
            <h2>Bootstrap and operations signals</h2>
          </div>
        </div>
        <div className="setup-grid">
          {snapshot.setup.categories.map((category) => (
            <article className="setup-card" key={category.id}>
              <div className="setup-head">
                <div>
                  <h3>{category.label}</h3>
                  <p>{category.checks.length} checks</p>
                </div>
                <mark className={pillTone(category.status)}>{formatStatus(category.status)}</mark>
              </div>
              <div className="setup-checks">
                {category.checks.map((check) => (
                  <div className="setup-check" key={check.id}>
                    <div className="setup-check-head">
                      <strong>{check.label}</strong>
                      <mark className={pillTone(check.status)}>{formatStatus(check.status)}</mark>
                    </div>
                    <p>{check.summary}</p>
                    {check.detail ? <span>{check.detail}</span> : null}
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Host</p>
            <h2>Server health</h2>
          </div>
        </div>
        <div className="host-grid">
          <article className="metric-card">
            <span>Load</span>
            <strong>
              {formatMetric(snapshot.system.load.oneMinute)} /{" "}
              {formatMetric(snapshot.system.load.fiveMinutes)} /{" "}
              {formatMetric(snapshot.system.load.fifteenMinutes)}
            </strong>
            <p>1m / 5m / 15m</p>
          </article>
          <article className="metric-card">
            <span>Disk</span>
            <strong>
              {formatMetric(snapshot.system.disk.usedGb, " GB")} /{" "}
              {formatMetric(snapshot.system.disk.totalGb, " GB")}
            </strong>
            <p>{formatMetric(snapshot.system.disk.usedPercent, "%")} used</p>
          </article>
          <article className="metric-card">
            <span>Memory</span>
            <strong>
              {formatMetric(snapshot.system.memory.usedMb, " MB")} /{" "}
              {formatMetric(snapshot.system.memory.totalMb, " MB")}
            </strong>
            <p>{formatMetric(snapshot.system.memory.usedPercent, "%")} used</p>
          </article>
          <article className="metric-card">
            <span>Core services</span>
            <strong>
              nginx <mark className={pillTone(snapshot.system.services.nginx)}>{snapshot.system.services.nginx}</mark>
            </strong>
            <p>
              docker{" "}
              <mark className={pillTone(snapshot.system.services.docker)}>
                {snapshot.system.services.docker}
              </mark>
            </p>
          </article>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Applications</p>
            <h2>Deployed sites</h2>
          </div>
        </div>
        <div className="site-grid">
          {snapshot.applications.map((application) => (
            <article className="site-card" key={application.name}>
              <div className="site-head">
                <div>
                  <h3>{application.name}</h3>
                  <p>{application.url}</p>
                </div>
                <mark className={pillTone(application.ok ? "up" : "down")}>
                  {application.ok ? "up" : "down"}
                </mark>
              </div>

              <dl className="site-details">
                <div>
                  <dt>Runtime</dt>
                  <dd>{application.runtimeMode}</dd>
                </div>
                <div>
                  <dt>HTTP</dt>
                  <dd>
                    {application.statusCode ?? "n/a"} in {formatMetric(application.latencyMs, " ms")}
                  </dd>
                </div>
                <div>
                  <dt>Deploy</dt>
                  <dd>{application.deploy.last_deploy_status || "unknown"}</dd>
                </div>
                <div>
                  <dt>Service</dt>
                  <dd>
                    {application.serviceName
                      ? `${application.serviceName} (${application.serviceStatus || "unknown"})`
                      : "not managed"}
                  </dd>
                </div>
                <div>
                  <dt>Health</dt>
                  <dd>{application.deploy.last_health_check?.status || "n/a"}</dd>
                </div>
                <div>
                  <dt>Updated</dt>
                  <dd>{formatTimestamp(application.lastDeployTimestamp)}</dd>
                </div>
                <div>
                  <dt>Release</dt>
                  <dd>{application.deploy.current_release || "n/a"}</dd>
                </div>
                <div>
                  <dt>Health detail</dt>
                  <dd>{application.lastHealthMessage || "n/a"}</dd>
                </div>
              </dl>

              <div className="button-row site-actions">
                <button
                  className="ghost-button"
                  disabled={!adminUnlocked || !application.serviceName || busyActionKey !== null}
                  onClick={() =>
                    void runAction({ action: "restart-site-service", siteName: application.name })
                  }
                  type="button"
                >
                  {busyActionKey === actionKey({ action: "restart-site-service", siteName: application.name })
                    ? "Restarting..."
                    : "Restart service"}
                </button>
                <button
                  className="ghost-button"
                  disabled={!adminUnlocked || !application.repoUrl || busyActionKey !== null}
                  onClick={() => void runAction({ action: "retry-deploy", siteName: application.name })}
                  type="button"
                >
                  {busyActionKey === actionKey({ action: "retry-deploy", siteName: application.name })
                    ? "Retrying..."
                    : "Retry deploy"}
                </button>
              </div>

              {application.lastFailureReason ? (
                <p className="site-error">Last failure: {application.lastFailureReason}</p>
              ) : null}
              {application.error ? <p className="site-error">HTTP error: {application.error}</p> : null}
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
