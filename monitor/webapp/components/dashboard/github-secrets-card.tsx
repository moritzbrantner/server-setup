import type { ChangeEvent } from "react";

import { pillTone } from "./format";
import type { useGithubSecrets } from "./use-github-secrets";

type GithubSecretsCardProps = ReturnType<typeof useGithubSecrets> & {
  adminUnlocked: boolean;
};

export function GithubSecretsCard({
  adminUnlocked,
  githubSecretDraft,
  githubSecretSiteOptions,
  githubSecretsBusy,
  githubSecretsDocument,
  githubSecretsMessage,
  githubSecretsSiteName,
  loadGithubSecrets,
  removeGithubSecret,
  saveGithubSecret,
  selectedGithubSecretSite,
  setGithubSecretsDocument,
  setGithubSecretsMessage,
  setGithubSecretsSiteName,
  updateGithubSecretDraft,
}: GithubSecretsCardProps) {
  return (
    <article className="admin-card">
      <div className="admin-card-head">
        <div>
          <h3>Repository secrets</h3>
          <p>Scan GitHub workflow files for required secrets and store the values in the repo env file.</p>
        </div>
      </div>
      {githubSecretSiteOptions.length > 0 ? (
        <>
          <div className="token-form">
            <label className="token-field">
              <span>Managed site</span>
              <select
                disabled={!adminUnlocked || githubSecretsBusy}
                onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                  setGithubSecretsSiteName(event.target.value);
                  setGithubSecretsDocument(null);
                  setGithubSecretsMessage(null);
                }}
                value={githubSecretsSiteName}
              >
                {githubSecretSiteOptions.map((option) => (
                  <option key={option.siteName} value={option.siteName}>
                    {option.siteName}
                  </option>
                ))}
              </select>
            </label>
            <div className="config-meta">
              <span>Repository</span>
              <strong>{githubSecretsDocument?.repo || selectedGithubSecretSite?.repoLabel || "n/a"}</strong>
            </div>
            <div className="config-meta">
              <span>Env file</span>
              <strong>{githubSecretsDocument?.envFilePath || "Loading..."}</strong>
            </div>
            <label className="token-field">
              <span>Secret name</span>
              <input
                disabled={!adminUnlocked || githubSecretsBusy}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateGithubSecretDraft("name", event.target.value)
                }
                placeholder="NEXT_PUBLIC_API_URL"
                type="text"
                value={githubSecretDraft.name}
              />
            </label>
            <label className="token-field">
              <span>Secret value</span>
              <input
                autoComplete="off"
                disabled={!adminUnlocked || githubSecretsBusy}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateGithubSecretDraft("value", event.target.value)
                }
                placeholder="Paste the secret value"
                type="password"
                value={githubSecretDraft.value}
              />
            </label>
            <div className="button-row">
              <button
                className="primary-button"
                disabled={
                  !adminUnlocked ||
                  githubSecretsBusy ||
                  !githubSecretsSiteName ||
                  !githubSecretDraft.name.trim() ||
                  !githubSecretDraft.value
                }
                onClick={() => void saveGithubSecret()}
                type="button"
              >
                {githubSecretsBusy ? "Saving..." : "Save secret"}
              </button>
              <button
                className="ghost-button"
                disabled={!adminUnlocked || githubSecretsBusy || !githubSecretsSiteName}
                onClick={() => void loadGithubSecrets(githubSecretsSiteName)}
                type="button"
              >
                {githubSecretsBusy ? "Refreshing..." : "Refresh secrets"}
              </button>
            </div>
          </div>
          <p className="inline-note">
            Workflow references are discovered from `.github/workflows/*.yml` and
            `.github/workflows/*.yaml`. Saving a name writes it to the repo env file without
            showing the stored value back.
          </p>
          {githubSecretsDocument?.workflowFiles.length ? (
            <p className="inline-note">
              Workflow files: {githubSecretsDocument.workflowFiles.join(", ")}
            </p>
          ) : (
            <p className="inline-note">
              No workflow files were found. Existing env-file keys are still editable here.
            </p>
          )}
          {githubSecretsMessage ? <p className="inline-note">{githubSecretsMessage}</p> : null}
          <div className="alert-list">
            {githubSecretsDocument?.secrets.length ? (
              githubSecretsDocument.secrets.map((secret) => (
                <article
                  className={`alert-card ${secret.configured ? "alert-ok" : "alert-warning"}`}
                  key={`${githubSecretsDocument.envFilePath}-${secret.name}`}
                >
                  <div className="alert-head">
                    <div>
                      <h3>{secret.name}</h3>
                      <p>
                        {secret.requiredByWorkflows.length > 0
                          ? `Required by ${secret.requiredByWorkflows.join(", ")}`
                          : "Only present in the env file"}
                      </p>
                    </div>
                    <div className="button-row">
                      <mark className={pillTone(secret.configured ? "ok" : "warning")}>
                        {secret.configured ? "configured" : "missing"}
                      </mark>
                      <button
                        className="ghost-button"
                        disabled={!adminUnlocked || githubSecretsBusy}
                        onClick={() => void removeGithubSecret(secret.name)}
                        type="button"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </article>
              ))
            ) : (
              <article className="alert-card alert-ok">
                <div className="alert-head">
                  <div>
                    <h3>No repository secrets yet</h3>
                    <p>Add a secret above or commit workflow files that reference `secrets.*` values.</p>
                  </div>
                  <mark className={pillTone("unknown")}>empty</mark>
                </div>
              </article>
            )}
          </div>
        </>
      ) : (
        <p className="inline-note">
          No deployed site currently exposes checkout metadata in the active status source.
        </p>
      )}
    </article>
  );
}
