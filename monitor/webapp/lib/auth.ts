import { timingSafeEqual } from "node:crypto";

export const ADMIN_TOKEN_HEADER = "x-status-admin-token";

function configuredAdminToken(): string | null {
  const token = process.env.STATUS_WEBAPP_ADMIN_TOKEN?.trim();
  return token ? token : null;
}

function secureEquals(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  if (leftBuffer.length !== rightBuffer.length) {
    return false;
  }
  return timingSafeEqual(leftBuffer, rightBuffer);
}

export function adminControlsEnabled(): boolean {
  return configuredAdminToken() !== null;
}

export function requestHasAdminAccess(request: Request): boolean {
  const expectedToken = configuredAdminToken();
  if (!expectedToken) {
    return false;
  }

  const providedToken = request.headers.get(ADMIN_TOKEN_HEADER)?.trim();
  if (!providedToken) {
    return false;
  }

  return secureEquals(expectedToken, providedToken);
}
