import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import { readEditableConfig, saveEditableConfig } from "@/lib/control";
import { getDashboardSnapshot } from "@/lib/status";

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

export async function GET(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const config = await readEditableConfig();
    return NextResponse.json(config, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error);
  }
}

export async function PUT(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const body = (await request.json()) as { raw?: unknown };
    if (typeof body.raw !== "string") {
      return errorResponse(new Error("Expected a string 'raw' payload."), 400);
    }

    const config = await saveEditableConfig(body.raw);
    const snapshot = await getDashboardSnapshot();
    return NextResponse.json(
      { config, snapshot },
      {
        headers: {
          "cache-control": "no-store",
        },
      }
    );
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
