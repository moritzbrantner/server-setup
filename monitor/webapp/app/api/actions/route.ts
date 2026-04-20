import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import {
  readEditableConfig,
  runDashboardAction,
  type DashboardActionRequest,
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

function parseActionRequest(body: unknown): DashboardActionRequest {
  if (!body || typeof body !== "object") {
    throw new Error("Expected an action payload.");
  }

  const record = body as Record<string, unknown>;
  switch (record.action) {
    case "start-nginx":
    case "restart-nginx":
    case "reload-nginx":
    case "restart-webhook":
    case "restart-status-webapp":
      return { action: record.action };
    case "restart-site-service":
    case "retry-deploy":
    case "repair-site":
      if (typeof record.siteName !== "string" || !record.siteName.trim()) {
        throw new Error("This action requires a non-empty siteName.");
      }
      return { action: record.action, siteName: record.siteName.trim() };
    case "add-site":
      return {
        action: "add-site",
        repoUrl: typeof record.repoUrl === "string" ? record.repoUrl : "",
        branch: typeof record.branch === "string" ? record.branch : "",
        checkoutPath: typeof record.checkoutPath === "string" ? record.checkoutPath : "",
        email: typeof record.email === "string" ? record.email : "",
        skipGithubHook: record.skipGithubHook === true,
      };
    default:
      throw new Error("Unsupported action.");
  }
}

export async function POST(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const body = parseActionRequest(await request.json());

    const response = await runDashboardAction(body);
    const config = await readEditableConfig();
    return NextResponse.json({ ...response, config }, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
