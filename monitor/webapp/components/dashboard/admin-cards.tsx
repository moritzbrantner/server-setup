import type { ChangeEvent, FormEvent } from "react";

import type {
  DashboardActionRequest,
  DashboardActionResult,
  EditableConfigDocument,
} from "@/lib/control";

import { actionLabel, formatTimestamp, pillTone } from "./format";
import type { NewSiteDraft } from "./model";

type UnlockCardProps = {
  adminControlsEnabled: boolean;
  adminMessage: string | null;
  adminToken: string;
  adminUnlocked: boolean;
  resetAdminSession: () => void;
  setAdminToken: (token: string) => void;
  unlockAdminControls: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
};

export function UnlockCard({
  adminControlsEnabled,
  adminMessage,
  adminToken,
  adminUnlocked,
  resetAdminSession,
  setAdminToken,
  unlockAdminControls,
}: UnlockCardProps) {
  return (
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
  );
}

type QuickActionsCardProps = {
  actionResult: DashboardActionResult | null;
  adminUnlocked: boolean;
  busyActionKey: string | null;
  runAction: (action: DashboardActionRequest) => void | Promise<void>;
};

export function QuickActionsCard({
  actionResult,
  adminUnlocked,
  busyActionKey,
  runAction,
}: QuickActionsCardProps) {
  return (
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
          onClick={() => void runAction({ action: "start-nginx" })}
          type="button"
        >
          {busyActionKey === "start-nginx" ? "Starting..." : "Start Nginx"}
        </button>
        <button
          className="secondary-button"
          disabled={!adminUnlocked || busyActionKey !== null}
          onClick={() => void runAction({ action: "restart-nginx" })}
          type="button"
        >
          {busyActionKey === "restart-nginx" ? "Restarting..." : "Restart Nginx"}
        </button>
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
  );
}

type AddSiteCardProps = {
  adminUnlocked: boolean;
  busyActionKey: string | null;
  configDocument: EditableConfigDocument | null;
  createSite: () => void | Promise<void>;
  newSiteDraft: NewSiteDraft;
  setNewSiteSkipGithubHook: (checked: boolean) => void;
  updateNewSiteDraft: (field: keyof Omit<NewSiteDraft, "skipGithubHook">, value: string) => void;
};

export function AddSiteCard({
  adminUnlocked,
  busyActionKey,
  configDocument,
  createSite,
  newSiteDraft,
  setNewSiteSkipGithubHook,
  updateNewSiteDraft,
}: AddSiteCardProps) {
  return (
    <article className="admin-card">
      <div className="admin-card-head">
        <div>
          <h3>Add website</h3>
          <p>
            Deploy a new repository through the same `deploy-repo` workflow used on the shell.
          </p>
        </div>
      </div>
      <div className="token-form">
        <label className="token-field">
          <span>Repository URL</span>
          <input
            disabled={!adminUnlocked || busyActionKey !== null}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              updateNewSiteDraft("repoUrl", event.target.value)
            }
            placeholder="git@github.com:your-org/your-app.git"
            type="text"
            value={newSiteDraft.repoUrl}
          />
        </label>
        <label className="token-field">
          <span>Tracked branch</span>
          <input
            disabled={!adminUnlocked || busyActionKey !== null}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              updateNewSiteDraft("branch", event.target.value)
            }
            placeholder="main"
            type="text"
            value={newSiteDraft.branch}
          />
        </label>
        <label className="token-field">
          <span>Checkout path</span>
          <input
            disabled={!adminUnlocked || busyActionKey !== null}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              updateNewSiteDraft("checkoutPath", event.target.value)
            }
            placeholder="/srv/apps/your-app"
            type="text"
            value={newSiteDraft.checkoutPath}
          />
        </label>
        <label className="token-field">
          <span>TLS email override</span>
          <input
            disabled={!adminUnlocked || busyActionKey !== null}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              updateNewSiteDraft("email", event.target.value)
            }
            placeholder="ops@example.com"
            type="email"
            value={newSiteDraft.email}
          />
        </label>
        <label className="toggle-field">
          <input
            checked={newSiteDraft.skipGithubHook}
            disabled={!adminUnlocked || busyActionKey !== null}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              setNewSiteSkipGithubHook(event.target.checked)
            }
            type="checkbox"
          />
          <span>Skip GitHub webhook setup</span>
        </label>
        <div className="button-row">
          <button
            className="primary-button"
            disabled={!adminUnlocked || busyActionKey !== null || !newSiteDraft.repoUrl.trim()}
            onClick={() => void createSite()}
            type="button"
          >
            {busyActionKey === "add-site" ? "Deploying..." : "Deploy website"}
          </button>
        </div>
      </div>
      <p className="inline-note">
        The repository must already include a valid root `server.conf`. Blank branch, path, and
        email fields fall back to the existing deploy defaults.
      </p>
      {configDocument?.kind === "monitor" ? (
        <p className="inline-note">
          The first successful deployment will create `deploy/registry.json` and switch the
          dashboard over to the deploy registry.
        </p>
      ) : null}
    </article>
  );
}
