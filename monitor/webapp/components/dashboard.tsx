"use client";

import {
  startTransition,
  useEffect,
  useEffectEvent,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import type {
  DashboardActionRequest,
  DashboardActionResult,
  EditableConfigDocument,
  GithubSecretsDocument,
  PorkbunDnsDocument,
  PorkbunDnsRecord,
  SiteDeploymentSettings,
} from "@/lib/control";
import type { DashboardSnapshot, SiteCheck, StatusLevel } from "@/lib/status";

type DashboardProps = {
  initialSnapshot: DashboardSnapshot;
  adminControlsEnabled: boolean;
};

type NewSiteDraft = {
  repoUrl: string;
  branch: string;
  checkoutPath: string;
  email: string;
  skipGithubHook: boolean;
};

type GithubSecretDraft = {
  name: string;
  value: string;
};

type PorkbunDnsDraft = {
  id: string;
  name: string;
  type: string;
  content: string;
  ttl: string;
  prio: string;
  notes: string;
};

type GithubSecretSiteOption = {
  siteName: string;
  repoLabel: string;
};

const EMPTY_NEW_SITE_DRAFT: NewSiteDraft = {
  repoUrl: "",
  branch: "",
  checkoutPath: "",
  email: "",
  skipGithubHook: false,
};

const EMPTY_GITHUB_SECRET_DRAFT: GithubSecretDraft = {
  name: "",
  value: "",
};

const EMPTY_PORKBUN_DNS_DRAFT: PorkbunDnsDraft = {
  id: "",
  name: "",
  type: "A",
  content: "",
  ttl: "600",
  prio: "",
  notes: "",
};

const PORKBUN_RECORD_TYPES = [
  "A",
  "AAAA",
  "ALIAS",
  "CAA",
  "CNAME",
  "HTTPS",
  "MX",
  "NS",
  "SRV",
  "SSHFP",
  "SVCB",
  "TLSA",
  "TXT",
];

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

function adminHeaders(adminToken: string): HeadersInit {
  return {
    "content-type": "application/json",
    "x-status-admin-token": adminToken,
  };
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    if (typeof body.error === "string" && body.error.trim()) {
      return body.error;
    }
  } catch {
    // Ignore JSON parsing errors and use the generic fallback below.
  }

  return `Request failed with ${response.status}.`;
}

function actionLabel(action: DashboardActionRequest["action"]): string {
  switch (action) {
    case "start-nginx":
      return "Start Nginx";
    case "restart-nginx":
      return "Restart Nginx";
    case "reload-nginx":
      return "Reload Nginx";
    case "restart-webhook":
      return "Restart webhook receiver";
    case "restart-status-webapp":
      return "Restart status webapp";
    case "restart-site-service":
      return "Restart service";
    case "retry-deploy":
      return "Retry deploy";
    case "add-site":
      return "Add website";
  }
}

function actionKey(action: DashboardActionRequest): string {
  if ("siteName" in action) {
    return `${action.action}:${action.siteName}`;
  }
  return action.action;
}

function configModeLabel(kind: EditableConfigDocument["kind"]): string {
  return kind === "registry" ? "deploy registry" : "monitor list";
}

function deriveSiteDrafts(snapshot: DashboardSnapshot): Record<string, SiteDeploymentSettings> {
  return Object.fromEntries(
    snapshot.applications.map((application) => [
      application.name,
      {
        siteName: application.name,
        repoUrl: application.repoUrl || "",
        webhookRepo: application.webhookRepo || "",
        branch: application.branch || "",
        checkoutPath: application.checkoutPath || "",
      },
    ])
  );
}

function deriveGithubSecretSiteOptions(snapshot: DashboardSnapshot): GithubSecretSiteOption[] {
  return snapshot.applications
    .filter((application) => application.checkoutPath)
    .map((application) => ({
      siteName: application.name,
      repoLabel: application.webhookRepo || application.repoUrl || application.name,
    }))
    .sort((left, right) => left.siteName.localeCompare(right.siteName));
}

function pushDeployTone(application: SiteCheck): string {
  return pillTone(application.pushDeploy?.status || "unknown");
}

