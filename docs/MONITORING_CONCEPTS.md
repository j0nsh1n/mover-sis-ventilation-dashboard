# Monitoring workspace drafts and retrieval plan

These are design proposals for the desktop research app. The [interactive drafts](prototypes/monitoring-concepts.html) use fictional `SYN` cases and do not connect to MOVER data, Ollama, or the filesystem. No layout or retrieval change has been implemented in the product.

## What the current app does

The desktop creates its full tab layout, schedules processed-data autoload immediately, and schedules the first-run `SetupWizard` 100 ms later. The wizard is already modal, but the main window and its loading state appear first. `needs_first_run_setup()` also skips the wizard for an older install with a saved EMR path.

The Ask tab is the primary entry point. Summary requires a double-click to open a case. The assistant needs both a selected Ollama model and processed cases before a question can run. The sidebar holds model controls, case controls, and help text, while setup and pipeline actions sit in the menu. These choices make the main workflow hard to discover.

The local data snapshot used for this draft has 19,114 rows in `patient_information.csv` and 50 cases in `cases.parquet`. Three warm-process measurements of `load_processed(validate=False)` took 0.022, 0.007, and 0.006 seconds for the 50-case cache. Three rebuilds of `emr_index()` took 0.250, 0.233, and 0.232 seconds. Those timings cover data reads on this host, not window startup, chart rendering, Ollama startup, or a full-corpus pipeline run. The larger UX issue is that “loaded cases” and “searchable surgeries” are different scopes, but the screen does not make that difference clear.

There is no article or publication database in the current repository. The existing `case_fetch.py` already indexes EMR metadata and can run the pipeline for selected PIDs. The current assistant tools mostly search the loaded processed sample. An article library would be a new, optional data source.

## Three layout directions

| Draft | Structure | Best use | Tradeoff |
| --- | --- | --- | --- |
| A. Case workbench | Case list at left, signal and event timeline in the center, source and literature panel at right. | Close examination of one surgery with evidence visible beside the answer. | Dense on small screens. The evidence panel must stack below the timeline. |
| B. Cohort board | Wide searchable table, direct row selection, persistent case detail panel. | Finding patterns across cases, filtering, and comparing groups. | The detailed timeline needs a second view. |
| C. Guided story | A question-led landing page with three explicit steps and a focused case panel. | Teaching and first-time exploration. | Slower for researchers who already know which case they want. |

I recommend **A as the main case workspace** and **B as its Browse entry point**. C is a useful later teaching mode, but it would add another navigation model before the core research loop is reliable. The three drafts are intentionally separate presentations; the switcher lets reviewers compare them without treating one as a reskin of the current tabs.

### Shared setup flow

1. Open a modal before the workspace loads or starts Ollama. Ask for the EMR folder. Offer the processed cache and local model location as optional fields. Keep the wave folder under Advanced settings.
2. Validate the chosen folder and show a precise result: “19,114 surgeries indexed,” “50 cases already analyzed,” or an error naming the missing file. Do not mark setup complete until the required path passes validation.
3. Offer a clearly marked synthetic demo path for first-time exploration. A canceled setup returns to a limited welcome screen instead of starting the app behind the dialog.
4. After setup, open the workspace with a visible data-scope label. Allow Settings to reopen the same source panel. Keep Ollama optional until the user invokes an assistant action.

The prototype's setup modal only demonstrates order, copy, and focus. It does not validate paths.

### Clickability and feedback requirements

- A single click or keyboard Enter selects a case. A visible **Open timeline** button performs the next action. No primary action depends on a double-click.
- Search, case selection, comparison, source rows, and question entry have labeled controls and a visible focus style. Interactive targets are at least 44 px high in the drafts.
- Every data state says what is available: **indexed**, **analyzed**, **loading this case**, **no matching cases**, or **source unavailable**. A failed load gives a retry action in context.
- A rule marker opens the measured values, source table, units, missingness, and threshold version. The assistant answer links to the same evidence view.
- The interface keeps “research and teaching only” visible without making it the main headline of every screen.

## Lightweight retrieval plan

**Recommendation.** Start with deterministic case search and SQLite full-text search for an optional article catalog. Add local text embeddings only if a small relevance test shows that keyword search misses important synonyms. An address or URL alone is a locator, not useful semantic text to embed. Embed a title and abstract when available; store the URL or DOI for citation and opening the source.

```text
Setup validates folders
  ├─ EMR index: PID, procedure, age, sex, ventilator availability
  ├─ Processed cache: analyzed case summaries, timeline, flags
  └─ Optional article catalog: citation, title, abstract, URL/DOI

User search or case question
  ├─ app filters the EMR index and shows candidates immediately
  ├─ selected PID triggers bounded case fetch and rule calculation
  ├─ optional article query returns a few ranked citations
  └─ local model receives only selected extracts and source labels
```

