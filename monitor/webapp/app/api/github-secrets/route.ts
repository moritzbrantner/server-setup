import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import {
  deleteGithubSecret,
  listGithubSecrets,
  setGithubSecret,
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

function parseSiteName(request: Request): string {
  const siteName = new URL(request.url).searchParams.get("siteName")?.trim() || "";
  if (!siteName) {
    throw new Error("siteName is required.");
  }
  return siteName;
}

function parseMutationBody(body: unknown): { siteName: string; name: string; value?: string } {
  if (!body || typeof body !== "object") {
    throw new Error("Expected a repository secret payload.");
  }

  const record = body as Record<string, unknown>;
  const siteName = typeof record.siteName === "string" ? record.siteName.trim() : "";
  const name = typeof record.name === "string" ? record.name.trim() : "";
  if (!siteName) {
    throw new Error("siteName is required.");
  }
  if (!name) {
    throw new Error("name is required.");
  }

  return {
    siteName,
    name,
    value: typeof record.value === "string" ? record.value : undefined,
  };
}

export async function GET(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const document = await listGithubSecrets(parseSiteName(request));
    return NextResponse.json(document, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}

export async function PUT(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const body = parseMutationBody(await request.json());
    const response = await setGithubSecret(body.siteName, body.name, body.value || "");
    return NextResponse.json(response, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}

export async function DELETE(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const body = parseMutationBody(await request.json());
    const response = await deleteGithubSecret(body.siteName, body.name);
    return NextResponse.json(response, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
