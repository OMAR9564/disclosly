export const tr = {
  locale: "tr",
  tagline: "KAP ve SEC açıklamaları üzerine kaynak gösteren araştırma asistanı",
  disclaimer: "Yatırım tavsiyesi değildir",
  disclaimerLabel: "Yasal not",
  disclaimerBody:
    "Disclosly yalnızca şirket açıklamalarındaki bilgileri ve bu bilgilerden yapılan hesapları kaynaklarıyla gösterir. Alım, satım veya portföy önerisi vermez.",
  statusLabel: "Servis durumu",
  statusLoading: "Servis durumu kontrol ediliyor…",
  statusAvailable: "Servis çalışıyor",
  statusVersion: "sürüm",
  statusUnavailable:
    "Servis şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin.",
  boardLoading: "Kontrol",
  boardAvailable: "Çalışıyor",
  boardUnavailable: "Ulaşılamıyor",
  sourcesValue: "KAP · SEC EDGAR",
  toggleLabel: "English",
} as const;

export type Dictionary = { readonly [K in keyof typeof tr]: string };
