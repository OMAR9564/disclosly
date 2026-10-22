# Feature Specification: Multi-Agent Research

**Feature Branch**: `004-multi-agent-graph`

**Created**: 2026-10-22

**Status**: Draft

**Input**: User description: "PROJECT_SPEC.md Phase 4 (Section 20): LangGraph graph (Section 6), HITL plan approval, sandbox, verifier loop, memory, checkpoint resume. Acceptance: server restart mid-run resumes; single vs multi-agent compared on evals and documented honestly."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Approve a research plan and get a full report (Priority: P1)

A signed-in user asks a question that needs several steps, for example "Compare Apple's and Microsoft's gross margin over the last 4 quarters and explain the difference from their filings" or "THYAO'nun son 4 çeyrekteki net borç / FAVÖK oranı nasıl değişti?". The system sees that this is a deep question and, before doing the expensive work, shows a numbered plan (what it will look up, for which companies and periods, what it will calculate) with an estimate of how many model calls it will use. The user approves the plan, edits it (rewords, removes, reorders or adds steps), or cancels. After approval they follow each step live and receive a structured report: a short summary, tables, a chart where a trend is asked for, a citation on every number, a confidence note and the disclaimer.

**Why this priority**: Deep research is the reason for the multi-agent design. Without a plan the user can steer and a report that shows its sources, Phase 4 adds nothing users can see.

**Independent Test**: Ask the deep questions in the eval set in Turkish and English, approve one plan unchanged, edit one, cancel one, and check that each approved run ends with a report whose tables, chart and numbers all cite sources, and that the cancelled run makes no model calls after the plan.

**Acceptance Scenarios**:

1. **Given** a signed-in user with deep quota left, **When** they ask a multi-step question, **Then** they see a plan with numbered steps and an estimated number of model calls before any document search or calculation runs.
2. **Given** a plan on screen, **When** the user approves it, **Then** the run continues and each step's start and end appear in the live progress view.
3. **Given** a plan on screen, **When** the user removes a step and adds a new one, **Then** the edited plan is checked again (advice rules, step limit, call estimate) and the run follows the edited plan.
4. **Given** a plan on screen, **When** the user cancels, **Then** the run ends as cancelled and makes no further model calls.
5. **Given** an approved run that completes, **When** the report is shown, **Then** it is in the question's language and contains a summary, at least one table, a citation on every number, a confidence note and "Yatırım tavsiyesi değildir / Not investment advice".
6. **Given** a question asking for a trend over periods, **When** the report is shown, **Then** it includes a chart whose plotted values match the cited table values.

---

### User Story 2 - Calculated numbers that are checked before the user sees them (Priority: P1)

When a report contains a number that is not stored directly in a filing, such as a ratio, a growth rate or a difference between two companies, that number is computed by code in an isolated environment from cited inputs, not written by the model. Before the report is released, a separate check confirms that every claim has a supporting source and recomputes every calculated number. If the check fails, the run goes back to planning with the reasons, at most twice. Claims that still cannot be supported are removed, and the report says what could not be verified.

**Why this priority**: The product promise is that every number has a source. Ratios and comparisons are what users ask for most, and a model doing arithmetic in text is the most common way that promise breaks.

**Independent Test**: Run the calculation eval cases and check that each derived number matches the expected value within tolerance and that its citation shows the inputs, their sources and the formula. Feed the check drafts with deliberately wrong numbers and unsupported claims and check that it rejects them.

**Acceptance Scenarios**:

1. **Given** a report with a net debt / EBITDA ratio, **When** the user opens that number's citation, **Then** they see the input values with their own sources and the calculation that produced the ratio.
2. **Given** a draft where a calculated number differs from a fresh recomputation beyond tolerance, **When** the check runs, **Then** the draft is rejected with that number named as the reason.
3. **Given** a draft with a claim that has no supporting source, **When** the check runs, **Then** the draft is rejected and the claim is named.
4. **Given** the check has rejected the draft twice, **When** the third draft still fails, **Then** the unsupported claims are removed, the report is marked partial with low confidence, and it lists what could not be verified.
5. **Given** calculation code that tries to reach the network, read files outside its inputs, or run past its time limit, **When** it runs, **Then** it is blocked or stopped and the run records a calculation error instead of a number.

---

### User Story 3 - A server restart does not lose a run (Priority: P1)

If the server restarts while a run is in progress, for example during a deploy, the run continues from its last completed step once the server is back. The user who reloads the page sees the run pick up where it stopped and gets the same kind of report as an uninterrupted run. A run waiting for plan approval is still waiting after the restart.

