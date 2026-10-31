import { memo } from "react";

import type { DashboardSnapshot } from "@/lib/status";

import { countAlerts, formatMetric, formatStatus, formatTimestamp, pillTone, setupOverview } from "./format";

type HeroSectionProps = {
  snapshot: DashboardSnapshot;
  error: string | null;
  refreshSnapshot: () => void | Promise<void>;
};

export function HeroSection({ snapshot, error, refreshSnapshot }: HeroSectionProps) {
  return (
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
  );
}

export const SummaryGrid = memo(function SummaryGrid({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
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
  );
});

export const AlertsSection = memo(function AlertsSection({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
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
  );
});

export const SetupHealthSection = memo(function SetupHealthSection({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
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
  );
});

export const HostHealthSection = memo(function HostHealthSection({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
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
  );
});
