# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary:** Turkish retail investors (Numiver users) researching BIST companies, funds and IPO
  prospectuses (halka arz izahnameleri). They read in Turkish.
- **Secondary:** international recruiters and technical reviewers trying the English demo over SEC
  10-K and 10-Q filings.
- **Power users / developers:** connect through MCP from their own AI client.

## Product Purpose

Disclosly answers questions about company disclosures (KAP and SEC EDGAR). Every number links to
its source document and page, the system checks its own answers, and it runs in production on a
$0 LLM budget.

## Positioning

Cited, verifiable answers built from primary disclosures, not opinions. It gives information and
calculations from documents only, never buy/sell advice.

## Operating Context

Users compare figures across quarters and companies and check them against the original filings.
The current phase ships only a placeholder page: name, one-line description, disclaimer, live
backend status and a TR/EN toggle (specs/001-walking-skeleton/contracts/web-page.md).

## Capabilities and Constraints

- UI in Turkish (default) and English; the choice persists per browser.
- The disclaimer "Yatırım tavsiyesi değildir / Not investment advice" appears on every report and
  on the placeholder page. No investment advice (Turkish regulation, KVKK).
- Next.js on Vercel free plan; Tailwind v4; no new dependencies without owner approval.

## Brand Commitments

Name: Disclosly (working name). No logo, palette or font is fixed yet.

## Evidence on Hand

No users, usage numbers, testimonials or eval results exist yet. Do not invent them.

## Product Principles

1. Source before claim: every figure is traceable to a document.
2. Information, never advice.
3. Honest status: show what works and what does not, without raw errors.
4. Frugal by design: minimal model calls and minimal client weight.

## Accessibility & Inclusion

WCAG AA contrast; keyboard-operable controls; works at 360 px width.
