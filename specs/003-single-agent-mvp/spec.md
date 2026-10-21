# Feature Specification: Single-Agent MVP

**Feature Branch**: `003-single-agent-mvp`

**Created**: 2026-10-21

**Status**: Draft

**Input**: User description: "PROJECT_SPEC.md Phase 3 (Section 20): ModelRouter with fallback + cache; one tool-calling agent; SSE UI with citations; auth, quotas, Turnstile, disclaimer, privacy page. Acceptance: real users can sign in and get cited answers; provider fallback demonstrated; closed beta to Numiver users." Acceptance revised by the owner on 2026-10-21: Numiver has no users yet, so this phase is accepted with the owner and allowlisted test accounts; real users come with the Phase 5 launch.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask a question and get a cited answer (Priority: P1)

A signed-in user types a question in Turkish or English about a company in the corpus (for example "THYAO'nun son 4 çeyrekteki net kârı nasıl değişti?"). While the answer is being prepared, they see live progress: which documents were searched and which tools were used. They then get an answer in the question's language where every number and factual claim links to its source (document, period, section or page). Clicking a citation opens the source excerpt. Every answer ends with "Yatırım tavsiyesi değildir / Not investment advice".

**Why this priority**: This is the product. Without cited answers there is nothing to put in front of users.

**Independent Test**: Sign in with a test account, ask a set of Turkish and English questions covering the ingested companies, and check that each answer streams progress, ends with the disclaimer, and that every number in it opens a source excerpt containing that number.

**Acceptance Scenarios**:

1. **Given** a signed-in user, **When** they ask a question about an ingested company, **Then** they see progress events within 3 seconds and a final answer with at least one citation.
2. **Given** an answer containing numbers, **When** the user clicks a citation, **Then** the cited excerpt is shown with its document, period and section or page, and it contains the cited value.
3. **Given** a question in Turkish, **When** the answer is returned, **Then** it is in Turkish; the same holds for English.
4. **Given** a question asking whether to buy or sell, or for a price target, **When** it is submitted, **Then** the system declines to advise, offers factual information instead where possible, and shows the disclaimer.
5. **Given** a question for which the corpus has no supporting evidence, **When** it is answered, **Then** the system says it could not find a source rather than giving an unsupported number.
6. **Given** the agent hits its call limit or time limit, **When** the run stops, **Then** the user gets the partial answer clearly marked as partial.

---

### User Story 2 - Answers keep working when a free provider fails (Priority: P1)

When the preferred free model provider returns a rate-limit error, a server error or times out, the system automatically switches to the next configured provider and the user still gets an answer. Repeated identical requests are served from cache without using any provider quota.

**Why this priority**: The $0 budget means free-tier limits are hit daily. Without fallback and caching the product breaks within hours. "Provider fallback demonstrated" is a phase acceptance criterion.

**Independent Test**: With recorded responses, force the first provider to fail and check that the run completes on a later provider, the failed provider is skipped during its cooldown, and the switch is visible in the run's trace. Repeat an identical request and check it is served from cache.

**Acceptance Scenarios**:

1. **Given** the first provider returns a rate-limit response with a retry-after hint, **When** a model call is made, **Then** the call succeeds on the next provider and the first provider is not tried again until its cooldown ends.
2. **Given** all providers are unavailable, **When** a user asks a question, **Then** they get a clear "temporarily unavailable, try later" message, not a crash or a blank screen.
3. **Given** a provider's tracked daily usage is near its known limit, **When** a call is made, **Then** that provider is skipped proactively.
4. **Given** a model call identical to an earlier one (same normalised prompt, model, parameters and tool schema version), **When** it is made, **Then** the cached response is returned and no provider is called.
5. **Given** any model call, **When** it completes, **Then** its provider, model, token counts, latency, cache hit flag and estimated would-have-cost are recorded in telemetry.

---

### User Story 3 - Sign in, quotas and abuse protection (Priority: P2)

An allowlisted user (the owner or a test account in this phase) signs in with Google or GitHub. Each user has a daily question quota; the UI shows how many questions remain and, when the quota is used up, explains that it resets tomorrow. Anonymous endpoints are protected against bots, and requests are rate-limited per IP and per user.

**Why this priority**: Required to open the product to real users safely, but it only protects Story 1; it is not useful alone.

**Independent Test**: Sign in with each provider, use up a small test quota, and check the quota indicator, the exhausted-quota message and the rate-limit responses. Check that an anonymous request without a valid bot-check token is rejected.

**Acceptance Scenarios**:

