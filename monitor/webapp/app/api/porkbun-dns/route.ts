import { NextResponse } from "next/server";

import { requestHasAdminAccess } from "@/lib/auth";
import {
  createPorkbunDnsRecord,
  deletePorkbunDnsRecord,
  listPorkbunDnsRecords,
  updatePorkbunDnsRecord,
  type PorkbunDnsRecordDraft,
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

function parseRecordBody(body: unknown, requireId: boolean): PorkbunDnsRecordDraft {
  if (!body || typeof body !== "object") {
    throw new Error("Expected a Porkbun DNS record payload.");
  }

  const record = body as Record<string, unknown>;
  const domain = typeof record.domain === "string" ? record.domain.trim() : "";
  const id = typeof record.id === "string" ? record.id.trim() : "";
  const name = typeof record.name === "string" ? record.name : "";
  const type = typeof record.type === "string" ? record.type.trim() : "";
  const content = typeof record.content === "string" ? record.content : "";
  if (!domain) {
    throw new Error("domain is required.");
  }
  if (requireId && !id) {
    throw new Error("id is required.");
  }
  if (!type) {
    throw new Error("type is required.");
  }
  if (!content.trim()) {
    throw new Error("content is required.");
  }

  return {
    domain,
    id: id || undefined,
    name,
    type,
    content,
    ttl: typeof record.ttl === "string" ? record.ttl : "",
    prio: typeof record.prio === "string" ? record.prio : "",
    notes: typeof record.notes === "string" ? record.notes : "",
  };
}

function parseDeleteBody(body: unknown): { domain: string; id: string } {
  if (!body || typeof body !== "object") {
    throw new Error("Expected a Porkbun DNS delete payload.");
  }

  const record = body as Record<string, unknown>;
  const domain = typeof record.domain === "string" ? record.domain.trim() : "";
  const id = typeof record.id === "string" ? record.id.trim() : "";
  if (!domain) {
    throw new Error("domain is required.");
  }
  if (!id) {
    throw new Error("id is required.");
  }
  return { domain, id };
}

export async function GET(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const domain = new URL(request.url).searchParams.get("domain")?.trim() || "";
    const document = await listPorkbunDnsRecords(domain);
    return NextResponse.json(document, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}

export async function POST(request: Request) {
  if (!requestHasAdminAccess(request)) {
    return unauthorizedResponse();
  }

  try {
    const response = await createPorkbunDnsRecord(parseRecordBody(await request.json(), false));
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
    const response = await updatePorkbunDnsRecord(parseRecordBody(await request.json(), true));
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
    const body = parseDeleteBody(await request.json());
    const response = await deletePorkbunDnsRecord(body.domain, body.id);
    return NextResponse.json(response, {
      headers: {
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    return errorResponse(error, error instanceof SyntaxError ? 400 : 500);
  }
}
