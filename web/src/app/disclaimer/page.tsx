import type { Metadata } from "next";

import { LegalPage } from "@/components/LegalPage";
import { loadLegal } from "@/lib/legal/content";

export const metadata: Metadata = { title: "Yasal uyarı · Disclosly" };

export default function Page() {
  return <LegalPage content={loadLegal("disclaimer")} />;
}