1. **Given** a visitor who is not signed in, **When** they try to ask a question, **Then** they are asked to sign in with Google or GitHub.
2. **Given** a user who is not on the beta access list, **When** they sign in, **Then** they see a "closed beta" message and cannot run questions.
3. **Given** a signed-in user with quota left, **When** they ask a question, **Then** the remaining count shown decreases by one.
4. **Given** a user who has used their daily quota, **When** they ask another question, **Then** the request is refused with a friendly message saying when the quota resets.
5. **Given** a client exceeding the per-IP or per-user rate limit, **When** it sends more requests, **Then** it receives a stable "rate limited" error code and message.
6. **Given** a session token that has expired, **When** it is used, **Then** the user is asked to sign in again.

---

### User Story 4 - Legal pages and disclosures (Priority: P2)

Any visitor can read a privacy policy (KVKK-compliant, in Turkish and English), terms of use and an investment disclaimer. The privacy policy states which personal data is stored (email and display name only), that only public disclosure data is sent to third-party model providers, and that some free providers may use prompts for training.

**Why this priority**: A legal precondition for letting real Turkish users in; small in effort.

**Independent Test**: Open each page signed out, in both languages, and check required statements are present; check that the sign-in screen links to the privacy policy and terms.

**Acceptance Scenarios**:

1. **Given** a signed-out visitor, **When** they open the privacy, terms or disclaimer page, **Then** it loads in the selected language.
2. **Given** the sign-in screen, **When** it is shown, **Then** it links to the privacy policy and terms.

---

### User Story 5 - Turkish/English interface (Priority: P3)

The interface is in Turkish by default with an English toggle; the choice is remembered. The layout works on a phone.

**Why this priority**: Beta users are Turkish; English matters for the SEC demo. Turkish-only would still be usable.

**Independent Test**: Toggle the language and reload; check all visible UI text changes and the choice persists; check the ask and answer screens at phone width.

**Acceptance Scenarios**:

1. **Given** a first visit, **When** the page loads, **Then** the UI is in Turkish.
2. **Given** the user switches to English, **When** they reload, **Then** the UI stays in English.

---

### Edge Cases

- The model returns output that does not match the expected structure: the system re-asks once, then fails safely with a clear message.
- Retrieved document text contains instructions ("ignore previous instructions…"): the agent treats it as data and does not follow it.
- The live progress connection drops mid-answer: the user can reload and still see the final answer for that run.
- A question names a company not in the corpus: the answer says the company is not covered and lists that coverage is limited.
- Two tabs submit questions at once near the quota limit: the quota is never exceeded.
- A provider returns a success response that is empty or truncated: treated as a failure and the next provider is tried.
- The user's display name or email contains unusual characters: stored and shown correctly; emails never appear in logs.

## Requirements *(mandatory)*

### Functional Requirements

**Model access**

- **FR-001**: System MUST route model calls through a single router supporting the `cheap` and `strong` task classes, with a configurable ordered list of free providers per class; model identifiers MUST come from configuration, not code.
- **FR-002**: On rate-limit, server error, timeout, or empty/invalid response, the router MUST mark the provider as cooling down (respecting any retry-after hint) and try the next provider.
- **FR-003**: The router MUST track per-provider daily usage and skip a provider proactively when its configured daily quota is nearly used.
- **FR-004**: The router MUST present one interface for tool calling across providers; for a provider without native tool calling it MUST use schema-constrained output with validation.
- **FR-005**: The router MUST cache responses keyed on normalised prompt, model, parameters and tool schema version, in durable storage with a fast in-memory layer; a cache hit MUST make no provider call.
- **FR-006**: Every model call MUST record provider, model, input/output tokens, latency, cache hit, error (if any) and estimated would-have-cost in telemetry, with the user identifier hashed.

**Agent**

- **FR-007**: A single tool-calling agent MUST answer questions using the existing document search, financial facts and document listing capabilities as tools.
- **FR-008**: Numbers in answers MUST come from tool results (financial facts or retrieved chunks), never from model memory; every number and factual claim MUST carry a citation to document, period and section or page.
- **FR-009**: The agent MUST enforce in code a maximum of 4 model calls and a 90-second deadline per run by default (configurable); on reaching a limit it MUST return a partial answer marked as partial.
- **FR-010**: All model outputs MUST be validated against a schema; on failure the agent MUST re-ask once, then fail safely. The re-ask counts toward the model-call limit in FR-009; if no call is left, the agent returns a partial answer instead of re-asking.
- **FR-011**: Retrieved text and tool outputs MUST be wrapped as untrusted data and never treated as instructions.
- **FR-012**: The agent MUST detect advice-seeking questions (buy/sell, price targets, portfolio advice) in Turkish and English and respond with information only.
- **FR-013**: Every answer MUST end with "Yatırım tavsiyesi değildir / Not investment advice" and MUST be in the language of the question.
- **FR-014**: When no supporting evidence is found, the answer MUST say so instead of guessing.

