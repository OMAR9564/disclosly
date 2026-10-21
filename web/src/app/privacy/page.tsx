import type { Metadata } from "next";

import { LegalPage } from "@/components/LegalPage";
import { loadLegal } from "@/lib/legal/content";

export const metadata: Metadata = { title: "Gizlilik · Disclosly" };

export default function Page() {
  return <LegalPage content={loadLegal("privacy")} />;
}
