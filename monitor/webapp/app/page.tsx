import { Dashboard } from "@/components/dashboard";
import { adminControlsEnabled } from "@/lib/auth";
import { getDashboardSnapshot } from "@/lib/status";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Page() {
  const snapshot = await getDashboardSnapshot();

  return <Dashboard adminControlsEnabled={adminControlsEnabled()} initialSnapshot={snapshot} />;
}