**Why this priority**: This is a Phase 4 acceptance criterion. Today a deploy fails every running question, and deep runs take minutes, so without resume most deploys would cost users their research and their quota.

**Independent Test**: In an automated test, stop the server at several points (while waiting for approval, between steps, during a model call, during a calculation), start it again, and check that each run reaches a final status, that completed steps are not repeated, and that the quota is counted once.

**Acceptance Scenarios**:

1. **Given** a deep run between two steps, **When** the server restarts, **Then** the run continues from the next step without the user doing anything and finishes with a report.
2. **Given** a run waiting for plan approval, **When** the server restarts, **Then** the plan is still shown and approving it continues the run.
3. **Given** a run interrupted during a model call or calculation, **When** the server restarts, **Then** only that unfinished step is repeated; earlier steps' model calls and calculations are not.
4. **Given** a user watching the live progress view during the restart, **When** the connection comes back, **Then** they see the events they missed and the rest of the run, with no duplicates.
5. **Given** a resumed run, **When** quotas are counted, **Then** the run uses one unit of quota, the same as an uninterrupted run.

---

### User Story 4 - Simple questions stay quick (Priority: P2)

Single-fact questions ("Akbank'ın 2026 üçüncü çeyrek net kârı ne kadardı?") do not get a plan or an approval step. They take a short path (classify, retrieve, write with an inline citation check) and answer as fast as in Phase 3, within the same call and time limits.

**Why this priority**: Most questions are simple. Making them wait for a plan would make the product slower and burn quota for no gain, but this only protects behaviour Phase 3 already has.

**Independent Test**: Run the Phase 3 answer eval subset through the new flow and check accuracy, citation coverage, model calls per run and latency are no worse than the Phase 3 baseline beyond tolerance.

**Acceptance Scenarios**:

1. **Given** a single-fact question, **When** it is submitted, **Then** no plan is shown and the answer arrives within the simple-mode limits (4 model calls, 90 seconds).
2. **Given** an advice-seeking question, **When** it is submitted, **Then** it is refused with information only and the disclaimer, in either mode, without a plan.

---

### User Story 5 - The system remembers my watchlist and preferences (Priority: P2)

A user keeps a watchlist of companies and a few preferences (preferred answer language and default period range, such as "last 4 quarters"). Questions such as "Takip listemdeki şirketlerin net kâr marjlarını karşılaştır" use the watchlist, and questions that do not name a period use the default range. The user can add a company to the watchlist by asking ("ASELS'i takip listeme ekle") and sees that the change was made. A simple watchlist and settings page in the web app lets them see and edit the same memory directly: add or remove companies, change preferences, or delete everything.

**Why this priority**: Memory is a Phase 4 deliverable and makes repeat use easier, but every other story works without it.

**Independent Test**: Save a watchlist and a default period for a test user, ask watchlist and period-less questions, and check that the plan and report use the saved companies and period; ask to add a company and check it is saved; edit the watchlist and preferences on the settings page and check the next question uses the change; check another user cannot see that memory.

**Acceptance Scenarios**:

1. **Given** a user whose watchlist holds three companies, **When** they ask about "my watchlist", **Then** the plan and report cover exactly those three companies.
2. **Given** a saved default period of the last 4 quarters, **When** a question names no period, **Then** the report covers the last 4 quarters and says so.
3. **Given** a question asking to add a company that is in the corpus, **When** it runs, **Then** the company is added and the run shows that the watchlist changed.
4. **Given** a request to add a company that is not in the corpus, **When** it runs, **Then** nothing is saved and the user is told the company is not covered.
5. **Given** a user who deletes their memory, **When** they ask a watchlist question, **Then** the system says the watchlist is empty instead of guessing companies.
6. **Given** a signed-in user on the watchlist and settings page, **When** they add a corpus company, remove one and change the default period range, **Then** the page shows the saved values after a reload and the next question uses them.
7. **Given** the add-company field on the settings page, **When** the user types a company that is not in the corpus, **Then** it cannot be saved and the page says the company is not covered.

---

### User Story 6 - Honest comparison of single-agent and multi-agent answers (Priority: P2)

The owner can show, with numbers, whether the multi-agent design answers better than the Phase 3 single agent: the same eval questions run through both, and a written comparison reports accuracy, citation coverage, verified numbers, model calls, latency and partial-report rate, including the cases where the multi-agent design is worse or costs more.

