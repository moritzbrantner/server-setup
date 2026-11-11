import type { ChangeEvent } from "react";

import type {
  DashboardActionRequest,
  EditableConfigDocument,
  SiteDeploymentSettings,
} from "@/lib/control";
import type { SiteCheck } from "@/lib/status";

import { actionKey, formatMetric, formatStatus, formatTimestamp, pillTone, pushDeployTone } from "./format";

type SiteControlsProps = {
  adminUnlocked: boolean;
  busyActionKey: string | null;
  configDocument: EditableConfigDocument | null;
  runAction: (action: DashboardActionRequest) => void | Promise<void>;
  saveSiteSettings: (siteName: string) => void | Promise<void>;
  siteDrafts: Record<string, SiteDeploymentSettings>;
  siteSaveKey: string | null;
  updateSiteDraft: (
    siteName: string,
    field: keyof Omit<SiteDeploymentSettings, "siteName">,
    value: string
  ) => void;
};

function SiteCard({
  adminUnlocked,
  application,
  busyActionKey,
  configDocument,
  runAction,
  saveSiteSettings,
  siteDrafts,
  siteSaveKey,
  updateSiteDraft,
}: SiteControlsProps & { application: SiteCheck }) {
  return (
    <article className="site-card">
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

      <div className="deploy-panel">
        <div className="deploy-panel-head">
          <div>
            <p className="eyebrow">Push to Main</p>
            <h4>{application.pushDeploy?.summary || "Push deploy readiness unavailable."}</h4>
          </div>
          <mark className={pushDeployTone(application)}>
            {application.pushDeploy ? formatStatus(application.pushDeploy.status) : "unknown"}
          </mark>
        </div>
        {application.pushDeploy?.issues && application.pushDeploy.issues.length > 0 ? (
          <ul className="issue-list">
            {application.pushDeploy.issues.map((issue) => (
              <li key={`${application.name}-${issue}`}>{issue}</li>
            ))}
          </ul>
        ) : (
          <p className="inline-note">Repository, branch, webhook filters, and host automation look ready.</p>
        )}
        {adminUnlocked && configDocument?.kind === "registry" ? (
          <div className="deploy-form">
            <label className="token-field">
              <span>Repository URL</span>
              <input
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateSiteDraft(application.name, "repoUrl", event.target.value)
                }
                type="text"
                value={siteDrafts[application.name]?.repoUrl || ""}
              />
            </label>
            <label className="token-field">
              <span>Webhook repo</span>
              <input
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateSiteDraft(application.name, "webhookRepo", event.target.value)
                }
                type="text"
                value={siteDrafts[application.name]?.webhookRepo || ""}
              />
            </label>
            <label className="token-field">
              <span>Tracked branch</span>
              <input
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateSiteDraft(application.name, "branch", event.target.value)
                }
                type="text"
                value={siteDrafts[application.name]?.branch || ""}
              />
            </label>
            <label className="token-field">
              <span>Checkout path</span>
              <input
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateSiteDraft(application.name, "checkoutPath", event.target.value)
                }
                type="text"
                value={siteDrafts[application.name]?.checkoutPath || ""}
              />
            </label>
            <div className="button-row">
              <button
                className="secondary-button"
                disabled={siteSaveKey !== null}
                onClick={() => void saveSiteSettings(application.name)}
                type="button"
              >
                {siteSaveKey === application.name ? "Saving..." : "Save push settings"}
              </button>
            </div>
          </div>
        ) : adminUnlocked ? (
          <p className="inline-note">
            Per-site push settings can only be edited when the active status source is the deploy registry.
          </p>
        ) : null}
      </div>

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
        <button
          className="ghost-button"
          disabled={!adminUnlocked || !application.repoUrl || busyActionKey !== null}
          onClick={() => void runAction({ action: "repair-site", siteName: application.name })}
          type="button"
        >
          {busyActionKey === actionKey({ action: "repair-site", siteName: application.name })
            ? "Repairing..."
            : "Repair site"}
        </button>
      </div>

      {application.lastFailureReason ? (
        <p className="site-error">Last failure: {application.lastFailureReason}</p>
      ) : null}
      {application.error ? <p className="site-error">HTTP error: {application.error}</p> : null}
    </article>
  );
}

export function ApplicationsSection({
  applications,
  ...siteControls
}: SiteControlsProps & { applications: SiteCheck[] }) {
  return (
    <section className="panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Applications</p>
          <h2>Deployed sites</h2>
        </div>
      </div>
      <div className="site-grid">
          {applications.map((application) => (
            <SiteCard application={application} key={application.name} {...siteControls} />
          ))}
      </div>
    </section>
  );
}
