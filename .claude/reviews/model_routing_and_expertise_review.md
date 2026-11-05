# Review of Model Routing, Separation of Concerns, and Expertise Criteria

This review was recorded on 2026-11-05. It was revised the same day to incorporate feedback on an actual published post, revalidation results, and comparison against the code. Phases 0–4 were then implemented the same day; see "Implementation Status" below. The rules now live in `agents_design/` 02–07 (decisions D-48 to D-59). Where this review and those documents differ, those documents and the code are correct.

- **Code basis:** Based on commit `73e44fa`. Commits after `9463837` only change this document, so the code is identical.
- **Reviewed:** Read the final version and summary notes of the [published post](https://vansdevblog.online/post/view/nestjs/6aeb9a729493325088e670f7) on the public site.
- **Sample:** Based on the first category page, this is the only post published after the pipeline code was introduced. This single post cannot establish differences among models or personas.
- **Not reviewed:** The reviewed PC had no `.env`, so `agent_drafts` and `agent_calls` (v1–v3 content, review history, and receipts) were not inspected. Parts of the root-cause analysis below that require these records are labeled as hypotheses. No vendor API was called directly for verification.
- **External sources:** Checked on 2026-11-05. Model metrics can change quickly.

## Implementation Status

Code and unit tests are complete for all five phases. **Nothing has run against a real vendor, a real Mongo, or real round records**, because the implementing PC has no `.env` or API keys. Unit tests use a scripted fake LLM and mocked HTTP responses written from vendor documentation, so they verify pipeline logic only. They do not show that vendors accept these requests or that posts read better.

| Phase | In code | Still needs a PC with keys |
|---|---|---|
| 0 | `core_mechanism`/`out_of_scope`, `OUT_OF_SCOPE` with MAJOR arbitration, `POST_REVIEW` and full read, density metrics, prompts v3, `troubleshooter` revision, `agents compare-prompts` | "Verify First" records; density decrease and the user's "reads like an article" judgment |
| 1 | `agent_routes`, round snapshot, candidate selection, preflight, support matrix, effort, truncation handling, xAI Responses API, per-model metrics | `pytest -m e2e tests/e2e/test_llm_vendors.py`; effort, output limits and timeouts tuned from real calls |
| 2 | Target level, prerequisites, level assessment, level-100 structure rule, evidence input and limits, `agents label`, agreement metric | Agreement with 10–20 user labels; the first reference posts |
| 3 | Search + transform calls, code-derived severity, citation check, SPOT_CHECK re-extraction, arbiter search, link check, TS syntax check, one model per lens, `agents fact-check` | Recall and false positives on seeded-error posts; Argon, Claude and Grok search behavior |
| 4 | Regex, line-break and tone patches, safeguards, gate recovery, `origin_cause=POLISH` | Safeguard pass rate and blind user evaluation of paragraphs |

Not done because it needs other repositories: showing the level in `vans_story_be_post` (DTO) and `vans_story` (display). `PUBLISH_POST_LEVEL` stays false until then, since Post BE rejects unknown fields (`forbidNonWhitelisted`, confirmed in `src/main.ts`).

User Decisions were taken at their recommendations, as listed in that section.

### Corrections Made While Implementing

| This review said | What was found or done instead |
|---|---|
| Thinking shares the output limit (all vendors, implied) | True for Argon and Opus 5.5. xAI's Responses `max_output_tokens` counts only visible output, not reasoning |
| Gemini documents structured output with Google Search | The example is the Interactions API. The client uses `generateContent`, for which no such example was found |
| Split into two calls only for vendors that cannot combine search and a schema | Every vendor uses two calls. The single-vendor probes that would decide this cannot run here, and one code path avoids depending on a preview feature. An inline path can be added later if measurements justify the extra call |
| `REVIEW:{lens}` avoids the review vendor already selected for the post | Impossible with three lenses and three vendors once the writer is excluded. The writing vendor is always avoided. Earlier review vendors are avoided only while another vendor remains |
| Phase 3 must separate `persona_id` and change `agents_round_detail.json` | Not needed. `CallModel` removed the persona from calls and receipts, and `slot_bindings` stays as the response contract. That golden is unchanged |
| SPOT_CHECK re-extracts changed sections | Requires the fact lens to be spot-checked even with no open items, or new claims go unchecked. `required_reviewers` now includes it. With no changed sections and no open items it makes no call |
| Change the default of `StructureRules.required_sections` | The default stays. The pipeline passes rules by the post's level, so posts without a level keep the old rule |
| `metrics._reached_ready` needs updating | No change needed. `POST_REVIEW` has not reached ready. `_is_fast_track` did change |
| Clean up `LENS_GUIDES` together with the prompt wording | Removed. Nothing in the application read it |
| Search calls use `review_timeout_s` (to evaluate) | Search calls use `draft_timeout_s`. The budget estimate adds a fixed amount for search results |
| Regex owns paragraph length | Regex cannot shorten a paragraph. Long paragraphs are reported in `PolishRecord.long_paragraphs` and left alone |
| Build the full read on slot selection before Phase 1 | Routing was built first, so the temporary selection was never written. The phases shipped together, so the order inside the change differs from the table below |
| A truncated response must not retry with the same limit | The limit is doubled up to the vendor cap within the attempts. At the cap the draft fails with `OUTPUT_TRUNCATED` |
| Token checks compare counts of protected tokens | Counts pass when `A` and `B` swap or when "3 seconds, 5 times" becomes "5 seconds, 3 times". Order is now compared, along with Latin terms, inline markup, block structure and length change. More negative forms are counted. These checks still cannot guarantee meaning, so the diff stays in the escalation summary |
| Treat search tool failure as a call failure | Also needed when the model answers from memory: claims with no citations at all now fail the call instead of being downgraded to `UNVERIFIABLE`, which would let wrong facts pass as MINOR |
| Re-extract claims from changed sections | An empty changed list must mean "extract nothing" on SPOT_CHECK, and already-checked claims are excluded, or a claim the author won in arbitration comes back as a new CRITICAL |
| `SpotCheckInput` unchanged | It now carries `topic`. The prompt judges `OUT_OF_SCOPE` against the core mechanism, which the input lacked |
| Not mentioned | Old rounds and drafts must keep working. Rounds without a snapshot derive one from `slot_bindings`, and drafts with slot reviewers run on the same code, keyed by primary lens |
| Not mentioned | An empty response body used to retry with an empty assistant message, which vendors reject with a 400. It now retries with the original messages |

## Conclusions Changed in This Revision

The actual post read more like a difficult specification than an article. The following conclusions were therefore revised.

- **Order:** Control narrative and scope before model routing.
- **Level 300 criterion:** Evaluate how deeply the causal chain of a single decision is explained, not the number of covered items.
- **External-evidence ceiling:** Retract the rule "Level 300 or higher requires evidence; without it, cap at 200." Apply it only to Level 400 and `troubleshooter`.
- **`alternate`:** Replace the single-value field with an ordered `candidates` array and conflict-avoidance rules.
- **`UNVERIFIABLE`:** Apply only to claims that can be verified against external documentation. Exclude design judgments from extraction. Do not block publication until the false-positive rate has been measured.
- **Polishing stage:** Do not assign it the task of expanding overly dense paragraphs. It should only correct inanimate subjects and sentence pacing.
- **Remove mandatory `tradeoff`:** Exempt Level 100 when labels are introduced, not later.
- **Remove the `SPOT_CHECK` route:** Because this call verifies the model's own findings, reuse the model that handled FULL.
- **Separate `persona_id`:** Defer from Phase 1 to Phase 3.
- **Pre-publication-stage results:** Merely displaying results on an approval screen gives the human no actionable choice. The only decision in `READY_TO_PUBLISH` is approval. Send blocking defects to `ESCALATED` and use the existing decisions (REVISE, PUBLISH_AS_IS, REJECT).
- **Remove the `in_scope` field:** It duplicates the information represented by the `OUT_OF_SCOPE` resolution.
- **Line-break cleanup safeguard:** Requiring "only line-break characters may differ" prevents paragraph splitting. Allow one adjacent space as well.

## Corrections Made During Revalidation

These corrections were made by comparing the code, the original published post, and vendor documentation again. Statements from the previous revision and feedback that differed from the facts are retained here.

| Previous statement | Actual finding |
|---|---|
| The post was produced under a 12,000-character limit and may have been compressed to fit it | The final Markdown is at least 12,204 characters. The runtime limit exceeded 12,000; its value is unknown |
| No temperature documentation was found for Gemini 4 | `gemini-4-argon` ignores temperature |
| Review-model example: `max_output_tokens: 8000` | This is risky because thinking shares the output limit. The example was corrected |
| The post met most Level 300 criteria (feedback said 100%) | Only the transition sequence and before/after code are clear. The body contains no "rejection," "alternative," "reversal," or "rollback" |
| The model produced a condensed specification while trying to satisfy the criteria table (feedback) | The criteria table was written after publication and is absent from every prompt. The depth-focused design is a preventive measure |
| This post is a logically complete Level 300 post (feedback) | It is not (see Phase 0). The rule that Level 300 can be written without supplied evidence remains, but this post is not supporting evidence |
| The code default for the length limit is 12,000 | This was true at publication time (05:49). Later commit `3b60323` (09:47, Q-08) raised it to 20,000 |
| Because `tree-sitter` is a dependency, TS syntax checks work immediately | The package is in `requirements.txt`, but grammars are downloaded from GitHub on first use. Prefetching them into the image is absent from committed code and exists only in an uncommitted working-tree Dockerfile change. Because the parser is error-tolerant, check `has_error` rather than expecting an exception |
| Pre-publication-stage results are shown on the approval screen | This service has no approval screen. It has an escalation summary (`EscalationSummary`) and a CLI, and the only human decision available in `READY_TO_PUBLISH` is `approve` |

## Implementation Order

| Phase | Scope | Original proposal | Completion criterion |
|---|---|---|---|
| 0 | Inspect actual round records; narrative and scope rules; core-mechanism delivery check; full pre-publication read; `troubleshooter` revision; single vendor calls | New | Density metrics decrease, the body delivers what the title promises, and users judge it to read like an article |
| 1 | Per-call model routing and DB lookup, `candidates`, `effort`, truncation handling | No. 1 | Models change per call without changing API responses or goldens |
| 2 | Level labels, remove mandatory Level 100 `tradeoff`, evidence rules for Level 400 and `troubleshooter`, optional evidence input | No. 3 | Measure agreement with user labels |
| 3 | Fact-checking tools, TypeScript syntax checks, "N lenses per post" structure | No. 2 | Recall and false positives measured with seeded-error posts |
| 4 | Add polishing to the pre-publication stage | No. 2 | Safeguard pass rate and user evaluation |

Phases 0, 2, and 4 all add calls after review passes and before `READY_TO_PUBLISH`. Create the state transition once in Phase 0; later phases only add calls (see "Pre-Publication Stage").

## Phase 0: Narrative and Scope Control

### Findings from the Actual Post

The post is titled "Redrawing NestJS Module Boundaries: Incremental Refactoring from a Layered Structure to Clean Architecture." Its summary notes state:

- Versions v1 → v4. Written by `claude-opus-5-5`; reviewed by `gemini-4-argon` and `grok-4.7`
- CRITICAL 1, MAJOR 2, MINOR 0, NITPICK 0
- Author accepted 3 findings and retained 0

The final version measures as follows. Sentence counts are approximated from Korean sentence endings.

| Metric | Value |
|---|---|
| Rendered body length | 11,475 characters (excluding anchors and Markdown syntax) |
| Lower bound of Markdown length | 12,204 characters (adding the minimum anchors and syntax) |
| Code blocks | 7 (6 TypeScript, 1 JSON), 6,631 characters (58%) |
| Paragraphs | 25, totaling 3,380 characters, average 135 characters |
| Sentences per paragraph | Average 2.6; 6 paragraphs have at least 4 sentences |
| Inline-code items per paragraph | Average 1.7; 5 paragraphs have at least 4 |
| Densest paragraph | 369 characters, 6 sentences, 10 inline-code items |

That single paragraph combines a Unit of Work port, `dataSource.transaction()`, `emitAsync()`, at-least-once delivery and idempotency, `SKIP LOCKED` claiming, broker replacement, and the purpose of `emit()` without an outbox. The paragraph describing reconciliation also handles "cases where even a retry never arrives" in a single sentence. This matches the feedback that the post declares conclusions and moves on instead of explaining why the problems occur.

Reading the content reveals problems more significant than density.

- **It does not fulfill the title.** A post claiming to cover module boundaries contains zero instances of `@Module` and zero instances of `exports:`. It has no code showing the post-change module composition, and the body primarily concerns payment consistency.
- **The difficult parts disappear.** Coupons, email, and refunds in the pre-change `OrderService` do not appear in the post-change code.
- **The prose and code conflict.** The prose says payment completion must not be lost, but `confirm-payment.use-case.ts` simply `return`s when no payment record exists. The post also does not explain who creates that record.
- **There is no fabricated experience narrative.** There are no first-person incidents or measured figures. The Phase 0 change to `troubleshooter` is preventive.

### Root-Cause Analysis

**Confirmed**

1. **The post appears to have been generated from the v1 prompt.** The post's ObjectId timestamp is 05:49 KST, while the file timestamp of `v2/write_draft.md` is 09:32 and the v2 commit (`aa72c89`) is at 09:48. The body also retains the formal imperative ending "~십시오", which v2 prohibits. Therefore, the effect of the v2 prompt has not yet been measured. Confirm using the receipt's `prompt_template_version`.
2. **The runtime length limit is unknown.** The code default was 12,000 at the time, but the final Markdown is at least 12,204 characters and could not have passed that limit. Therefore, the runtime `DRAFT_MAX_CHARS` was higher. `.env.example` does not contain this value. The current code default is 20,000 (Q-08). In "runtime limit and code proportion" in the table below, the limit has already been raised; only the code proportion remains relevant.
3. **Review alone cannot produce v4.** With `max_iterations=2`, review revisions end at v3 (`PostDraft.start_drafting`). Unless the environment value was changed, at least one version resulted from a gate failure (GATE) or human revision (HUMAN).
4. **The review lenses request scope expansion.** The FACT lens in `v2/review_full.md` checks "missing essential background and unaddressed edge cases," while the CRITICAL_PRACTICAL lens checks "practical constraints (cost, operations, team size)." There is no input that limits scope. `TopicForReviewer` contains only title, audience, keywords, and outline.
5. **Rejecting a MAJOR finding disadvantages the author.** The reviewer who raised a finding judges the validity of its rejection during SPOT_CHECK. Only CRITICAL findings receive arbitration (`deadlocked_items`). A MAJOR finding that the reviewer does not accept iterates to the limit and then reaches `ESCALATED` (Q-14). Giving the author a defense only through the prompt leaves this path unchanged.
6. **No rule prevents additive patching.** `revise_draft.md` defines acceptance and rejection formats, but its only revision-method rule is "do not use additional length to broaden the topic." v2 has "one new concept per sentence," but no paragraph-level rule.
7. **No stage checks whether the title is delivered.** Lenses only look for errors. No item asks whether the title or core mechanism was actually conveyed.
8. **No stage reads the entire revised version.** A FULL review occurs only once, when no reviewed version exists (D-21). Subsequent SPOT_CHECK calls examine only previous findings and new CRITICAL issues (D-09). Contradictions and omitted code introduced by revisions are not detected.
9. **The author receives no actual evidence.** `WriteDraftInput` contains only persona, topic, and structure rules. There is no path for evidence from a real project to enter the post.

**Hypotheses (to verify from records)**

- **The post was compressed because of length.** The evidence for this has weakened. Code occupying 58% does leave less room for explanation, but the runtime limit is unknown. It is also unknown whether the gate failure that produced v4 concerned length; it may instead have involved anchors or resolution IDs.
- **Accepting review findings broadened the scope.** It is unknown whether the three blocking findings expanded all four clusters (domain separation; payment idempotency and reconciliation; outbox and `SKIP LOCKED`; cutover, ESLint, and tests), or whether they were already present in v1. Because `overall_feedback` is also passed to the author, inspect it as well.

### Verify First

Perform read-only verification on a PC that has `.env`. The public page contains only the final version.

1. Record the runtime `.env` values for `DRAFT_MAX_CHARS`, `MAX_ITERATIONS`, `PROMPT_VERSION`, `DRAFT_TOKEN_LIMIT`, and `AGENT_SLOT_BINDINGS`.
2. Check the prompt version through `agent_calls.prompt_template_version`.
3. Determine why v4 was created from `origin_cause`, `validation_errors`, and `content_markdown` length in `agent_drafts.versions[]`.
4. Measure density metrics for v1–v4, then compare the three review findings and the original `overall_feedback` to determine when the four clusters were introduced.

| Finding | Interpretation | Fix first |
|---|---|---|
| v1 is already dense and broad in scope | Topic and writing-prompt problem | Topic scope limits and paragraph rules |
| Scope broadened in the version accepting review findings | Review and revision-structure problem | Review scope limits and author defense |
| It was compressed after failing the gate because of length | Length-limit problem | Runtime limit and code proportion |

These cases can overlap. The rules below are needed regardless of the result, but this baseline is required to evaluate their effect.

### Rule Changes

| Location | Change | Enforcement |
|---|---|---|
| `TopicProposal`, `propose_topic` | Add `core_mechanism` (one core mechanism) and `out_of_scope` (what will not be covered). "Module boundaries and domain separation" and "outbox and zero-downtime deployment" belong in separate posts | Schema for the fields; prompt for singularity |
| `TopicForReviewer`, `ArbitrateInput` | Pass both fields | Code |
| `review_full` | Limit CRITICAL and MAJOR to logical and factual defects within `core_mechanism`. Prohibit demands to implement out-of-scope operational edge cases (multi-instance locking, reconciliation batches, deployment cutover, etc.) in the body code. Such findings must be MINOR or lower | Prompt and arbitration |
| `review_full` lens wording | Narrow "unaddressed edge cases" to those required for the core mechanism to work, and "practical constraints" to whether assumptions and limitations are stated | Prompt |
| `review_full` | Failure to convey the core mechanism is MAJOR. Check whether the body and code actually demonstrate what the title and `core_mechanism` promise | Prompt |
| `revise_draft` | When accepting a finding, rewrite the paragraph instead of appending one or two sentences. For out-of-scope findings, do not expand body logic; retain them in `tradeoff` under "Assumptions and scope not covered by this post" | Prompt and metrics |
| `revise_draft` | Require an in-scope determination first for each blocking item. Because the decision appears in the resolution status (`ACCEPTED`, `REJECTED_WITH_REASON`, `OUT_OF_SCOPE`), do not add a separate field. A defense mechanism is useless if the author accepts everything (this post retained 0 findings). Measure the `OUT_OF_SCOPE` ratio | Prompt and metrics |
| `ResolutionStatus`, `ItemResolution` validator, `validation._check_resolutions`, `deadlocked_items`, `arbitrate`, `pipeline_views.build_escalation_summary` | Add the `OUT_OF_SCOPE` resolution. Permit it only for blocking items and require `reason`. The gate rejects `OUT_OF_SCOPE` on non-blocking items. If the reviewer does not accept it, a MAJOR finding also receives one arbitration (change to Q-14). Include the `tradeoff` section containing the scope declaration in arbitration input. `UPHOLD_AUTHOR` closes the item; `UPHOLD_REVIEWER` requires acceptance in the next version as it does now (enforced by the gate and passed through `must_accept`). Also include it in the rejected-findings list of the escalation summary | Code |
| Pre-publication stage | Read the entire revised version once. Do not send results back into the revision loop. If there is a blocking defect, send it to `ESCALATED`; otherwise, save observations in the post document and proceed to `READY_TO_PUBLISH` (see "Pre-Publication Stage") | Code |
| `write_draft`, `revise_draft` | Keep one core concept per paragraph. Do not merely declare keywords; explain the problem context, code or solution, and operating mechanism in prose | Prompt and metrics |
| `services/metrics.py` | Report sentences and inline-code items per paragraph by version, the number of paragraphs with at least four inline-code items, and length growth between versions. Do not block initially | Metrics |
| `personas.py` | Remove "first-person experience narrative" from the `troubleshooter` `tone_markers` | Code |

Implementation must account for the following.

- **Prompt version:** Ship prompt changes in a new `agents/prompts/v3` directory, following `prompts/registry.py` rules. To avoid another version bump in Phase 2, the writing prompt should refer only to `required_sections`, not directly to `tradeoff`. Update "findings that the reviewer marked CRITICAL" in `arbitrate.md` and the resolution rules in `revise_draft.md` for `OUT_OF_SCOPE` and MAJOR arbitration.
- **`troubleshooter` and tone preservation:** Retain "first-person voice" in `TonePreservationRule.must_not_flag`. The prohibited element is not the writing style but a narrative that presupposes a real incident.
- **Lens wording location:** The wording actually used is in `review_full.md`. Nothing in application code reads `LENS_GUIDES` in `personas.py`; only `test_personas.py` does. Clean up both together.
- **Out-of-scope factual errors:** If something stated in the body is wrong, it is CRITICAL regardless of scope. Scope limits apply only to demands to "implement more."
- **`troubleshooter`:** "Incident response account" and "extreme testing" in `writer_bias` also presuppose real incidents or execution. Removal alone is insufficient; replace them with analysis of public cases or apply the Phase 2 evidence rule. This value is also passed to reviewers as `author_tone_markers`.
- **Summary notes and metrics:** `_is_rejected` and `rejected_with_reason_ratio` must count `OUT_OF_SCOPE` as retained.
- **Stored-document compatibility:** `TopicProposal` is used for both LLM output and persistence. Making the fields required would prevent reading existing documents, so provide defaults and check for empty values in `topic_proposal_check`.

Narrowing scope risks demoting real defects to MINOR, where they may remain unfixed because MINOR passes without revision. Therefore, conditions required for the core mechanism to work remain blocking, while demoted findings remain recorded in the notes.

### Pre-Publication Stage

The full read (Phase 0), level assessment (Phase 2), and polishing (Phase 4) all run after review passes and before `READY_TO_PUBLISH`. Within one new state (for example, `POST_REVIEW`), run polishing, full read, and level assessment in that order. This order ensures that the read and assessment see the final body.

- **Stage pointer:** Adding only one state is insufficient. Because `_step` is saved after each call, the system must know which calls completed if a failure or process restart occurs midway. Store `post_review_stage` (`POLISH` → `FINAL_READ` → `ASSESS_LEVEL` → `DONE`) and each stage's result in `PostDraft`. After each successful call or skipped polish, advance and persist the pointer in the same operation. Resuming from `FAILED` reads `failure.from_status=POST_REVIEW` and this pointer to avoid repeating completed calls. In versions where later phases have not yet been introduced, initialize the pointer as though absent stages were skipped.

- **Entry paths:** There are three paths into `READY_TO_PUBLISH`: `evaluate`, `pass_validation` after human revision, and `PUBLISH_AS_IS`. Insert the new state only in the `evaluate` path. The other two are unchanged because a human determined the body (D-17).
- **Destination of results:** The only human decision available in `READY_TO_PUBLISH` is `approve` (`DraftDecisionService`). `resolve_escalation` is exclusive to `ESCALATED`. If results are only displayed, the only options are approval or abandoning the round. Therefore, when the full read reports a blocking defect (a contradiction between body and code or failure to convey the core mechanism), send the draft to `ESCALATED` with a new `EscalationReason` (`FINAL_READ_FLAGGED`). The human uses the existing REVISE(instruction), PUBLISH_AS_IS, or REJECT decisions. A body changed through REVISE is a human revision and does not re-enter this stage. If the approval toggle is on, a human approves that body; if it is off, the body is published without this check. If the full read escalates the draft, do not run the subsequent level assessment. Store non-blocking observations in a new `PostDraft` field and expose them in the escalation summary (`EscalationSummary`, `GET drafts/{id}/escalation/`, and CLI). Put the Phase 3 `UNVERIFIABLE` list and Phase 4 before/after polishing diff in the same location.
- **Full-read contract:** Do not let an unconstrained model verdict decide escalation. Define finding kinds in the output schema. `BODY_CODE_CONTRADICTION` and `CORE_MECHANISM_MISSING` are blocking; code derives `FINAL_READ_FLAGGED` when either exists. Other observations are non-blocking. Every finding must identify an existing section anchor and quote the relevant body or code. The full read checks internal coherence and delivery of the promised mechanism; it does not duplicate Phase 3 external fact-checking.
- **Posts without a level:** Posts published through a human path have no assessment, so `level` is empty. Omit the display.
- **Call failures:** If the polishing call exhausts retries or exceeds the budget (`LLMRetriesExhaustedError`, `BudgetExceededError`), skip polishing. If the full read or assessment exhausts retries or exceeds the budget, stop in `FAILED` as today.
- **Full-read model:** Use a model from a vendor other than the writing vendor. Before Phase 1, select the first slot in `reviewer_assignments` whose bound provider differs from the writing provider. Assignment order makes the selection deterministic. Validate at round creation that such a reviewer exists; `RoundCreate.bindings` can otherwise bind all slots to one vendor.
- **Related updates:** `_EVALUATION_TARGETS`, `EvaluationOutcome._valid_target`, `_step`, `metrics._reached_ready`, `EscalationReason`, and the transition table in document 03. New call types require corresponding updates to `CallKind`, `agent_calls._OUTPUT_SCHEMAS` and `output_schema_of` (replay), `_REVIEW_KINDS` (timeout and temperature branching), `prompts.registry.TEMPLATE_NAMES`, and `builders._TEMPLATE_BY_INPUT`. `AgentCall.slot` is required, but full-read, level-assessment, and polishing calls have no slot, so make it optional (and do the same for `CallPublic.slot`; golden `agents_calls.json` contains only existing calls, so its values do not change). Leave D-09 unchanged because the full read does not trigger revision.
- **When disabling approval:** Blocking results go to `ESCALATED`, so they reach a human regardless of the approval toggle. If the approval toggle is off, no one sees observations, `UNVERIFIABLE` items, or polishing diffs. Decide how these will be used before disabling `PUBLISH_REQUIRES_APPROVAL`.

### Effect Comparison

Starting a new round with the same topic triggers the `agent_topics` duplicate check. First compare drafts with a script that sends the saved `WRITE_DRAFT` input JSON through the v1, v2, and v3 templates. Because `replay` resends stored messages unchanged, changing templates requires reading the user-message JSON back into `WriteDraftInput` and rebuilding it with `render_messages(…, version)`. Run each condition at least three times and use the same `structure_rules.max_chars` value (currently the 20,000 default). The script must fill the new v3 fields because they are absent from the stored input. Evaluate the revision stage using a new-topic round. Because this post is the only sample, also test other topics and writing vendors. Compare using the measurement table above, whether the core mechanism is conveyed, and direct user evaluation.

### Single Vendor Calls

Use small calls with API keys to verify the following. The results determine the Phase 1 support matrix and Phase 3 call structure.

- Combined search and JSON schema: Gemini 4 Argon, Claude, Grok 4.7 (Responses API)
- Whether effort values and temperature can coexist: Claude, Grok 4.7
- Termination reason and body under a small output limit: all three vendors

## Phase 1: Per-Call Model Selection and DB Lookup

### Current Code

The per-call foundation already exists.

- `AgentCallService.call` receives a `binding` for each call.
- `AgentCall.model_binding` snapshots the model for every attempt.
- `LLMParams.model_id` is also passed per call.
- `AgentCallService.replay(call, binding=)` can send the same message to a different model.

The following locations read `slot_bindings`.

| Location | Purpose | Phase 1 handling |
|---|---|---|
| 5 locations in `draft_pipeline.py`, 1 in `topic_selection.py` | LLM calls | Replace with route lookup |
| Publication account in `publishing.py` (D-45) | Writing vendor | Keep unchanged. Populate `slot_bindings` from writing routes |
| `_model` in `publishing.py` | Model name in notes | Replace so reviewer models are read from routes |
| `_display`, `builders.persona_of`, lens calculation in `round.py` | `persona_id` | Keep unchanged |
| `pipeline_views.py`, `schemas/agent_pipeline.py` | API responses | Keep unchanged (see boundary below) |

### Storage Format

Add one collection to the same DB (D-43). Names and values are examples.

```json
{
  "_id": "REVIEW:FACT_STRUCTURE",
  "candidates": [
    {"provider": "google", "model_id": "gemini-4-argon", "effort": "high", "temperature": null, "max_output_tokens": 16000},
    {"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high", "temperature": null, "max_output_tokens": 16000},
    {"provider": "xai", "model_id": "grok-4.7", "effort": "high", "temperature": null, "max_output_tokens": 16000}
  ],
  "note": "For a post written by a Google model, advance to the next candidate"
}
```

Keys are `PROPOSE_TOPIC:{persona}`, `WRITE_DRAFT:{persona}`, `REVISE_DRAFT:{persona}`, `REVIEW:{lens}`, `ARBITRATE`, and `FINAL_READ`. Later phases add `ASSESS_LEVEL`, `POLISH_FORMAT`, and `POLISH_TONE`. In Phase 1, look up `REVIEW:{lens}` by the reviewer's primary lens. Secondary lenses are handled within the same call.

- **Lookup time:** Read once when creating the round and snapshot the entire candidate array. Reading on every call could change models midway through an in-progress post.
- **Slots and personas:** Removing `AGENT_SLOT_BINDINGS` also removes the source of the slot-to-persona mapping. Store one document in the same collection (for example, `_id: "SLOTS"`, `{A: architect, B: systems_engineer, C: troubleshooter}`) and snapshot it when creating the round. Decide during implementation whether to preserve `RoundCreate.bindings` (A/B overrides), as listed in "User Decisions."
- **Missing route:** Fail round creation. Silently falling back to `AGENT_SLOT_BINDINGS` would hide missing configuration. Remove this setting after seeding initial data.

### Candidate Selection and Conflict Avoidance

Scan in array order and select the first candidate whose vendor is not excluded. Implement this as a pure function that reads only the snapshot and post state, ensuring the same result after restart.

| Route | Vendor to avoid |
|---|---|
| `PROPOSE_TOPIC`, `WRITE_DRAFT`, `POLISH_FORMAT` | None |
| `REVISE_DRAFT` | Select the first candidate from the same vendor as the writer; the model may differ. Human revision (HUMAN) follows the same rule. If none exists, fail round creation during preflight |
| `REVIEW:{lens}` | Writing vendor and the review vendor already selected first for the same post |
| `SPOT_CHECK` | No route. Reuse the model that handled FULL |
| `ARBITRATE` | Writing vendor and the vendor of the reviewer who raised the item |
| `FINAL_READ`, `ASSESS_LEVEL`, `POLISH_TONE` | Writing vendor |

- **Conflict unit:** Vendor. Different models from the same company do not count as independent verification.
- **Three-vendor structure:** For two reviewers and an arbitrator to always be available, `REVIEW` and `ARBITRATE` candidates must include all three vendors.
- **Preflight:** At round creation, calculate reviewer and arbitrator selection for the writing candidate actually selected for each persona; fail creation if candidates are exhausted. Because `candidates` is not failover and `WRITE_DRAFT` has no vendor exclusion, later writing candidates are not possible combinations and must not make preflight stricter. If `RoundCreate.bindings` overrides are preserved, validate the actual override in that request against the same rules. Also fail when the selected writing vendor is absent from that persona's `REVISE_DRAFT` candidates. This prevents discovering configuration failures midway through a post. Exclude vendors without API keys (vendors absent from `LLMClientRegistry.providers`) from the calculation; otherwise, calls fail with `KeyError`.
- **Not failover:** `candidates` exists only for conflict avoidance. A call failure still exhausts retries and then enters `FAILED`.

### effort and temperature

| Vendor | temperature | effort |
|---|---|---|
| Anthropic | Prohibited. API `2026-10-21` returns 400, so the current code does not send it | `output_config.effort`. Opus 5.5 defaults to `medium`, and thinking cannot be disabled |
| Google | `gemini-4-argon` ignores it. Documentation advises omitting it. Gemini 3 documentation strongly recommends retaining the 1.0 default | `thinkingLevel`. Argon supports LOW, MEDIUM, and HIGH, defaulting to MEDIUM. `MINIMAL` and `thinking_budget` return 400 |
| xAI | Supported. Recommended value is 0.7; values above 1 are clamped to 1 | low through xhigh, default high. Cannot be disabled |

Temperature in the current code does not work as intended. Writing is configured as 0.7 and review as 0.2, but the value is not sent to Claude and Argon ignores it. It is only effective for Grok, where the review value 0.2 differs from xAI's recommendation. Receipts retain values that were not applied. Because effort is not sent, Opus 5.5 and Argon run at medium while Grok runs at high. The quality impact has not been measured.

Rules:

1. In the call-specific type, both `temperature` and `effort` are nullable and default to null. Do not send them when null. Remove `Settings.review_temperature`. Because `ModelBinding.temperature` is emitted as `0.7` in `RoundPublic.slot_bindings` and golden `agents_round_detail.json`, do not change its type or read it in Phase 1. Calls use only values from the route candidate.
2. Maintain a vendor/model support matrix in code. Fail round creation for unsupported combinations (Anthropic + temperature, Argon + temperature, Argon + `xhigh`, xAI + `max`, etc.). If clients silently discard values, receipts diverge from actual requests.
3. Prioritize effort. If a model rejects the coexistence of both values in a single-call test, mark temperature as prohibited for that model in the support matrix. If coexistence is supported, send both configured values.

### Output Limits and Truncation

Thinking shares the output limit. With a small limit, the response may be truncated before producing a body.

- **Argon:** Output includes thinking, and if thinking consumes the limit, the body may be empty. The per-response output limit for direct non-streaming HTTP requests is 32,768. The current client does not handle continuation (`CONTINUATION`, `continuation_token`), so it must not exceed this value. A forum user also reported an empty body at 8,192.
- **Opus 5.5:** Thinking cannot be disabled. A `stop_reason` of `max_tokens` means output is incomplete.
- **Current clients:** `AnthropicClient` and `GoogleClient` do not inspect termination reasons. A truncated response is treated as a contract-violation retry, repeated with the same limit, and eventually enters `FAILED`.

Rules:

1. All three clients include the termination reason in `LLMResponse`.
2. Record truncation separately from contract violations and do not retry with the same limit.
3. When increasing effort, measure output limits, `review_timeout_s`, and `draft_timeout_s` together. Determine values through single calls and actual rounds.

### Boundary Between Phases 1 and 3

**Phase 1 retains the two-reviewer and slot structure and changes only model lookup. Change to "N lenses per post, one model per lens" in one operation in Phase 3.** The N-lens structure depends on the fact-checking contract.

- **Responses and goldens:** In Phase 1, preserve the shape of `RoundPublic.slot_bindings` and `reviewer_assignments`. Keep the snapshot only in `PublishingRound`; do not expose it in responses. Therefore, `pipeline_views.py` and `agents_round_detail.json` do not change. `agents_calls.json` emits only `provider` and `model_id`, so it is unaffected.
- **Meaning of `slot_bindings`:** The slot's persona and writing model. Populate it from writing routes when creating the round. Account selection and `_display` in `publishing.py` continue to work unchanged.
- **`effort` field:** Adding it directly to `ModelBinding` would change responses. Define a separate type for calls and receipts, including nullable `temperature`. If `AgentCall.model_binding` changes to that type, it must still read existing receipts containing `persona_id` (`replay` uses them).
- **Change together in Phase 3:** Separation of `persona_id`, `reviewer_assignments`, the two-reviewer assumption in `publishing.py`, `_LENS_LABELS`, `pipeline_views.py`, and `agents_round_detail.json`.

### Model Attribution in Summary Notes

**List only models that affected the body.**

| Role | Attribution |
|---|---|
| Writing, review | Always |
| Revision | Only when different from the writing model |
| Arbitration | Only when arbitration occurred |
| Polishing | Only when a patch was actually applied |
| Full read, level assessment | Do not list. They do not change the body and are recorded in `agent_calls` |

Record the candidate actually selected. In Phase 1, the affected test is the note-wording assertion in `test_publishing.py`.

## Phase 2: Expertise Criteria for Posts

### Current Code

- The only level-related field is free-text `TopicProposal.audience`. The Post BE `Post` schema has none.
- Topic-proposal prompts and review lenses have no depth criteria.
- Gate rules are identical for every post (length range, at least one code block, and `REQUIRED_SECTIONS`). Requiring `tradeoff` even for introductory posts makes their surface structure similar.

### Criteria Table

The framework is adapted from AWS session levels 100–400; the detailed criteria are a draft created for this review.

| | 100 Introductory | 200 Applied Fundamentals | 300 Advanced | 400 Expert |
|---|---|---|---|---|
| Core question | How do I use it? | How do I use it correctly? | Why does this decision follow, and how does the code change? | Why does it behave this way, and where does it fail? |
| Prerequisite knowledge | None | Basic usage experience | Direct experience with multiple approaches | Internal behavior and operational experience |
| Assessment criterion | Following the procedure produces a working result | Explains both method and rationale | Deeply explains the causal chain of one selected transition or design decision through before/after code | Explains internal behavior or failure causes with measurements |
| Evidence | Official documentation | Executable code | Before/after code and tests. A standard scenario is acceptable | Measurements and operational records. External evidence required |

**Do not score the number of items.** The previous revision listed rejected alternatives, transition sequence, and reversal cost as Level 300 criteria. The published post was not written against this table; the table was created after the post and appears in no prompt. However, putting a checklist into prompts or assessment could produce posts that fill one line per item to pass, so prevent that behavior explicitly. These are now only examples that may appear in a causal explanation. The assessor must quote paragraphs demonstrating depth; a post that skims multiple topics in one line each is not Level 300.

Applied to two examples:

- "Creating a Nest Module" is Level 100. It can be replaced by procedures from official documentation.
- "Incremental Refactoring from a Layered Structure to Clean Architecture" can target Level 300. However, the actual published version is difficult to rate as 300 under these criteria: it lacks module-composition code, difficult parts of the pre-change code disappear after the change, and some paragraphs merely declare conclusions. Set the final level as the first example labeled by the user.

### Application

1. **Topic:** Add target level and prerequisite knowledge to `TopicProposal`. Scope fields are already added in Phase 0. Pass both values to reviewers through `TopicForReviewer`. Reviewers need them to determine whether omitted prerequisites are appropriate for the audience. Only the assessor does not receive them.
2. **Assessment:** In the pre-publication stage, use a model from a vendor other than the writing vendor. Do not include the target level in its input; require it to quote the body for each criterion. Because a model may overrate its own post, do not use the author's declared value as the label.
3. **Structure rules:** Remove mandatory `tradeoff` for Level 100. Introduce this with the label. Forced tradeoffs would distort assessment and agreement validation.
4. **Display:** Publish the assessed value as `level`.
5. **Validation:** Measure agreement between model assessments and 10–20 posts labeled directly by the user. Version the criteria table and reference posts in the DB and snapshot them into each round. The first category page has 31 public posts; most titles inspected appear introductory, and only one post was generated by the pipeline. Therefore, initially measure only the separation between Levels 100/200 and 300+, then measure 300 versus 400 after accumulating reference posts.
6. **Blocking (later):** After measuring agreement, consider blocking posts assessed below their target.

Update the following structure-rule locations.

- `_check_anchors` in `validation.py` reads global `REQUIRED_SECTIONS`. Change it to select required sections by the post's target level. Existing posts without a level retain current rules.
- Change the default for `StructureRules.required_sections` and the expected value in `test_builders.py` together.
- For Level 100 posts without `tradeoff`, put out-of-scope declarations in `conclusion`.

### External-Evidence Rules

Evidence means inputs such as git history, `code-graph context` output, and measurement records.

- **Level 300 and below:** Can be written without supplied evidence. A standard domain scenario, before/after code, and unit tests are sufficient if they form a closed logical argument. The published post is not an example that satisfies this condition, so verify feasibility with a new post.
- **Level 400:** Evidence is required. Without it, Level 400 cannot be selected as the target.
- **`troubleshooter`:** Posts that presuppose a real incident require evidence. Without it, only analysis of public cases is allowed, with a maximum level of 200. This value requires user confirmation.
- **Optional input:** Add an optional field to `WriteDraftInput` so evidence can be supplied regardless of level. Do not broaden the required cases. Currently, no path allows evidence from a real project to enter the post, so this input is expected to have the greatest effect.

Fabricated experience narratives are difficult to detect through web-search fact-checking. Prevent them at the input stage.

### Display Considerations

- Post BE uses `forbidNonWhitelisted: true`, so it rejects fields absent from the DTO. Deployment order is vans_story_be_post, vans_story, vans_devblog_fastapi. Because existing posts remain, make the field optional.
- `PostList` already uses `PostCard.renderBadge` for the category icon. Add a separate level display.
- Decide list filtering and search-index integration later.

## Phase 3: Fact-Checking, Code Validation, and Lens Structure

### Problems in the Current Structure

- **No sources:** `LLMClient.complete` has no tool parameters, so reviewers judge from memory alone.
- **Unverifiable items are ignored:** `checklist.unverifiable` is only stored. The validator in `post_draft.py` checks only whether it is empty; evaluation and publication do not read it.
- **Links are not checked:** `_warn_external_links` only checks the http(s) scheme.
- **Lenses are not dedicated:** `build_review_assignments` attaches the author's lens as another reviewer's secondary lens. The FACT lens of a post written by the FACT persona receives no dedicated review.

### Limit the Extraction Scope

Split the post into claims and assess each claim (the SAFE approach). Limit extraction to **claims whose truth can be determined from external documentation**.

| Target | Example (actual post) |
|---|---|
| Framework/library API | Behavior of `suppressErrors` in `emitAsync()` |
| Configuration option name | `import/no-restricted-paths` |
| Version | A statement that behavior changed in a particular version |
| DB syntax | `SELECT ... FOR UPDATE SKIP LOCKED` |
| Protocols and figures | Delivery guarantees and benchmark figures |

**Do not extract:** Design judgments ("a call from payment to order is a notification"), causal explanations of example code in the body, opinions, and recommendations. No external URL may exist for these. If all become MAJOR, the post's core logic will be erased or trapped in a revision loop. The LOGIC lens handles this content.

In the schema, constrain `claim_type` to an enum of the categories above. Output is `{section_id, claim_type, claim, status, source_url, note}`.

Code determines severity, using the same approach as D-03.

| Assessment | Handling |
|---|---|
| `REFUTED` | CRITICAL |
| `UNVERIFIABLE` | Initially record as MINOR and show in the escalation summary. Raise to MAJOR after measuring the false-positive rate |
| `SUPPORTED` + no URL | Treat as `UNVERIFIABLE` |
| `SUPPORTED` + URL present | No finding |

If MAJOR blocks from the outset, true claims that search fails to find will be deleted or sent to `ESCALATED` under `max_iterations=2`. The author has no search tool, making evidence difficult to provide.

Treat failure of the search tool itself as a call failure. Counting it as `UNVERIFIABLE` would turn every outage into a finding.

When the version changes, handle the following.

- **New claims:** SPOT_CHECK examines only new CRITICAL findings. If fact-checking runs only during FULL, claims added after v2 are not verified. Re-extract and assess sections changed by revision.
- **Arbitration:** Give search access to arbitrators deciding disputes over `REFUTED` findings. The current arbitrator judges only from memory.

### Web Search and JSON Schema

| Vendor | Documented behavior | Verify or change |
|---|---|---|
| Gemini | Structured output can be combined with Google Search (Preview) | Documentation says "Gemini 3 series only," but the tool example uses `gemini-4-argon`. The Argon developer guide does not cover this combination |
| Grok 4.7 | The Responses API includes a `web_search` + `json_schema` example | `/chat/completions` in `clients/llm/xai.py` is marked legacy in xAI documentation. Migrate to the Responses API |
| Claude | Supports `web_search` ($10 per 1,000 calls). Citations are always enabled, and combining citations with `output_config.format` returns 400 | Combining these features appears likely to return 400. This is an inference because no documentation directly addressing the combination was found |

For vendors that cannot combine them in a single call, split the operation into two calls.

1. **Verification:** Verify facts with web search. Receive ordinary text and citations.
2. **Transformation:** Have a lightweight model (such as Haiku 5.5) convert the result to the JSON schema.

Prevent the transformation model from fabricating URLs through code validation. A JSON `source_url` must appear in the first call's citations; otherwise, treat it as `UNVERIFIABLE`. Add citations to `LLMResponse` and retain two receipts. Also evaluate the "structured data with citations" approach in Anthropic documentation as a candidate.

Check link responses before review and provide the results as fact-checking input. Network results are unstable, so do not include them in a deterministic gate.

When adding search, clients must handle the following.

- **Continuation:** Long Claude searches may return interrupted with `pause_turn`, requiring a continuation loop.
- **Response parsing:** Claude returns pre-search sentences and cited sentences in separate text blocks. `AnthropicClient` concatenates them, so the result cannot be parsed directly as JSON. This is another reason for the second call.
- **Tokens and time:** Search results count as input tokens. They are absent from the `_check_budget` estimate, so they consume the post limit (`draft_token_limit`) faster than expected. Evaluate `review_timeout_s=300` as well.

### Code Validation

Six of the published post's seven code blocks are TypeScript, but the gate checks only python and json (Q-06). Q-06 raised concerns about subprocesses and image size, but `tree-sitter`, already used by the code graph, is a dependency and supports syntax checking without a subprocess. However, grammars are not included in the wheel and are downloaded from GitHub on first use. The code graph's grammar list includes typescript and tsx (`_LANGUAGE_BY_SUFFIX`). Prefetching grammars into the image is absent from the committed Dockerfile and exists only in an uncommitted working-tree change, so runtime downloads remain necessary until that change is incorporated. Because tree-sitter is an error-tolerant parser, inspect the root node's `has_error` rather than expecting an exception.

A fragment containing only a method is flagged as a syntax error. Add `typescript-snippet`, analogous to `python-snippet`, and exclude it from checking (`tiptap.py` strips the `-snippet` suffix regardless of language). Add the tagging rule to the writing prompt. Syntax checks do not detect type or logic errors. The full read is responsible for inconsistencies between code and prose.

### Lens Structure

Change to "N lenses per post, one model per lens." Arbitrator rules have already moved into the Phase 1 routing rules, so change assignments, summary notes, responses, and goldens together here.

## Phase 4: Style, Tone, and Paragraph Cleanup

### Role

This does not work as a review lens.

- Tone-preservation rules prohibit findings about tone and sentence rhythm (`must_not_flag` in `contracts.py`).
- Style findings are MINOR, and a post with only MINOR findings is published without revision (`evaluate` in `evaluation.py`).
- D-47 already rejected the same proposal.

Therefore, polishing runs within the pre-publication stage created in Phase 0. **Polishing cannot expand a compressed narrative.** The safeguards below prevent semantic distortion, but they also prevent adding new explanations. Phase 0 rules address information density; polishing only refines presentation.

Research found that LLMs changed meaning even when instructed to correct only grammar (the subjects were essays, not technical posts containing code). In a small comparison of academic-paper editing, AI edits improved 61% and worsened 14%, while human edits improved 90% (two papers, eight paragraphs, and two tables). This is why the model must not rewrite the entire post.

| Task | Owner | Safeguard |
|---|---|---|
| "~하십시오", "~라고 볼 수 있다", "~인 상황이다", paragraph length | Regular expressions | Do not use an LLM |
| Line-break and paragraph cleanup | Cheapest model | Apply only if the text differs solely by inserted/deleted line breaks and one space adjacent to them, and code blocks are byte-identical |
| Correct inanimate subjects and split sentence pacing | Model from a vendor other than the writing vendor | Accept only per-paragraph patches. Apply only when code blocks, inline code, numbers, URLs, anchors, and the count of negative expressions are identical before and after |

Allowing only line-break characters prevents paragraph splitting itself: dividing "Sentence 1. Sentence 2" into two paragraphs replaces one space with a line break. However, all whitespace cannot be ignored during comparison. Spacing changes can alter the meaning of a Korean sentence and can also break inline code and code indentation. Therefore, allow only one space adjacent to a line break.

Token checks cannot guarantee meaning. Changing "does not block" to "blocks" leaves code and numbers unchanged. Therefore, also compare the count of Korean negative expressions (않, 못, 없) and show the before/after polishing diff in the escalation summary. Human approval handles any remaining semantic changes. Disabling approval removes this safeguard, so reassess before doing so.

If one model edits posts from all three personas, their styles may converge. Reduce this risk by passing `tone_markers` and allowing edits only to paragraphs that violate a rule. Verify from actual results whether persona differences are preserved.

### Gate-Recovery Rules

The gate's Markdown checks (`_check_anchors`, `_check_code_blocks`, `_check_length`, `_check_convertible`) are pure functions and can run after each patch.

1. Apply patches individually in paragraph order. If a patch violates a safeguard or gate check, discard only that patch and continue.
2. Run the full `ValidationGate.validate` on the combined result of the remaining patches. If it fails, discard the entire polished version and use the review-approved version as the publication version.
3. If no patches were applied, do not create a new version.
4. Never return to `REVISING` or call the LLM again. A polishing failure must not stop the post.
5. Record the count and reasons for discarded patches as polishing-quality metrics.

The polished result is a new version, but it is not re-reviewed. Human approval (D-17) applies to the polished version. There is currently no paragraph-length gate; if one is added, follow the same rules. The polished version has a new `origin_cause` value (`POLISH`) and empty `resolutions`. The gate's `_check_anchor_persistence` runs on every non-HUMAN version, but passes at this point because there are no unresolved blocking items.

Existing transitions cannot be reused. `submit_version` is called only from `DRAFTING`. `pass_validation` sends a reviewed post to SPOT_CHECK and raises an exception when no unresolved items exist, while `fail_validation` transitions to `REVISING`. Add a dedicated method that attaches the polished version in a passed state after checks complete. Update `_is_fast_track` ("v1 is the publication version unchanged") and the note's "v1 → vN" so polishing versions are not counted.

## Model Research Results

The available sources support only candidate selection.

| Concern | Primary candidate | Basis | Confidence |
|---|---|---|---|
| Fact-checking | Gemini 4 Argon + search | AA-Omniscience v1.1 hallucination rate 4% (High) | Medium |
| Fact-checking Gemini-written posts | Opus 5.5 + search | Same benchmark: 66% accuracy (Xhigh), net score 48 (Max) | Medium |
| Tone | Claude | No quantitative Korean benchmark was found. Only a personal research note and an English blind test (Claude 4.8, ChatGPT 4.6, Gemini 4.1) are available | Low |
| Line breaks | Haiku 5.5 | $0.10 input and $0.50 output per million tokens. Application requires passing code checks, so failure preserves the original | High |
| Grok 4.7 | Deferred | The AA page has no figures. Writing evaluations vary widely by source | Undetermined |

Interpret these results with the following caveats.

- AA defines hallucination rate as `incorrect / (incorrect + partially correct + unanswered)`. It is not the overall error rate; it more closely measures the tendency to bluff when uncertain.
- AA is a general-knowledge test and differs from detecting errors in Korean technical writing. Its domains include software engineering, but no domain-specific figures were inspected.
- Metrics vary by effort. For Gemini 4 Carbon, High has an 11% hallucination rate and net score 54, while Medium has 4% and 47. Carbon was excluded because it could not be found on Google's model-list page.
- Figures from secondary aggregation sites were not confirmed on the original page: Argon hallucination rate 15.1%; Grok 4.7 accuracy 47.4% and hallucination rate 29.3%; Opus 5.5 accuracy 66.2% and hallucination rate 58.6%. The Opus 5.5 figures were seen only in a search summary.

Measure performance on this workload instead of relying on rankings. Use `replay(binding=)` and add only a small script that substitutes the post.

- **Fact-checking:** Seed each of five real posts with ten incorrect APIs, versions, or nonexistent options. For each model/effort/search combination, measure detections (recall) and fabricated errors (false positives). Do not use author acceptance rate as a metric because it does not establish correctness. In the actual post, the author accepted all three findings, but that does not mean the findings were correct.
- **Tone:** Have each model edit the same 20 paragraphs and ask the user to evaluate them with model names hidden. Also measure safeguard pass rate.
- **Cost (estimate):** With Opus 5.5, approximately $0.1–0.3 per review and $3–9 for 30 reviews. This assumes 12k input tokens and 3–10k output tokens; search cost is separate.
- **Operational metrics:** `compute_metrics` aggregates only by call kind and slot. After routing, aggregation by model and lens is required.

## Items to Verify with Actual Calls and Records

- [ ] Runtime `.env` length limit, iteration limit, prompt version, token limit, and slot bindings
- [ ] Published post's prompt version; per-version `origin_cause`, `validation_errors`, and length; review items and `overall_feedback`
- [ ] How the other two posts in the same round ended
- [ ] Gemini 4 Argon: combine Google Search + `responseJsonSchema`
- [ ] Claude: combine `web_search` + `output_config.format`; text-block structure of the response; `pause_turn`
- [ ] Grok 4.7: combine `web_search` + `json_schema` after migrating to the Responses API
- [ ] Whether Claude and Grok effort values can coexist with temperature
- [ ] Termination reasons and body content for all three vendors under a small output limit

## User Decisions

All recommendations below were applied in the implementation. Each can still be changed. The two rows left to implementation resolved as follows: `body.bindings` is preserved and overrides only the slot's persona and writing model; lens composition stays at the current three lenses until Phase 3 measurements exist.

| Item | Recommendation |
|---|---|
| Q-14 change: one arbitration for a MAJOR disputed as `OUT_OF_SCOPE` | Change. A prompt-only change causes rejection to lead to `ESCALATED` |
| Introduce full pre-publication read | Introduce. Do not send it into the revision loop |
| Handling blocking results from the full read | `ESCALATED(FINAL_READ_FLAGGED)`. `READY_TO_PUBLISH` has no reversal decision, so use the existing escalation menu. The alternative is adding REVISE and REJECT decisions to `READY_TO_PUBLISH` |
| Phase 1 `ModelBinding.temperature` | Keep the type and response unchanged and do not read it. Preserve goldens. The alternative is making it nullable and updating goldens |
| Initial handling of `UNVERIFIABLE` | Non-blocking. Raise to MAJOR after measuring the false-positive rate |
| Optional evidence input | Include in Phase 2. Required cases remain only Level 400 and `troubleshooter` |
| Block lower-level posts or only label them | Label first. Consider blocking after validating agreement |
| Maximum `troubleshooter` level without evidence | 200. Do not write posts that presuppose real incidents |
| Level of the actual published post | Set through a user label and use as the first reference-post example |
| Preserve A/B overrides (`body.bindings`) | Decide during implementation after inspecting usage |
| Preserve per-vendor publication accounts (D-45) | Preserve, based on writing vendor |
| Preserve or restructure lens composition | Decide from Phase 3 measurement results |

## Sources

- [AA-Omniscience v1.1](https://artificialanalysis.ai/evaluations/omniscience)
- [Gemini structured outputs](https://aistudio.google.com/docs/structured-output), [Gemini models](https://aistudio.google.com/docs/models), [Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3), [Gemini 4 Argon developer guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-4-argon), [Argon truncation forum report](https://discuss.ai.google.dev/t/gemini-4-argon-thought-only-max-tokens-with-preserved-signatures-including-thinkinglevel-low/198148)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), [xAI web search](https://docs.x.ai/developers/tools/web-search), [xAI reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), [xAI best practices](https://docs.x.ai/developers/model-capabilities/text/best-practices), [xAI Responses](https://docs.x.ai/developers/rest-api-reference/inference/responses), [xAI Chat Completions(legacy)](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions), [Grok 4.7 guide](https://docs.x.ai/developers/grok-4-7)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Claude citations](https://platform.claude.com/docs/en/build-with-claude/citations), [Claude web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [How LLMs Distort Our Written Language](https://arxiv.org/abs/2603.18161), [AI editing comparison article](https://www.news-medical.net/news/20260210/AI-editing-tools-make-more-corrections-but-reduce-writing-quality.aspx), [SAFE](https://arxiv.org/abs/2403.18802)
- [LLM writing notes by language](https://peterkaminski.ai/research/llm-writing-quality-by-language/), [LLM Guides blind test](https://llmguides.ai/lab/llm-writing-quality-test/), [BenchLM writing rankings](https://benchlm.ai/best/writing), [Secondary aggregation: BenchLM hallucination rate](https://benchlm.ai/benchmarks/omnisciencehallucinationrate)
- [AWS session level guide](https://aws.amazon.com/events/summits/asean/online/agenda)
