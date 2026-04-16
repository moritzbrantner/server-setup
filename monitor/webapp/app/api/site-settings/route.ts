import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import {
  updateSiteDeploymentSettings,
  type SiteDeploymentSettings,
} from "@/lib/control";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function unauthorizedResponse() {
  return NextResponse.json(
    {
      error:
        "Admin controls are locked. Set STATUS_WEBAPP_ADMIN_TOKEN in the webapp environment and send it with x-status-admin-token.",
    },
    { status: 401 }
  );
}

function errorResponse(error: unknown, status = 500) {
  const message = error instanceof Error ? error.message : "Request failed.";
  return NextResponse.json({ error: message }, { status });
}

function parseSettings(body: unknown): SiteDeploymentSettings {
  if (!body || typeof body !== "object") {
    throw new Error("Expected a site settings payload.");
  }

  const record = body as Record<string, unknown>;
  if (typeof record.siteName !== "string" || !record.siteName.trim()) {
    throw new Error("siteName is required.");
  }

  return {
    siteName: record.siteName.trim(),
    repoUrl: typeof record.repoUrl === "string" ? record.repoUrl : "",
    webhookRepo: typeof record.webhookRepo === "string" ? record.webhookRepo : "",
    branch: typeof record.branch === "string" ? record.branch : "",
    checkoutPath: typeof record.checkoutPath === "string" ? record.checkoutPath : "",
  };
}

export async function PUT(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const body = parseSettings(await request.json());
    const response = await updateSiteDeploymentSettings(body);
    return NextResponse.json(response, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