function porkbunRecordNameForDraft(record: PorkbunDnsRecord, domain: string): string {
  const fqdn = record.name.replace(/\.$/, "");
  const cleanDomain = domain.replace(/\.$/, "");
  if (fqdn === cleanDomain) {
    return "";
  }
  if (fqdn.endsWith(`.${cleanDomain}`)) {
    return fqdn.slice(0, -(cleanDomain.length + 1));
  }
  return fqdn;
}

export function Dashboard({ initialSnapshot, adminControlsEnabled }: DashboardProps) {
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
  const [githubSecretsDocument, setGithubSecretsDocument] = useState<GithubSecretsDocument | null>(null);
  const [githubSecretsMessage, setGithubSecretsMessage] = useState<string | null>(null);
  const [githubSecretsBusy, setGithubSecretsBusy] = useState(false);
  const [githubSecretsSiteName, setGithubSecretsSiteName] = useState<string>(
    deriveGithubSecretSiteOptions(initialSnapshot)[0]?.siteName || ""
  );
  const [githubSecretDraft, setGithubSecretDraft] = useState<GithubSecretDraft>(EMPTY_GITHUB_SECRET_DRAFT);
  const [porkbunDnsDocument, setPorkbunDnsDocument] = useState<PorkbunDnsDocument | null>(null);
  const [porkbunDnsDomain, setPorkbunDnsDomain] = useState("");
  const [porkbunDnsDraft, setPorkbunDnsDraft] = useState<PorkbunDnsDraft>(EMPTY_PORKBUN_DNS_DRAFT);
  const [porkbunDnsMessage, setPorkbunDnsMessage] = useState<string | null>(null);
  const [porkbunDnsBusy, setPorkbunDnsBusy] = useState(false);
  const githubSecretSiteOptions = deriveGithubSecretSiteOptions(snapshot);
  const selectedGithubSecretSite =
    githubSecretSiteOptions.find((entry) => entry.siteName === githubSecretsSiteName) || null;

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
        setGithubSecretsMessage(null);
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

  const loadGithubSecrets = useEffectEvent(async (siteName: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!siteName.trim()) {
      setGithubSecretsDocument(null);
      return;
    }

    setGithubSecretsBusy(true);
    try {
      const response = await fetch(`/api/github-secrets?siteName=${encodeURIComponent(siteName)}`, {
        cache: "no-store",
        headers: adminHeaders(trimmedToken),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const document = (await response.json()) as GithubSecretsDocument;
      startTransition(() => {
        setGithubSecretsDocument(document);
        setGithubSecretsMessage(null);
        setAdminMessage(null);
      });
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : "Unable to load repository secrets.";
      startTransition(() => {
        setGithubSecretsDocument(null);
        setGithubSecretsMessage(message);
      });
    } finally {
      setGithubSecretsBusy(false);
    }
  });

  const loadPorkbunDns = useEffectEvent(async (domain: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }

    setPorkbunDnsBusy(true);
    try {
      const suffix = domain.trim() ? `?domain=${encodeURIComponent(domain.trim())}` : "";
      const response = await fetch(`/api/porkbun-dns${suffix}`, {
        cache: "no-store",
        headers: adminHeaders(trimmedToken),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const document = (await response.json()) as PorkbunDnsDocument;
      startTransition(() => {
        setPorkbunDnsDocument(document);
        if (!domain.trim() && document.domains[0]?.domain) {
          setPorkbunDnsDomain(document.domains[0].domain);
        }
        setPorkbunDnsMessage(null);
        setAdminMessage(null);
      });
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Unable to load Porkbun DNS.";
      startTransition(() => {
        setPorkbunDnsDocument(null);
        setPorkbunDnsMessage(message);
      });
    } finally {
      setPorkbunDnsBusy(false);
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
    if (githubSecretSiteOptions.length === 0) {
      setGithubSecretsSiteName("");
      setGithubSecretsDocument(null);
      return;
    }

    if (!githubSecretSiteOptions.some((entry) => entry.siteName === githubSecretsSiteName)) {
      setGithubSecretsSiteName(githubSecretSiteOptions[0]?.siteName || "");
    }
  }, [githubSecretSiteOptions, githubSecretsSiteName]);

  useEffect(() => {
    const intervalId = setInterval(() => {
      void refreshSnapshot();
    }, 30000);

    return () => clearInterval(intervalId);
  }, [refreshSnapshot]);

  useEffect(() => {
    if (!adminUnlocked || !githubSecretsSiteName) {
      return;
    }
    void loadGithubSecrets(githubSecretsSiteName);
  }, [adminUnlocked, githubSecretsSiteName, loadGithubSecrets]);

  useEffect(() => {
    if (!adminUnlocked) {
      return;
    }
    void loadPorkbunDns(porkbunDnsDomain);
  }, [adminUnlocked, porkbunDnsDomain, loadPorkbunDns]);

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

  const updateGithubSecretDraft = useEffectEvent(
    (field: keyof GithubSecretDraft, value: string) => {
      setGithubSecretDraft((current) => ({
        ...current,
        [field]: value,
      }));
    }
  );

  const saveGithubSecret = useEffectEvent(async () => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!githubSecretsSiteName) {
      setGithubSecretsMessage("Select a deployed site first.");
      return;
    }
    if (!githubSecretDraft.name.trim()) {
      setGithubSecretsMessage("Secret name is required.");
      return;
    }
    if (!githubSecretDraft.value) {
      setGithubSecretsMessage("Secret value is required.");
      return;
    }

    setGithubSecretsBusy(true);
    try {
      const response = await fetch("/api/github-secrets", {
        method: "PUT",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          siteName: githubSecretsSiteName,
          name: githubSecretDraft.name,
          value: githubSecretDraft.value,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: GithubSecretsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setGithubSecretsDocument(payload.document);
        setGithubSecretsMessage(payload.result.summary);
        setGithubSecretDraft(EMPTY_GITHUB_SECRET_DRAFT);
        setAdminMessage(null);
      });
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "Unable to save the repository secret.";
      startTransition(() => {
        setGithubSecretsMessage(message);
      });
    } finally {
      setGithubSecretsBusy(false);
    }
  });

  const removeGithubSecret = useEffectEvent(async (name: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!githubSecretsSiteName) {
      setGithubSecretsMessage("Select a deployed site first.");
      return;
    }

    setGithubSecretsBusy(true);
    try {
      const response = await fetch("/api/github-secrets", {
        method: "DELETE",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          siteName: githubSecretsSiteName,
          name,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: GithubSecretsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setGithubSecretsDocument(payload.document);
        setGithubSecretsMessage(payload.result.summary);
        setAdminMessage(null);
      });
    } catch (deleteError) {
      const message =
        deleteError instanceof Error ? deleteError.message : "Unable to delete the repository secret.";
      startTransition(() => {
        setGithubSecretsMessage(message);
      });
    } finally {
      setGithubSecretsBusy(false);
    }
  });

  const updatePorkbunDnsDraft = useEffectEvent((field: keyof PorkbunDnsDraft, value: string) => {
    setPorkbunDnsDraft((current) => ({
      ...current,
      [field]: value,
    }));
  });

  const editPorkbunRecord = useEffectEvent((record: PorkbunDnsRecord) => {
    setPorkbunDnsDraft({
      id: record.id,
      name: porkbunRecordNameForDraft(record, porkbunDnsDomain),
      type: record.type || "A",
      content: record.content,
      ttl: record.ttl || "600",
      prio: record.prio || "",
      notes: record.notes || "",
    });
    setPorkbunDnsMessage(`Editing DNS record ${record.id}.`);
  });

  const clearPorkbunDnsDraft = useEffectEvent(() => {
    setPorkbunDnsDraft(EMPTY_PORKBUN_DNS_DRAFT);
    setPorkbunDnsMessage(null);
  });

  const savePorkbunDnsRecord = useEffectEvent(async () => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!porkbunDnsDomain.trim()) {
      setPorkbunDnsMessage("Select a Porkbun domain first.");
      return;
    }
    if (!porkbunDnsDraft.type.trim()) {
      setPorkbunDnsMessage("Record type is required.");
      return;
    }
    if (!porkbunDnsDraft.content.trim()) {
      setPorkbunDnsMessage("Record content is required.");
      return;
    }

    setPorkbunDnsBusy(true);
    try {
      const response = await fetch("/api/porkbun-dns", {
        method: porkbunDnsDraft.id ? "PUT" : "POST",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          domain: porkbunDnsDomain,
          ...porkbunDnsDraft,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: PorkbunDnsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setPorkbunDnsDocument(payload.document);
        setPorkbunDnsMessage(payload.result.summary);
        setPorkbunDnsDraft(EMPTY_PORKBUN_DNS_DRAFT);
        setAdminMessage(null);
      });
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save the Porkbun DNS record.";
      startTransition(() => {
        setPorkbunDnsMessage(message);
      });
    } finally {
      setPorkbunDnsBusy(false);
    }
  });

  const removePorkbunDnsRecord = useEffectEvent(async (recordId: string) => {
    const trimmedToken = adminToken.trim();
    if (!trimmedToken) {
      setAdminMessage("Admin token is missing.");
      return;
    }
    if (!porkbunDnsDomain.trim()) {
      setPorkbunDnsMessage("Select a Porkbun domain first.");
      return;
    }

    setPorkbunDnsBusy(true);
    try {
      const response = await fetch("/api/porkbun-dns", {
        method: "DELETE",
        headers: adminHeaders(trimmedToken),
        body: JSON.stringify({
          domain: porkbunDnsDomain,
          id: recordId,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }

      const payload = (await response.json()) as {
        document: PorkbunDnsDocument;
        result: { summary: string };
      };
      startTransition(() => {
        setPorkbunDnsDocument(payload.document);
        setPorkbunDnsMessage(payload.result.summary);
        if (porkbunDnsDraft.id === recordId) {
          setPorkbunDnsDraft(EMPTY_PORKBUN_DNS_DRAFT);
        }
        setAdminMessage(null);
      });
    } catch (deleteError) {
      const message =
        deleteError instanceof Error ? deleteError.message : "Unable to delete the Porkbun DNS record.";
      startTransition(() => {
        setPorkbunDnsMessage(message);
      });
    } finally {
      setPorkbunDnsBusy(false);
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
    setGithubSecretsDocument(null);
    setGithubSecretsMessage(null);
    setGithubSecretDraft(EMPTY_GITHUB_SECRET_DRAFT);
    setPorkbunDnsDocument(null);
    setPorkbunDnsDomain("");
    setPorkbunDnsDraft(EMPTY_PORKBUN_DNS_DRAFT);
    setPorkbunDnsMessage(null);
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
          <div className="meta-actions">
            <button className="ghost-button" onClick={() => void refreshSnapshot()} type="button">
              Refresh now
            </button>
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
            <p className="eyebrow">Admin</p>
            <h2>Config and recovery controls</h2>
          </div>
        </div>
        <div className="panel-content admin-grid">
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

          <article className="admin-card">
            <div className="admin-card-head">
              <div>
                <h3>Porkbun DNS</h3>
                <p>Manage DNS records through Porkbun using the server-side API credentials.</p>
              </div>
              <mark className={pillTone(porkbunDnsDocument ? "ok" : "unknown")}>
                {porkbunDnsDocument ? "loaded" : "locked"}
              </mark>
            </div>
            <div className="token-form">
              <label className="token-field">
                <span>Domain</span>
                <select
                  disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDocument?.domains.length}
                  onChange={(event: ChangeEvent<HTMLSelectElement>) => {
                    setPorkbunDnsDomain(event.target.value);
                    setPorkbunDnsDraft(EMPTY_PORKBUN_DNS_DRAFT);
                    setPorkbunDnsMessage(null);
                  }}
                  value={porkbunDnsDomain}
                >
                  {porkbunDnsDocument?.domains.length ? (
                    porkbunDnsDocument.domains.map((domain) => (
                      <option key={domain.domain} value={domain.domain}>
                        {domain.domain}
                      </option>
                    ))
                  ) : (
                    <option value="">No domains loaded</option>
                  )}
                </select>
              </label>
              <div className="button-row">
                <button
                  className="ghost-button"
                  disabled={!adminUnlocked || porkbunDnsBusy}
                  onClick={() => void loadPorkbunDns(porkbunDnsDomain)}
                  type="button"
                >
                  {porkbunDnsBusy ? "Refreshing..." : "Refresh DNS"}
                </button>
                {porkbunDnsDraft.id ? (
                  <button
                    className="ghost-button"
                    disabled={!adminUnlocked || porkbunDnsBusy}
                    onClick={() => void clearPorkbunDnsDraft()}
                    type="button"
                  >
                    Cancel edit
                  </button>
                ) : null}
              </div>
              <div className="dns-record-form">
                <label className="token-field">
                  <span>Type</span>
                  <select
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLSelectElement>) =>
                      updatePorkbunDnsDraft("type", event.target.value)
                    }
                    value={porkbunDnsDraft.type}
                  >
                    {PORKBUN_RECORD_TYPES.map((recordType) => (
                      <option key={recordType} value={recordType}>
                        {recordType}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="token-field">
                  <span>Name</span>
                  <input
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updatePorkbunDnsDraft("name", event.target.value)
                    }
                    placeholder="@, www, *, or full name"
                    type="text"
                    value={porkbunDnsDraft.name}
                  />
                </label>
                <label className="token-field">
                  <span>Content</span>
                  <input
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updatePorkbunDnsDraft("content", event.target.value)
                    }
                    placeholder="1.2.3.4 or target.example.com"
                    type="text"
                    value={porkbunDnsDraft.content}
                  />
                </label>
                <label className="token-field">
                  <span>TTL</span>
                  <input
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updatePorkbunDnsDraft("ttl", event.target.value)
                    }
                    placeholder="600"
                    type="number"
                    value={porkbunDnsDraft.ttl}
                  />
                </label>
                <label className="token-field">
                  <span>Priority</span>
                  <input
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updatePorkbunDnsDraft("prio", event.target.value)
                    }
                    placeholder="MX/SRV only"
                    type="number"
                    value={porkbunDnsDraft.prio}
                  />
                </label>
                <label className="token-field">
                  <span>Notes</span>
                  <input
                    disabled={!adminUnlocked || porkbunDnsBusy || !porkbunDnsDomain}
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updatePorkbunDnsDraft("notes", event.target.value)
                    }
                    placeholder="Optional"
                    type="text"
                    value={porkbunDnsDraft.notes}
                  />
                </label>
              </div>
              <div className="button-row">
                <button
                  className="primary-button"
                  disabled={
                    !adminUnlocked ||
                    porkbunDnsBusy ||
                    !porkbunDnsDomain ||
                    !porkbunDnsDraft.type.trim() ||
                    !porkbunDnsDraft.content.trim()
                  }
                  onClick={() => void savePorkbunDnsRecord()}
                  type="button"
                >
                  {porkbunDnsBusy
                    ? "Saving..."
                    : porkbunDnsDraft.id
                      ? "Update record"
                      : "Create record"}
                </button>
              </div>
            </div>
            <p className="inline-note">
              Set `PORKBUN_API_KEY` and `PORKBUN_SECRET_API_KEY` in the status webapp environment.
              The name field accepts blank or `@` for the root record.
            </p>
            {porkbunDnsMessage ? <p className="inline-note">{porkbunDnsMessage}</p> : null}
            <div className="alert-list dns-record-list">
              {porkbunDnsDocument?.records.length ? (
                porkbunDnsDocument.records.map((record) => (
                  <article className="alert-card alert-ok" key={`${porkbunDnsDomain}-${record.id}`}>
                    <div className="alert-head">
                      <div>
                        <h3>
                          {record.type} {record.name || porkbunDnsDomain}
                        </h3>
                        <p>{record.content}</p>
                        <p>
                          TTL {record.ttl || "n/a"} · priority {record.prio || "0"}
                          {record.notes ? ` · ${record.notes}` : ""}
                        </p>
                      </div>
                      <div className="button-row">
                        <button
                          className="ghost-button"
                          disabled={!adminUnlocked || porkbunDnsBusy}
                          onClick={() => void editPorkbunRecord(record)}
                          type="button"
                        >
                          Edit
                        </button>
                        <button
                          className="ghost-button"
                          disabled={!adminUnlocked || porkbunDnsBusy}
                          onClick={() => void removePorkbunDnsRecord(record.id)}
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
                      <p>Select a domain and refresh DNS records.</p>
                    </div>
                    <mark className={pillTone("unknown")}>empty</mark>
                  </div>
                </article>
              )}
            </div>
          </article>
        </div>
        <div className="panel-content">
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
        </div>
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
              </div>

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
