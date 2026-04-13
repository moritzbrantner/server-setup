"use client";

import { startTransition, useEffect, useEffectEvent, useState } from "react";

import type { DashboardSnapshot, StatusLevel } from "@/lib/status";

type DashboardProps = {
  initialSnapshot: DashboardSnapshot;
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

export function Dashboard({ initialSnapshot }: DashboardProps) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [error, setError] = useState<string | null>(null);

  const refresh = useEffectEvent(async () => {
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

  useEffect(() => {
    const intervalId = setInterval(() => {
      void refresh();
    }, 30000);

    return () => clearInterval(intervalId);
  }, []);

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
                    {application.serviceName ? `${application.serviceName} (${application.serviceStatus || "unknown"})` : "not managed"}
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
