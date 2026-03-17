import { NextResponse } from "next/server";

import { getDashboardSnapshot } from "@/lib/status";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  const snapshot = await getDashboardSnapshot();
  return NextResponse.json(snapshot, {
    headers: {
      "cache-control": "no-store",
    },
  });
}