**Streaming and UI**

- **FR-015**: Users MUST be able to start a run and receive live events (search performed, tool called, answer text, final answer) as they happen.
- **FR-016**: A completed run's final answer MUST be retrievable later by its run identifier by the user who created it.
- **FR-017**: Each citation in the UI MUST be clickable and show the source excerpt with document, period and section or page.
- **FR-018**: The UI MUST show the user's remaining daily questions and a friendly message when the quota is exhausted.
- **FR-019**: The UI MUST be in Turkish by default with an English toggle that persists, and be usable at phone width.

**Access and protection**

- **FR-020**: Users MUST sign in with Google or GitHub; no passwords are stored. Sessions MUST expire after a short period and require re-authentication.
- **FR-021**: Only users on a beta access list MUST be able to run questions during the closed beta; others see a closed-beta message. The beta list is an owner-maintained email allowlist; the owner can add or remove emails without a redeploy, and a change takes effect on the user's next request.
- **FR-022**: System MUST enforce a configurable daily question quota per user (default 20 per day) atomically, so concurrent requests cannot exceed it.
- **FR-023**: System MUST rate-limit requests per IP and per user and return a stable error code and message when limited.
- **FR-024**: Anonymous endpoints MUST require a valid bot-protection check.
- **FR-025**: Only email and display name MUST be stored as personal data; emails MUST be masked in logs and traces.

**Legal**

- **FR-026**: System MUST provide privacy policy (KVKK), terms of use and disclaimer pages in Turkish and English, reachable without signing in and linked from the sign-in screen.

**Evaluation**

- **FR-027**: The agent MUST ship with an eval subset (Turkish and English) covering cited numeric answers, advice refusal and injection resistance, replayable in CI from recorded responses.
- **FR-028**: A fallback demonstration MUST exist as an automated test (recorded responses) and be shown once against live providers, with the trace recorded.

### Key Entities

- **User**: a person signed in via Google or GitHub; email, display name, sign-in provider, beta access flag, language preference.
- **Run**: one question and its answer; owner, question, language, status (running, completed, partial, failed), events, final answer, usage (model calls, tokens, providers used), timestamps.
- **Answer**: the final text in the question's language, its citations, a partial flag and the disclaimer.
- **Citation**: a link from a claim or number in an answer to a stored chunk or financial fact (document, period, section/page, excerpt).
- **Provider**: a configured model source with task classes, cooldown state and daily usage count.
- **Cached Response**: a stored model response keyed by the normalised request, with hit count.
- **Quota Usage**: questions used per user per day.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On production, the owner and at least 3 allowlisted test accounts (Google and GitHub both used) sign in and each receives a cited answer in Turkish and in English.
- **SC-002**: In the answer eval subset, 100% of numbers in answers have a citation, and at least 80% of numeric answers match the expected value within tolerance.
- **SC-003**: 100% of answers carry the disclaimer; 100% of the advice-refusal eval cases are refused.
- **SC-004**: When the first provider fails, at least 95% of runs still complete on another provider (demonstrated in the recorded fallback test and once live).
- **SC-005**: Users see the first progress update within 3 seconds and the final answer within 30 seconds for 95% of simple questions.
- **SC-006**: Repeated identical questions use zero provider calls; the cache hit rate is measured and reported.
- **SC-007**: No run exceeds 4 model calls or 90 seconds; daily quotas are never exceeded, including under concurrent requests.
- **SC-008**: No email, token or key appears in logs or traces in an automated scan of a test run's output.

## Assumptions

- Phase 2 corpus, hybrid search and financial facts are in place and reused as agent tools; no new ingestion work in this phase.
- This phase uses one agent with tools only; the multi-node graph, plan approval, sandbox, verifier loop, long-term memory and checkpoint resume belong to Phase 4. Numbers are taken from stored facts and chunks, not computed in a sandbox.
- BYOK, the MCP server, feedback buttons, history and watchlist pages, examples on the landing page and the report cache are out of scope (later phases); the quota message says "try tomorrow" only.
- Default daily quota is 20 questions per user, configurable, since only the simple path exists (PROJECT_SPEC §22 leaves exact quotas open).
- The app runs standalone at disclosly.com. Numiver has no users yet, so there is no Numiver integration or Numiver beta in this phase; access is limited to the owner and allowlisted test accounts, and real users come with the Phase 5 public launch (PROJECT_SPEC §20, revised 2026-10-21).
- Answers are plain structured text with citations; tables and charts in reports come with Phase 4.
- The privacy policy and terms are drafted by the agent for owner review; legal sign-off is the owner's responsibility.
- Provider set for this phase is the free tiers listed in PROJECT_SPEC §8.2, as many as the owner can obtain keys for; at least two are needed to demonstrate fallback.
