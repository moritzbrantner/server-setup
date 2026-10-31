export function adminHeaders(adminToken: string): HeadersInit {
  return {
    "content-type": "application/json",
    "x-status-admin-token": adminToken,
  };
}

export async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    if (typeof body.error === "string" && body.error.trim()) {
      return body.error;
    }
  } catch {
    // Ignore JSON parsing errors and use the generic fallback below.
  }

  return `Request failed with ${response.status}.`;
}
