import { startTransition, useEffect, useEffectEvent, useState } from "react";

import type { GithubSecretsDocument } from "@/lib/control";
import type { DashboardSnapshot } from "@/lib/status";

import { adminHeaders, readError } from "./api";
import {
  deriveGithubSecretSiteOptions,
  EMPTY_GITHUB_SECRET_DRAFT,
  type GithubSecretDraft,
} from "./model";

type UseGithubSecretsOptions = {
  snapshot: DashboardSnapshot;
  adminToken: string;
  adminUnlocked: boolean;
  setAdminMessage: (message: string | null) => void;
};

export function useGithubSecrets({
  snapshot,
  adminToken,
  adminUnlocked,
  setAdminMessage,
}: UseGithubSecretsOptions) {
  const [githubSecretsDocument, setGithubSecretsDocument] = useState<GithubSecretsDocument | null>(null);
  const [githubSecretsMessage, setGithubSecretsMessage] = useState<string | null>(null);
  const [githubSecretsBusy, setGithubSecretsBusy] = useState(false);
  const [githubSecretsSiteName, setGithubSecretsSiteName] = useState<string>(
    deriveGithubSecretSiteOptions(snapshot)[0]?.siteName || ""
  );
  const [githubSecretDraft, setGithubSecretDraft] = useState<GithubSecretDraft>(EMPTY_GITHUB_SECRET_DRAFT);
  const githubSecretSiteOptions = deriveGithubSecretSiteOptions(snapshot);
  const selectedGithubSecretSite =
    githubSecretSiteOptions.find((entry) => entry.siteName === githubSecretsSiteName) || null;

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
    if (!adminUnlocked || !githubSecretsSiteName) {
      return;
    }
    void loadGithubSecrets(githubSecretsSiteName);
  }, [adminUnlocked, githubSecretsSiteName, loadGithubSecrets]);

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

  const resetGithubSecrets = useEffectEvent(() => {
    setGithubSecretsDocument(null);
    setGithubSecretsMessage(null);
    setGithubSecretDraft(EMPTY_GITHUB_SECRET_DRAFT);
  });

  return {
    githubSecretSiteOptions,
    selectedGithubSecretSite,
    githubSecretsDocument,
    setGithubSecretsDocument,
    githubSecretsMessage,
    setGithubSecretsMessage,
    githubSecretsBusy,
    githubSecretsSiteName,
    setGithubSecretsSiteName,
    githubSecretDraft,
    loadGithubSecrets,
    updateGithubSecretDraft,
    saveGithubSecret,
    removeGithubSecret,
    resetGithubSecrets,
  };
}
