import { Dashboard } from "@/components/dashboard";
import { getDashboardSnapshot } from "@/lib/status";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Page() {
  const snapshot = await getDashboardSnapshot();

  return <Dashboard initialSnapshot={snapshot} />;
}
