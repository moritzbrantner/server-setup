"use client";

import type { DashboardSnapshot } from "@/lib/status";

import { AddSiteCard, QuickActionsCard, UnlockCard } from "./dashboard/admin-cards";
import { ApplicationsSection } from "./dashboard/applications-section";
import { ConfigCard } from "./dashboard/config-card";
import { DomainDnsCard } from "./dashboard/domain-dns-card";
import { GithubSecretsCard } from "./dashboard/github-secrets-card";
import {
  AlertsSection,
  HeroSection,
  HostHealthSection,
  SetupHealthSection,
  SummaryGrid,
} from "./dashboard/status-sections";
import { useDashboardController } from "./dashboard/use-dashboard-controller";

type DashboardProps = {
  initialSnapshot: DashboardSnapshot;
  adminControlsEnabled: boolean;
};

export function Dashboard({ initialSnapshot, adminControlsEnabled }: DashboardProps) {
  const {
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
  } = useDashboardController(initialSnapshot);

  return (
    <main className="shell">
      <HeroSection error={error} refreshSnapshot={refreshSnapshot} snapshot={snapshot} />
      <SummaryGrid snapshot={snapshot} />

      <section className="panel">
        <div className="section-head">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Config and recovery controls</h2>
          </div>
        </div>
        <div className="panel-content admin-grid">
          <UnlockCard
            adminControlsEnabled={adminControlsEnabled}
            adminMessage={adminMessage}
            adminToken={adminToken}
            adminUnlocked={adminUnlocked}
            resetAdminSession={resetAdminSession}
            setAdminToken={setAdminToken}
            unlockAdminControls={unlockAdminControls}
          />
          <QuickActionsCard
            actionResult={actionResult}
            adminUnlocked={adminUnlocked}
            busyActionKey={busyActionKey}
            runAction={runAction}
          />
          <AddSiteCard
            adminUnlocked={adminUnlocked}
            busyActionKey={busyActionKey}
            configDocument={configDocument}
            createSite={createSite}
            newSiteDraft={newSiteDraft}
            setNewSiteSkipGithubHook={setNewSiteSkipGithubHook}
            updateNewSiteDraft={updateNewSiteDraft}
          />
          <GithubSecretsCard adminUnlocked={adminUnlocked} {...secrets} />
          <DomainDnsCard adminUnlocked={adminUnlocked} {...domains} />
        </div>
        <div className="panel-content">
          <ConfigCard
            adminToken={adminToken}
            adminUnlocked={adminUnlocked}
            configBusy={configBusy}
            configDirty={configDirty}
            configDocument={configDocument}
            configDraft={configDraft}
            configMessage={configMessage}
            discardConfigChanges={discardConfigChanges}
            loadConfig={loadConfig}
            saveConfig={saveConfig}
            setConfigDraft={setConfigDraft}
          />
        </div>
      </section>

      <AlertsSection snapshot={snapshot} />
      <SetupHealthSection snapshot={snapshot} />
      <HostHealthSection snapshot={snapshot} />
      <ApplicationsSection
        adminUnlocked={adminUnlocked}
        applications={snapshot.applications}
        busyActionKey={busyActionKey}
        configDocument={configDocument}
        runAction={runAction}
        saveSiteSettings={saveSiteSettings}
        siteDrafts={siteDrafts}
        siteSaveKey={siteSaveKey}
        updateSiteDraft={updateSiteDraft}
      />
    </main>
  );
}
