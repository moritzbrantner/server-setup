import { startTransition, useEffect, useEffectEvent, useState } from "react";

import type { DomainDnsRecord, DomainRecordsDocument } from "@/lib/control";
import type { DashboardSnapshot } from "@/lib/status";

import { adminHeaders, readError } from "./api";
import {
  deriveDomainSiteOptions,
  draftFromDomainRecord,
  EMPTY_DOMAIN_RECORD_DRAFT,
  type DomainRecordDraft,
} from "./model";

type UseDomainRecordsOptions = {
  snapshot: DashboardSnapshot;
  adminToken: string;
  adminUnlocked: boolean;
  setAdminMessage: (message: string | null) => void;
};

export function useDomainRecords({
  snapshot,
  adminToken,
  adminUnlocked,
  setAdminMessage,
}: UseDomainRecordsOptions) {
  const [domainRecordsDocument, setDomainRecordsDocument] = useState<DomainRecordsDocument | null>(null);
  const [domainRecordsMessage, setDomainRecordsMessage] = useState<string | null>(null);
  const [domainRecordsBusy, setDomainRecordsBusy] = useState(false);
  const [domainRecordsSiteName, setDomainRecordsSiteName] = useState<string>(
    deriveDomainSiteOptions(snapshot)[0]?.siteName || ""
  );
  const [domainRecordDraft, setDomainRecordDraft] = useState<DomainRecordDraft>(EMPTY_DOMAIN_RECORD_DRAFT);
  const domainSiteOptions = deriveDomainSiteOptions(snapshot);
  const selectedDomainSite =
    domainSiteOptions.find((entry) => entry.siteName === domainRecordsSiteName) || null;

  const loadDomainRecords = useEffectEvent(async (siteName: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!siteName.trim()) {
      setDomainRecordsDocument(null);
      return;
    }

    setDomainRecordsBusy(true);
    try {
      const response = await fetch(`/api/domain-records?siteName=${encodeURIComponent(siteName)}`, {
        cache: "no-store",
        headers: adminHeaders(trimmedToken),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const document = (await response.json()) as DomainRecordsDocument;
      startTransition(() => {
        setDomainRecordsDocument(document);
        setDomainRecordsMessage(null);
        setAdminMessage(null);
      });
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : "Unable to load domain records.";
      startTransition(() => {
        setDomainRecordsDocument(null);
        setDomainRecordsMessage(message);
      });
    } finally {
      setDomainRecordsBusy(false);
    }
  });

  useEffect(() => {
    if (domainSiteOptions.length === 0) {
      setDomainRecordsSiteName("");
      setDomainRecordsDocument(null);
      return;
    }

    if (!domainSiteOptions.some((entry) => entry.siteName === domainRecordsSiteName)) {
      setDomainRecordsSiteName(domainSiteOptions[0]?.siteName || "");
    }
  }, [domainSiteOptions, domainRecordsSiteName]);

  useEffect(() => {
    if (!adminUnlocked || !domainRecordsSiteName) {
      return;
    }
    void loadDomainRecords(domainRecordsSiteName);
  }, [adminUnlocked, domainRecordsSiteName, loadDomainRecords]);

  const updateDomainRecordDraft = useEffectEvent(
    (field: keyof DomainRecordDraft, value: string) => {
      setDomainRecordDraft((current) => ({
        ...current,
        [field]: value,
      }));
    }
  );

  const editDomainRecord = useEffectEvent((record: DomainDnsRecord) => {
    setDomainRecordDraft(draftFromDomainRecord(record));
    setDomainRecordsMessage(`Editing DNS record ${record.id}.`);
  });

  const clearDomainRecordDraft = useEffectEvent(() => {
    setDomainRecordDraft(EMPTY_DOMAIN_RECORD_DRAFT);
    setDomainRecordsMessage(null);
  });

  const saveDomainRecord = useEffectEvent(async () => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!domainRecordsSiteName) {
      setDomainRecordsMessage("Select a domain-managed site first.");
      return;
    }
    if (!domainRecordDraft.type.trim() || !domainRecordDraft.name.trim() || !domainRecordDraft.content.trim()) {
      setDomainRecordsMessage("Record type, name, and value are required.");
      return;
    }

    setDomainRecordsBusy(true);
    try {
      const editing = Boolean(domainRecordDraft.id.trim());
      const response = await fetch("/api/domain-records", {
        method: editing ? "PUT" : "POST",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          siteName: domainRecordsSiteName,
          id: domainRecordDraft.id,
          type: domainRecordDraft.type,
          name: domainRecordDraft.name,
          content: domainRecordDraft.content,
          ttl: Number(domainRecordDraft.ttl || 600),
          prio: domainRecordDraft.prio.trim() ? Number(domainRecordDraft.prio) : null,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: DomainRecordsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setDomainRecordsDocument(payload.document);
        setDomainRecordsMessage(payload.result.summary);
        setDomainRecordDraft(EMPTY_DOMAIN_RECORD_DRAFT);
        setAdminMessage(null);
      });
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "Unable to save the DNS record.";
      startTransition(() => {
        setDomainRecordsMessage(message);
      });
    } finally {
      setDomainRecordsBusy(false);
    }
  });

  const removeDomainRecord = useEffectEvent(async (id: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!domainRecordsSiteName) {
      setDomainRecordsMessage("Select a domain-managed site first.");
      return;
    }

    setDomainRecordsBusy(true);
    try {
      const response = await fetch("/api/domain-records", {
        method: "DELETE",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          siteName: domainRecordsSiteName,
          id,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: DomainRecordsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setDomainRecordsDocument(payload.document);
        setDomainRecordsMessage(payload.result.summary);
        setAdminMessage(null);
      });
    } catch (deleteError) {
      const message =
        deleteError instanceof Error ? deleteError.message : "Unable to delete the DNS record.";
      startTransition(() => {
        setDomainRecordsMessage(message);
      });
    } finally {
      setDomainRecordsBusy(false);
    }
  });

  const resetDomainRecords = useEffectEvent(() => {
    setDomainRecordsDocument(null);
    setDomainRecordsMessage(null);
    setDomainRecordDraft(EMPTY_DOMAIN_RECORD_DRAFT);
  });

  return {
    domainSiteOptions,
    selectedDomainSite,
    domainRecordsDocument,
    setDomainRecordsDocument,
    domainRecordsMessage,
    setDomainRecordsMessage,
    domainRecordsBusy,
    domainRecordsSiteName,
    setDomainRecordsSiteName,
    domainRecordDraft,
    setDomainRecordDraft,
    loadDomainRecords,
    updateDomainRecordDraft,
    editDomainRecord,
    clearDomainRecordDraft,
    saveDomainRecord,
    removeDomainRecord,
    resetDomainRecords,
  };
}
