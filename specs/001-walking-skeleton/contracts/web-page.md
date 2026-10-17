# UI Contract: Placeholder Page (`/`)

Served at `https://disclosly.com`; `https://www.disclosly.com` redirects to it.

## Content (both languages required; Turkish default)

| Element | Test id | TR | EN |
|---|---|---|---|
| Product name | `product-name` | Disclosly | Disclosly |
| One-line description | `tagline` | KAP ve SEC açıklamaları üzerine kaynak gösteren araştırma asistanı | Research assistant with cited answers over KAP and SEC disclosures |
| Disclaimer (always visible) | `disclaimer` | Yatırım tavsiyesi değildir | Not investment advice |
| Backend status | `backend-status` | see states | see states |
| Language toggle | `language-toggle` | "English" | "Türkçe" |

The final copy may be refined in implementation. The test ids, the disclaimer text and the language
behaviour are fixed.

## Backend status states

| State | Trigger | TR | EN |
|---|---|---|---|
| `loading` | Request in flight | Servis durumu kontrol ediliyor… | Checking service status… |
| `available` | `/healthz` 200 with valid body | Servis çalışıyor (sürüm `abc1234`) | Service available (version `abc1234`) |
| `unavailable` | Non-200, invalid body, network error or > 5 s | Servis şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin. | Service is currently unavailable. Please try again later. |

The element exposes the state as `data-state="loading|available|unavailable"`. The page never shows
a raw error or a blank screen.

## Behaviour

- `<html lang>` matches the active language (`tr` / `en`).
- The language choice persists across reloads in the same browser. If storage is unavailable, the
  page falls back to Turkish without errors.
- The page works at 360 px width with no horizontal scroll.
- The page meets WCAG AA contrast, and the toggle is keyboard-operable.
