import type { Metadata, Viewport } from "next";
import { Archivo } from "next/font/google";
import "./globals.css";

const archivo = Archivo({
  subsets: ["latin", "latin-ext"],
  axes: ["wdth"],
  variable: "--font-archivo",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Disclosly",
  description:
    "KAP ve SEC açıklamaları üzerine kaynak gösteren araştırma asistanı.",
};

export const viewport: Viewport = {
  themeColor: "#181817",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // `lang` starts as Turkish; useLanguage() syncs it with the visitor's stored choice.
  return (
    <html lang="tr" className={archivo.variable}>
      <body>{children}</body>
    </html>
  );
}
