import type { ChangeEvent } from "react";

import { pillTone } from "./format";
import { DNS_RECORD_TYPES, EMPTY_DOMAIN_RECORD_DRAFT } from "./model";
import type { useDomainRecords } from "./use-domain-records";

type DomainDnsCardProps = ReturnType<typeof useDomainRecords> & {
  adminUnlocked: boolean;
};

export function DomainDnsCard({
  adminUnlocked,
  clearDomainRecordDraft,
  domainRecordDraft,
  domainRecordsBusy,
  domainRecordsDocument,
  domainRecordsMessage,
  domainRecordsSiteName,
  domainSiteOptions,
  editDomainRecord,
  loadDomainRecords,
  removeDomainRecord,
  saveDomainRecord,
  selectedDomainSite,
  setDomainRecordDraft,
  setDomainRecordsDocument,
  setDomainRecordsMessage,
  setDomainRecordsSiteName,
  updateDomainRecordDraft,
}: DomainDnsCardProps) {
  return (
    <article className="admin-card">
      <div className="admin-card-head">
        <div>
          <h3>Domain DNS</h3>
          <p>Manage Namecheap or Porkbun DNS records for sites with a `dns` config block.</p>
        </div>
      </div>
      {domainSiteOptions.length > 0 ? (
        <>
          <div className="token-form">
            <label className="token-field">
              <span>Managed site</span>
              <select
                disabled={!adminUnlocked || domainRecordsBusy}
                onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                  setDomainRecordsSiteName(event.target.value);
                  setDomainRecordsDocument(null);
                  setDomainRecordsMessage(null);
                  setDomainRecordDraft(EMPTY_DOMAIN_RECORD_DRAFT);
                }}
                value={domainRecordsSiteName}
              >
                {domainSiteOptions.map((option) => (
                  <option key={option.siteName} value={option.siteName}>
                    {option.siteName}
                  </option>
                ))}
              </select>
            </label>
            <div className="config-meta">
              <span>Provider</span>
              <strong>{domainRecordsDocument?.provider || selectedDomainSite?.provider || "n/a"}</strong>
            </div>
            <div className="config-meta">
              <span>Zone</span>
              <strong>{domainRecordsDocument?.zone || selectedDomainSite?.zone || "Loading..."}</strong>
            </div>
            <label className="token-field">
              <span>Record type</span>
              <select
                disabled={!adminUnlocked || domainRecordsBusy}
                onChange={(event: ChangeEvent<HTMLSelectElement>) =>
                  updateDomainRecordDraft("type", event.target.value)
                }
                value={domainRecordDraft.type}
              >
                {DNS_RECORD_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
            <label className="token-field">
              <span>Host</span>
              <input
                disabled={!adminUnlocked || domainRecordsBusy}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateDomainRecordDraft("name", event.target.value)
                }
                placeholder="@"
                type="text"
                value={domainRecordDraft.name}
              />
            </label>
            <label className="token-field">
              <span>Value</span>
              <input
                disabled={!adminUnlocked || domainRecordsBusy}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateDomainRecordDraft("content", event.target.value)
                }
                placeholder="203.0.113.10"
                type="text"
                value={domainRecordDraft.content}
              />
            </label>
            <label className="token-field">
              <span>TTL</span>
              <input
                disabled={!adminUnlocked || domainRecordsBusy}
                min="0"
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateDomainRecordDraft("ttl", event.target.value)
                }
                type="number"
                value={domainRecordDraft.ttl}
              />
            </label>
            <label className="token-field">
              <span>Priority</span>
              <input
                disabled={!adminUnlocked || domainRecordsBusy}
                min="0"
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  updateDomainRecordDraft("prio", event.target.value)
                }
                placeholder="MX/SRV only"
                type="number"
                value={domainRecordDraft.prio}
              />
            </label>
            <div className="button-row">
              <button
                className="primary-button"
                disabled={
                  !adminUnlocked ||
                  domainRecordsBusy ||
                  !domainRecordsSiteName ||
                  !domainRecordDraft.type.trim() ||
                  !domainRecordDraft.name.trim() ||
                  !domainRecordDraft.content.trim()
                }
                onClick={() => void saveDomainRecord()}
                type="button"
              >
                {domainRecordsBusy
                  ? "Saving..."
                  : domainRecordDraft.id
                    ? "Update record"
                    : "Create record"}
              </button>
              <button
                className="ghost-button"
                disabled={!adminUnlocked || domainRecordsBusy || !domainRecordsSiteName}
                onClick={() => void loadDomainRecords(domainRecordsSiteName)}
                type="button"
              >
                {domainRecordsBusy ? "Refreshing..." : "Refresh records"}
              </button>
              <button
                className="ghost-button"
                disabled={!adminUnlocked || domainRecordsBusy}
                onClick={() => void clearDomainRecordDraft()}
                type="button"
              >
                Clear
              </button>
            </div>
          </div>
          <p className="inline-note">
            Namecheap changes rewrite the provider host list after preserving records that are not edited here.
          </p>
          {domainRecordsMessage ? <p className="inline-note">{domainRecordsMessage}</p> : null}
          <div className="alert-list">
            {domainRecordsDocument?.records.length ? (
              domainRecordsDocument.records.map((record) => (
                <article className="alert-card alert-ok" key={`${domainRecordsDocument.zone}-${record.id}`}>
                  <div className="alert-head">
                    <div>
                      <h3>
                        {record.type} {record.name}
                      </h3>
                      <p>
                        {record.content} · TTL {record.ttl}
                        {record.prio !== null ? ` · priority ${record.prio}` : ""}
                      </p>
                    </div>
                    <div className="button-row">
                      <button
                        className="ghost-button"
                        disabled={!adminUnlocked || domainRecordsBusy}
                        onClick={() => void editDomainRecord(record)}
                        type="button"
                      >
                        Edit
                      </button>
                      <button
                        className="ghost-button"
                        disabled={!adminUnlocked || domainRecordsBusy}
                        onClick={() => void removeDomainRecord(record.id)}
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
                    <h3>No DNS records loaded</h3>
                    <p>Unlock admin controls and refresh records for the selected domain.</p>
                  </div>
                  <mark className={pillTone("unknown")}>empty</mark>
                </div>
              </article>
            )}
          </div>
        </>
      ) : (
        <p className="inline-note">
          No deployed site currently defines `dns.provider` and `dns.zone` in the active status source.
        </p>
      )}
    </article>
  );
}
