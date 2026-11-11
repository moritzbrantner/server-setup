import type { ChangeEvent } from "react";

import type { EditableConfigDocument } from "@/lib/control";

import { configModeLabel, pillTone } from "./format";

type ConfigCardProps = {
  adminToken: string;
  adminUnlocked: boolean;
  configBusy: boolean;
  configDirty: boolean;
  configDocument: EditableConfigDocument | null;
  configDraft: string;
  configMessage: string | null;
  discardConfigChanges: () => void;
  loadConfig: (token: string) => void | Promise<void>;
  saveConfig: () => void | Promise<void>;
  setConfigDraft: (draft: string) => void;
};

export function ConfigCard({
  adminToken,
  adminUnlocked,
  configBusy,
  configDirty,
  configDocument,
  configDraft,
  configMessage,
  discardConfigChanges,
  loadConfig,
  saveConfig,
  setConfigDraft,
}: ConfigCardProps) {
  return (
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
  );
}
