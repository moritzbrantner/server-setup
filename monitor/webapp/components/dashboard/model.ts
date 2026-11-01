import type { DomainDnsRecord, SiteDeploymentSettings } from "@/lib/control";
import type { DashboardSnapshot } from "@/lib/status";

export type NewSiteDraft = {
  repoUrl: string;
  branch: string;
  checkoutPath: string;
  email: string;
  skipGithubHook: boolean;
};

export type GithubSecretDraft = {
  name: string;
  value: string;
};

export type GithubSecretSiteOption = {
  siteName: string;
  repoLabel: string;
};

export type DomainSiteOption = {
  siteName: string;
  provider: string;
  zone: string;
};

export type DomainRecordDraft = {
  id: string;
  type: string;
  name: string;
  content: string;
  ttl: string;
  prio: string;
};

export const EMPTY_NEW_SITE_DRAFT: NewSiteDraft = {
  repoUrl: "",
  branch: "",
  checkoutPath: "",
  email: "",
  skipGithubHook: false,
};

export const EMPTY_GITHUB_SECRET_DRAFT: GithubSecretDraft = {
  name: "",
  value: "",
};

export const EMPTY_DOMAIN_RECORD_DRAFT: DomainRecordDraft = {
  id: "",
  type: "A",
  name: "@",
  content: "",
  ttl: "600",
  prio: "",
};

export const DNS_RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV", "ALIAS"];

export function deriveSiteDrafts(snapshot: DashboardSnapshot): Record<string, SiteDeploymentSettings> {
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

export function deriveGithubSecretSiteOptions(snapshot: DashboardSnapshot): GithubSecretSiteOption[] {
  return snapshot.applications
    .filter((application) => application.checkoutPath)
    .map((application) => ({
      siteName: application.name,
      repoLabel: application.webhookRepo || application.repoUrl || application.name,
    }))
    .sort((left, right) => left.siteName.localeCompare(right.siteName));
}

export function deriveDomainSiteOptions(snapshot: DashboardSnapshot): DomainSiteOption[] {
  return snapshot.applications
    .filter((application) => application.dnsProvider && application.dnsZone)
    .map((application) => ({
      siteName: application.name,
      provider: application.dnsProvider || "",
      zone: application.dnsZone || "",
    }))
    .sort((left, right) => left.siteName.localeCompare(right.siteName));
}

export function draftFromDomainRecord(record: DomainDnsRecord): DomainRecordDraft {
  return {
    id: record.id,
    type: record.type || "A",
    name: record.name || "@",
    content: record.content,
    ttl: String(record.ttl || 600),
    prio: record.prio === null ? "" : String(record.prio),
  };
}
