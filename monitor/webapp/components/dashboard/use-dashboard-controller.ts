import {
  startTransition,
  useEffect,
  useEffectEvent,
  useState,
  type FormEvent,
} from "react";

import type {
  DashboardActionRequest,
  DashboardActionResult,
  EditableConfigDocument,
  SiteDeploymentSettings,
} from "@/lib/control";
import type { DashboardSnapshot } from "@/lib/status";

import { adminHeaders, readError } from "./api";
import { actionKey } from "./format";
import { deriveSiteDrafts, EMPTY_NEW_SITE_DRAFT, type NewSiteDraft } from "./model";
import { useDomainRecords } from "./use-domain-records";
import { useGithubSecrets } from "./use-github-secrets";

export function useDashboardController(initialSnapshot: DashboardSnapshot) {
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
  const [siteDrafts, setSiteDrafts] = useState<Record<string, SiteDeploymentSettings>>(
    deriveSiteDrafts(initialSnapshot)
  );
  const [siteSaveKey, setSiteSaveKey] = useState<string | null>(null);
  const [newSiteDraft, setNewSiteDraft] = useState<NewSiteDraft>(EMPTY_NEW_SITE_DRAFT);
  const secrets = useGithubSecrets({ snapshot, adminToken, adminUnlocked, setAdminMessage });
  const domains = useDomainRecords({ snapshot, adminToken, adminUnlocked, setAdminMessage });

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
        setSiteDrafts(deriveSiteDrafts(nextSnapshot));
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
        setSiteDrafts(deriveSiteDrafts(snapshot));
        setConfigMessage(null);
        setAdminMessage(null);
        secrets.setGithubSecretsMessage(null);
        domains.setDomainRecordsMessage(null);
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
        setSiteDrafts(deriveSiteDrafts(payload.snapshot));
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
        config?: EditableConfigDocument;
      };
      startTransition(() => {
        if (payload.config) {
          setConfigDocument(payload.config);
          setConfigDraft(payload.config.raw);
        }
        setSnapshot(payload.snapshot);
        setSiteDrafts(deriveSiteDrafts(payload.snapshot));
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

  const updateNewSiteDraft = useEffectEvent(
    (field: keyof Omit<NewSiteDraft, "skipGithubHook">, value: string) => {
      setNewSiteDraft((current) => ({
        ...current,
        [field]: value,
      }));
    }
  );

  const setNewSiteSkipGithubHook = useEffectEvent((checked: boolean) => {
    setNewSiteDraft((current) => ({
      ...current,
      skipGithubHook: checked,
    }));
  });

  const createSite = useEffectEvent(async () => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }

    if (!newSiteDraft.repoUrl.trim()) {
      setAdminMessage("Repository URL is required to deploy a website.");
      return;
    }

    setBusyActionKey("add-site");
    try {
      const response = await fetch("/api/actions", {
        method: "POST",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          action: "add-site",
          ...newSiteDraft,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        result: DashboardActionResult;
        snapshot: DashboardSnapshot;
        config?: EditableConfigDocument;
      };
      startTransition(() => {
        if (payload.config) {
          setConfigDocument(payload.config);
          setConfigDraft(payload.config.raw);
        }
        setSnapshot(payload.snapshot);
        setSiteDrafts(deriveSiteDrafts(payload.snapshot));
        setActionResult(payload.result);
        setNewSiteDraft(EMPTY_NEW_SITE_DRAFT);
        setAdminMessage(null);
        setError(null);
      });
    } catch (createError) {
      const message =
        createError instanceof Error ? createError.message : "Unable to deploy the new website.";
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
    setSiteDrafts(deriveSiteDrafts(snapshot));
    setConfigMessage(null);
    secrets.resetGithubSecrets();
    domains.resetDomainRecords();
    setActionResult(null);
    setAdminMessage("Admin token cleared from this browser session.");
  });

  const updateSiteDraft = useEffectEvent(
    (siteName: string, field: keyof Omit<SiteDeploymentSettings, "siteName">, value: string) => {
      setSiteDrafts((current) => ({
        ...current,
        [siteName]: {
          ...(current[siteName] || {
            siteName,
            repoUrl: "",
            webhookRepo: "",
            branch: "",
            checkoutPath: "",
          }),
          [field]: value,
        },
      }));
    }
  );

  const saveSiteSettings = useEffectEvent(async (siteName: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }

    const settings = siteDrafts[siteName];
    if (!settings) {
      setAdminMessage(`No editable settings were found for ${siteName}.`);
      return;
    }

    setSiteSaveKey(siteName);
    try {
      const response = await fetch("/api/site-settings", {
        method: "PUT",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify(settings),
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
        setSiteDrafts(deriveSiteDrafts(payload.snapshot));
        setAdminMessage(null);
        setError(null);
      });
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "Unable to save deployment settings.";
      startTransition(() => {
        setAdminMessage(message);
      });
    } finally {
      setSiteSaveKey(null);
    }
  });

  const configDirty = configDocument !== null && configDraft !== configDocument.raw;

  return {
    snapshot,
    error,
    refreshSnapshot,
    configDocument,
    configDraft,
    setConfigDraft,
    configMessage,
    configBusy,
    configDirty,
    loadConfig,
    saveConfig,
    discardConfigChanges,
    adminMessage,
    actionResult,
    adminToken,
    setAdminToken,
    adminUnlocked,
    unlockAdminControls,
    resetAdminSession,
    busyActionKey,
    runAction,
    newSiteDraft,
    updateNewSiteDraft,
    setNewSiteSkipGithubHook,
    createSite,
    siteDrafts,
    siteSaveKey,
    updateSiteDraft,
    saveSiteSettings,
    secrets,
    domains,
  };
}