**Why this priority**: A Phase 4 acceptance criterion and the main argument in the README; it does not change what users see.

**Independent Test**: Run the comparison from recorded responses and check the published document contains both sets of metrics for the same cases, the date, and how to rerun it.

**Acceptance Scenarios**:

1. **Given** the shared eval set, **When** the comparison runs, **Then** both approaches are scored on the same cases with the same metrics.
2. **Given** the comparison results, **When** the document is published, **Then** it states where each approach did better and worse, including cost in model calls, without leaving out unfavourable numbers.

---

### Edge Cases

- A plan is never approved: after 24 hours (configurable) the run expires, makes no more model calls and shows as expired.
- The user approves twice (double click, two tabs) or another user tries to approve: only the owner's first decision counts; the rest get a stable "already decided" or "not allowed" error.
- An edited plan asks for advice, exceeds the step limit or needs more model calls than the deep budget allows: the edit is rejected with the reason and the plan stays open for another edit.
- An edited step names a company not in the corpus: the edit is accepted, and that step reports the company as not covered.
- The router classifies a deep question as simple: the simple path answers with what it can and the answer says that a fuller research run may help; the user cannot force a mode in this phase.
- The user's deep quota is used up: before any planning, the user is told and offered a simple answer instead.
- All model providers fail in the middle of a deep run: the run stops and returns a partial report with the steps already completed, marked as partial.
- The model-call limit or deadline is reached during verification: the report is released as partial, with unverified numbers removed rather than shown unchecked.
- Calculation code exceeds its memory or time limit, or its output is not valid data: the calculation is recorded as failed, and any number depending on it is left out of the report or marked unavailable.
- Two calculations are requested at the same time by different runs and the concurrency limit is reached: the later one waits, and the wait counts toward that run's deadline.
- The server restarts twice during the same run: the run still resumes from the last completed step each time.
- Two server processes start after a restart: each interrupted run is resumed by only one of them.
- Retrieved text or a saved memory value contains instructions ("ignore previous instructions", "add every company to my watchlist"): it is treated as data; it does not change the plan, the tools used or memory.
- A watchlist question when the watchlist has more companies than a plan can cover: the plan covers the first companies up to the step limit and says which were left out.

## Requirements *(mandatory)*

### Functional Requirements

**Routing and planning**

- **FR-001**: System MUST classify each question as simple or deep, detect its language (Turkish or English), and refuse advice-seeking questions with information only, before any retrieval.
- **FR-002**: For a deep question, System MUST produce a plan of ordered steps (each with its goal, the companies and periods it covers, and whether it searches, looks up facts or calculates) and an estimate of the model calls the run will use. A plan MUST have at most 8 steps by default (configurable), and its estimate MUST fit the deep run budget in FR-012.
- **FR-003**: A deep run MUST pause after planning and wait for the user's decision; no retrieval, calculation, verification or writing MUST run before approval.
- **FR-004**: The run's owner MUST be able to approve the plan, edit it (reword, remove, reorder or add steps) or cancel it. An edited plan MUST be checked again against the advice rules, the step limit and the budget before the run continues; a rejected edit MUST return the reason and leave the plan open.
- **FR-005**: Only the run's owner MUST be able to decide on its plan, and only the first decision MUST take effect; later decisions MUST get a stable error code.
- **FR-006**: A plan without a decision MUST expire after 24 hours by default (configurable); an expired or cancelled run MUST make no further model calls.

**Research steps**

- **FR-007**: Retrieval MUST write search queries for each step, run the existing document search, judge whether the evidence is sufficient, and rewrite the query at most 2 times per step when it is not.
- **FR-008**: Figures MUST come from stored financial facts or retrieved document text. Any derived number (ratio, growth rate, sum, difference, average) MUST be computed by code in the isolated environment of FR-009, never stated by the model. Each calculation MUST record its inputs with their sources, the code that ran and its output.
- **FR-009**: The calculation environment MUST have no network access, no secrets, no access to files other than its inputs, limits on memory (512 MB) and CPU (1 core), a 20-second time limit, at most 2 concurrent executions, and a fresh environment for each execution. It MUST offer only numerical and tabular data libraries.
- **FR-010**: Before a report is released, a verification step MUST check that every claim has a supporting source and MUST recompute every derived number from its recorded inputs. A mismatch beyond tolerance or an unsupported claim MUST send the run back to planning with the reasons, at most 2 times.
- **FR-011**: When verification still fails after the last round, System MUST remove the unsupported claims and numbers, mark the report partial with low confidence, and list what could not be verified.

