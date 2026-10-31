import type { DashboardActionRequest, EditableConfigDocument } from "@/lib/control";
import type { DashboardSnapshot, SiteCheck, StatusLevel } from "@/lib/status";

export function formatMetric(value: number | null, suffix = ""): string {
  if (value === null || Number.isNaN(value)) {
    return "n/a";
  }

  return `${value}${suffix}`;
}

export function formatTimestamp(value: string | null | undefined): string {
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

export function formatStatus(value: string): string {
  return value.replace(/[-_]/g, " ");
}

export function pillTone(status: string): string {
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

export function countAlerts(snapshot: DashboardSnapshot, level: "critical" | "warning"): number {
  return snapshot.alerts.filter((alert) => alert.level === level).length;
}

export function setupOverview(status: StatusLevel): string {
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

export function actionLabel(action: DashboardActionRequest["action"]): string {
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
    case "repair-site":
      return "Repair site";
    case "add-site":
      return "Add website";
  }
}

export function actionKey(action: DashboardActionRequest): string {
  if ("siteName" in action) {
    return `${action.action}:${action.siteName}`;
  }
  return action.action;
}

export function configModeLabel(kind: EditableConfigDocument["kind"]): string {
  return kind === "registry" ? "deploy registry" : "monitor list";
}

export function pushDeployTone(application: SiteCheck): string {
  return pillTone(application.pushDeploy?.status || "unknown");
}