The model may request `search_cases`, `open_case`, or `search_articles`, but the app executes the calls. It enforces limits, reads only configured local sources, and records which source each answer used. The app rejects unknown PIDs and never labels the 50-case processed sample as the 19,114-surgery EMR universe.

The article catalog can be one local SQLite file with fields for stable ID, title, abstract, year, authors, URL or DOI, source file, import date, and rights or license note. FTS5 indexes title and abstract; its built-in `bm25()` provides ranked keyword results. Import is explicit and incremental, with a content hash so unchanged records are skipped. At question time, fetch at most five article records and a bounded case extract. Show the citation and source link in the UI. Do not fetch remote pages during a case question or send local case text to a cloud service. [SQLite documents FTS5 and BM25 ranking](https://www.sqlite.org/fts5.html).

If synonym recall is poor, an optional background import job can call Ollama's local embedding endpoint on title plus abstract. Store the vector beside the catalog row and the embedding model name. Rebuild vectors when that model changes. Query vectors can rerank a bounded candidate set or search a modest local vector set, with a hard result limit and a keyword fallback when Ollama is offline. Do not embed every minute of a timeline or every case row. Ollama [documents local embeddings](https://docs.ollama.com/capabilities/embeddings) and its [embedding endpoint](https://docs.ollama.com/api/embed). No article embedding model is proposed as a required dependency for the first iteration.

### Build sequence and checks

1. **Fix startup state.** Run setup validation before processed-data autoload. Show a welcome or demo state when no valid source exists. Check fresh install, saved valid settings, stale paths, cancel, and offline model cases.
2. **Make case navigation direct.** Implement the chosen layout with single-click selection, visible loading and error states, keyboard reachability, and source-scope labels. Test the same actions on a narrow window.
3. **Unify case search.** Use the EMR index for the searchable universe. Fetch and analyze only selected PIDs, with a small bounded batch and a cache keyed by PID, threshold preset, and source fingerprint. Keep “indexed” and “analyzed” counts separate. Benchmark first usable screen, search, and first case open on the same machine before changing storage further.
4. **Add the article catalog only after a real source set exists.** Import a small, licensed local collection. Check citation completeness, duplicate handling, stale links, and empty results. Compare FTS results with a reviewed set of research questions. Add embeddings only if that comparison finds a specific recall gap.
5. **Ground the assistant.** Make each answer name the active case, analyzed scope, rule version, and cited records. Test that missing case data and missing article matches lead to an explicit “no evidence found” response instead of a confident synthesis.

Proposed performance targets for review, not measured claims: the main workspace becomes usable within 2 seconds for the current local sample; a metadata search responds within 1 second; opening one unanalyzed case shows progress immediately and completes within an agreed budget measured on the target workstation. Report cold and warm timings separately, including Ollama startup if the user asks for a model answer.

## Refinement path for an anesthesia research proof of concept

The first demonstrable workflow is retrospective: choose a de-identified surgery, inspect a time-aligned signal and event, review the rule inputs, then read a local summary with source links. The user can always reach the original extract. Add article citations only when the article library exists and the cited abstract or local text is available.

The next evaluation uses synthetic cases with known injected anomalies, then a manually reviewed set of de-identified real cases. Record search relevance, flag agreement, data coverage, misleading answers, time to open a case, and how often users can find the source of a statement. Ask anesthesia researchers to review the interpretation of induction, maintenance, and emergence before treating flag counts as meaningful. Keep thresholds and dataset version visible, because the current rules are still awaiting calibration against real distributions.

The product boundary stays explicit. This version is a research and education tool for historical data, with no live patient feed, treatment directive, or time-critical alert. A later move toward clinical use would be a separate project with clinical, institutional, privacy, human-factors, and regulatory review. FDA's [2026 clinical decision support guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/clinical-decision-support-software) and [CDS FAQ](https://www.fda.gov/medical-devices/software-medical-device-samd/clinical-decision-support-software-frequently-asked-questions-faqs) explain why intended use, reviewable basis, and time-critical decisions matter. This paragraph describes a product boundary, not a regulatory classification.

## Decisions needed before implementation

- Choose the primary presentation. A is the current recommendation, with B for cohort browsing.
- Identify the article source, rights, and approximate record count. The repo has no article database today.
- Decide whether the synthetic demo is part of the shipped desktop experience or only used in research demonstrations.

The proposals would change the UI and data-loading behavior stated in `spec.md`. That contract needs the user's approval before implementation and must not be silently rewritten.
