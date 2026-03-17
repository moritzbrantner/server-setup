"use client";

import { startTransition, useEffect, useEffectEvent, useState } from "react";

import type { DashboardSnapshot } from "@/lib/status";

type DashboardProps = {
  initialSnapshot: DashboardSnapshot;
};

function formatMetric(value: number | null, suffix = ""): string {
  if (value === null || Number.isNaN(value)) {
    return "n/a";
  }

  return `${value}${suffix}`;
}

function formatUpdatedAt(value: string): string {
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

function pillTone(ok: boolean): string {
  return ok ? "pill pill-ok" : "pill pill-bad";
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
          <h1>Current server and application status</h1>
          <p className="lead">
            Live checks are pulled from the deployed sites, persisted deploy-state JSON, and the
            local systemd host.
          </p>
        </div>
        <div className="hero-meta">
          <div className="meta-block">
            <span>Updated</span>
            <strong>{formatUpdatedAt(snapshot.generatedAt)}</strong>
          </div>
          <div className="meta-block">
            <span>Host</span>
            <strong>{snapshot.system.hostname}</strong>
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
          <span>Degraded</span>
          <strong>{snapshot.summary.degradedSites}</strong>
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
              nginx <mark className={pillTone(snapshot.system.services.nginx === "active")}>
                {snapshot.system.services.nginx}
              </mark>
            </strong>
            <p>
              docker{" "}
              <mark className={pillTone(snapshot.system.services.docker === "active")}>
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
                <mark className={pillTone(application.ok)}>{application.ok ? "up" : "down"}</mark>
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
                  <dd>{application.serviceStatus ?? "not managed"}</dd>
                </div>
                <div>
                  <dt>Health</dt>
                  <dd>{application.deploy.last_health_check?.status || "n/a"}</dd>
                </div>
                <div>
                  <dt>Release</dt>
                  <dd>{application.deploy.current_release || "n/a"}</dd>
                </div>
              </dl>

              {application.error ? <p className="site-error">{application.error}</p> : null}
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