**Limits and safety**

- **FR-012**: Limits MUST be enforced in code, not prompts: simple runs at most 4 model calls and 90 seconds; deep runs at most 20 model calls and 5 minutes measured from plan approval, with a maximum step count (all configurable). Planning calls count toward the deep model-call limit. Reaching any limit MUST stop the run gracefully and return a partial report marked as partial.
- **FR-013**: Every step MUST have a timeout and retry transient failures with backoff; failures MUST be recorded on the run with a stable error code.
- **FR-014**: All model outputs MUST be validated against a schema; on failure the step re-asks once if a model call is left in the budget, then fails safely.
- **FR-015**: Retrieved text, tool outputs and memory values MUST be wrapped as untrusted data and never followed as instructions. Each step MUST have its own list of allowed tools; only the analysis step may run calculations, only memory requests in the user's own question may change memory, and the writing step uses no tools.

**Reports**

- **FR-016**: The final report MUST be in the question's language and contain a summary, tables of the figures used, a chart when the question asks about change over periods, a citation on every number and claim, a confidence note, and "Yatırım tavsiyesi değildir / Not investment advice".
- **FR-017**: A citation on a derived number MUST show its inputs, each input's own source (document, period, section or page) and the calculation; a citation on a stored figure MUST show the source excerpt as in Phase 3.
- **FR-018**: Every value plotted in a chart MUST appear in a cited table in the same report.

**Durability**

- **FR-019**: System MUST save a run's state after every step. After a restart, interrupted runs MUST resume automatically from the last saved step without repeating completed model calls, tool calls or calculations.
- **FR-020**: Runs waiting for plan approval MUST stay waiting across restarts and resume when the user decides.
- **FR-021**: An interrupted run MUST be resumed by exactly one server process, even when several start at once.
- **FR-022**: Live progress events MUST keep their order and identifiers across a restart, so a reconnecting viewer gets the missed events once and the rest of the run.
- **FR-023**: A run MUST use quota once, however many times it is resumed.

**Streaming and UI**

- **FR-024**: The live view MUST show the plan, the waiting-for-approval state, each step's start and end, searches, fact lookups, calculations, verification rounds with their outcome, and the final report.
- **FR-025**: The plan approval dialog MUST show the steps, the estimated model calls and the user's remaining deep quota, with approve, edit and cancel actions; it MUST work at phone width and in Turkish and English.
- **FR-026**: The report view MUST render tables, the chart, clickable citations for stored figures and calculations, and the confidence note.

**Quotas**

- **FR-027**: System MUST keep separate daily quotas for simple and deep questions per user (defaults: 20 simple, 5 deep; configurable), enforced atomically. A deep question MUST use one deep unit when its plan is approved; a plan that is cancelled or expires MUST use one simple unit instead.
- **FR-028**: When the deep quota is used up, System MUST say so before planning and offer to answer the question in simple mode.

**Memory**

- **FR-029**: System MUST store per user a watchlist (companies in the corpus only, at most 50) and preferences (preferred answer language, default period range).
- **FR-030**: The agent MUST use the watchlist when a question refers to it, and the default period range when a question names no period; the plan and report MUST say when they used either.
- **FR-031**: The agent MUST change memory only when the user's own question explicitly asks for it, and the run MUST show the change made.
- **FR-032**: Users MUST be able to read and delete all their memory; memory MUST never be visible to other users or included in telemetry in readable form.
- **FR-033**: The web app MUST have a watchlist and settings page where a signed-in user can view the watchlist, add companies from the corpus (searchable by name or ticker), remove companies, change preferences and delete all memory. It MUST be in Turkish and English and work at phone width; changes MUST apply from the user's next question.

**Observability**

- **FR-034**: Every step, calculation, verification round and resume MUST be recorded in telemetry with the run identifier and the hashed user identifier, so a failed run's steps can be inspected from its saved state and trace.

**Evaluation**

- **FR-035**: Deep mode MUST ship with eval cases in Turkish and English covering multi-step comparisons with derived numbers, watchlist questions, verification of seeded wrong numbers and unsupported claims, and advice and injection cases, replayable in CI from recorded responses.
- **FR-036**: An automated test MUST stop and restart the server at the points listed in User Story 3 and check that runs resume correctly.
- **FR-037**: An automated test set MUST try to escape the calculation environment (network, file access, secrets, excessive memory, endless loops, process spawning) and check every attempt is blocked or stopped.
- **FR-038**: The single agent from Phase 3 and the multi-agent flow MUST be scored on the same eval cases, and the results (accuracy, citation coverage, verified-number rate, model calls, latency, partial rate) MUST be written up in a dated document that includes unfavourable results.

