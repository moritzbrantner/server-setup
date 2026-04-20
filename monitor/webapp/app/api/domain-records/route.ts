import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import {
  createDomainRecord,
  deleteDomainRecord,
  listDomainRecords,
  updateDomainRecord,
  type DomainRecordMutation,
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

function readRequiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} is required.`);
  }
  return value.trim();
}

function readSiteName(value: unknown): string {
  return readRequiredString(value, "siteName");
}

function parseMutation(body: unknown, requireId: boolean): { siteName: string; record: DomainRecordMutation } {
  if (!body || typeof body !== "object") {
    throw new Error("Expected a DNS record payload.");
  }

  const record = body as Record<string, unknown>;
  if (requireId && (typeof record.id !== "string" || !record.id.trim())) {
    throw new Error("id is required.");
  }

  return {
    siteName: readSiteName(record.siteName),
    record: {
      id: typeof record.id === "string" ? record.id : "",
      type: typeof record.type === "string" ? record.type : "",
      name: typeof record.name === "string" ? record.name : "",
      content: typeof record.content === "string" ? record.content : "",
      ttl: typeof record.ttl === "number" ? record.ttl : Number(record.ttl || 600),
      prio:
        record.prio === null || record.prio === undefined || record.prio === ""
          ? null
          : typeof record.prio === "number"
            ? record.prio
            : Number(record.prio),
    },
  };
}

export async function GET(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const url = new URL(request.url);
    const document = await listDomainRecords(readSiteName(url.searchParams.get("siteName")));
    return NextResponse.json(document, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error);
  }
}

export async function POST(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const { siteName, record } = parseMutation(await request.json(), false);
    const response = await createDomainRecord(siteName, record);
    return NextResponse.json(response, {
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
    const { siteName, record } = parseMutation(await request.json(), true);
    const response = await updateDomainRecord(siteName, record);
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
    const body = (await request.json()) as Record<string, unknown>;
    const response = await deleteDomainRecord(readSiteName(body.siteName), readRequiredString(body.id, "id"));
    return NextResponse.json(response, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