### Key Entities

- **Run**: one question and its outcome; now also has a mode (simple or deep), a plan, statuses for waiting for approval, cancelled and expired, the number of verification rounds, and usage per step.
- **Plan**: the ordered steps for a deep run, the estimated model calls, whether it was edited, and the owner's decision and its time.
- **Plan Step**: a goal, the companies and periods it covers, its kind (search, fact lookup, calculation), and its status.
- **Calculation**: inputs (each linked to its source), the code that ran, the output or error, and resource use.
- **Verification Result**: per claim and per number, supported or not, the recomputed value, and the reasons sent back to planning.
- **Report**: summary, tables, optional chart, citations, confidence note, partial flag, list of unverified items and disclaimer, in the question's language.
- **Saved Run State**: the state of a run after each completed step, used to resume after a restart.
- **Memory Item**: a user's watchlist entry or preference.
- **Quota Usage**: simple and deep questions used per user per day.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the restart test, 100% of runs interrupted at each tested point reach a final status after restart, use quota once, and repeat no completed step; their reports score the same on the eval checks as uninterrupted runs.
- **SC-002**: In the deep eval set, 100% of numbers carry a citation, 100% of derived numbers come from a recorded calculation, and at least 80% of expected numbers match within tolerance.
- **SC-003**: The verification step rejects at least 90% of seeded wrong numbers and unsupported claims, and no report released as complete contains a number that differs from its recomputation.
- **SC-004**: 100% of reports carry the disclaimer; 100% of advice-refusal cases are refused and 100% of injection cases leave the plan, tools and memory unchanged.
- **SC-005**: No simple run exceeds 4 model calls or 90 seconds; no deep run exceeds 20 model calls or 5 minutes after approval.
- **SC-006**: Users see a plan within 15 seconds for 95% of deep questions and the final report within 5 minutes of approval for 95% of approved runs.
- **SC-007**: On the Phase 3 answer eval subset, simple questions score no worse than the Phase 3 baseline beyond the configured tolerance, and 95% still answer within 30 seconds.
- **SC-008**: 100% of calculation-escape test cases are blocked or stopped within the time limit, and no calculation sees a secret.
- **SC-009**: 100% of watchlist eval questions cover exactly the saved companies, and no test user can read another user's memory.
- **SC-010**: A dated comparison of the single agent and the multi-agent flow on the same cases is published in the repository, with all metrics for both, how to rerun it, and at least one stated weakness of the multi-agent flow if the numbers show one.

## Assumptions

- Phase 3 is accepted: sign-in, the beta allowlist, quotas, the model router with fallback and cache, the search and facts tools, live progress and cited answers are in place and reused.
- Deep mode is open only to users already on the beta allowlist; the public launch stays in Phase 5.
- PROJECT_SPEC §4 fixes a graph orchestration framework with a durable checkpointer and a container-based sandbox. ADR 0003 kept Phase 3 on a plain loop, so both the new dependencies and the sandbox component need the owner's approval during planning, with an ADR.
- The calculation environment runs on the same VPS within the memory budget in PROJECT_SPEC §16 (at most 2 at a time, 512 MB each).
- The Phase 3 single agent stays runnable only as the eval baseline for the comparison; users get the new flow for both modes.
- Default deep quota is 5 per user per day, following the example in PROJECT_SPEC §10; exact quotas remain an open decision (§22) and are configurable.
- Plans have at most 8 steps and expire after 24 hours without a decision; both are configurable defaults.
- Charts are line or bar charts built from report tables; other chart types are out of scope.
- Preferences are limited to answer language and default period range; other preferences come later if users ask for them.
- The watchlist and settings page is the only new page besides the plan dialog and report view; BYOK, the MCP server, feedback buttons, the history page, landing-page examples and the report cache stay out of scope (Phase 5).
- The eval datasets grow by the deep and memory cases this phase needs; reaching the full sizes in PROJECT_SPEC §14.1 is Phase 5 work.
- Failed runs can be inspected from saved state and traces; one-click step-by-step replay and dashboards are Phase 5.
- The privacy policy is updated to mention that the watchlist and preferences are stored; the owner reviews the wording.
