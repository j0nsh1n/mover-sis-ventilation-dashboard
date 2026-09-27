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
| A. AI workbench | A prominent question box, case shortlist, timeline, and source evidence on one screen. | Repeated questions about cases while keeping measurements and citations visible. | Dense on small screens. The evidence panel must stack below the timeline. |
| B. Cohort board | Wide searchable table, direct row selection, persistent case detail panel. | Manual filtering, comparison, and audit of the AI shortlist. | Adds a second, more complex navigation view. |
| C. Guided AI start | A large question box, then three stages: retrieve, inspect, and verify. | First-time use and teaching through a focused question. | Slower for researchers who already know which case they want. |

I recommend **C as the welcome screen** and **A as the main workspace after the first question**. The same question and evidence stay visible during that transition. B can remain an optional **Browse all cases** action for people who want direct cohort control. The AI is the first action in A and C. The three drafts remain separate presentations so reviewers can compare them before the product adopts one navigation model.

### Shared setup flow

1. Open a modal before the workspace loads or starts Ollama. Ask for the EMR folder and show whether a local chat model and embedding model are ready. Offer the processed cache as an optional field. Keep the wave folder under Advanced settings.
2. Validate the chosen folder and show a precise result: “19,114 surgeries indexed,” “50 cases already analyzed,” or an error naming the missing file. Do not mark setup complete until the required path passes validation.
3. Offer a clearly marked synthetic demo path for first-time exploration. A canceled setup returns to a limited welcome screen instead of starting the app behind the dialog.
4. After setup, place the AI question box in focus and show the data-scope label. Allow Settings to reopen the same source panel. If Ollama is unavailable, say so beside the question box and keep manual case browsing usable.

The prototype's setup modal only demonstrates order, copy, and focus. It does not validate paths.

### Clickability and feedback requirements

- A single click or keyboard Enter selects a case. A visible **Open timeline** button performs the next action. No primary action depends on a double-click.
- The question box is the dominant control. Suggested questions demonstrate finding similar cases, explaining a rule episode, and comparing documented patterns. The app reports retrieval progress before generation starts.
- Search, case selection, comparison, source rows, and question entry have labeled controls and a visible focus style. Interactive targets are at least 44 px high in the drafts.
- Every data state says what is available: **indexed**, **analyzed**, **loading this case**, **no matching cases**, or **source unavailable**. A failed load gives a retry action in context.
- A rule marker opens the measured values, source table, units, missingness, and threshold version. The assistant answer links to the same evidence view.
- The interface keeps “research and teaching only” visible without making it the main headline of every screen.

## AI-first retrieval plan

An embedding model turns text into a numeric vector. A question and a relevant record should land near each other in that vector space. The chat LLM does **not** read vectors as evidence. The app uses vectors to find record IDs, loads the corresponding text or case values, and gives those bounded extracts to the chat LLM. The answer must link back to the original records. [Ollama describes this retrieval pattern](https://docs.ollama.com/capabilities/embeddings).

**Recommendation.** Make semantic retrieval a core part of the AI question flow. Build one short, factual text record per surgery from fields available in the EMR index, such as the procedure and documented tags. Keep age, sex, and other exact fields as filters. Build one record per article from its title and abstract when a local article collection exists. A URL or DOI is a locator and citation, not meaningful text to embed.

```text
Background preparation after setup or import
  ├─ EMR index → short case text → one case vector per PID
  └─ local article catalog → title + abstract → one article vector per paper

Researcher asks one question
  ├─ embed the question once with the same embedding model
  ├─ apply exact case filters and rank case and article vectors separately
  ├─ show a short, labeled candidate list while selected cases load
  ├─ fetch and analyze only the chosen or top bounded cases
  └─ pass source extracts to the local chat LLM for a cited answer
```

The app may prefetch a small shortlist before generation, then let the LLM call bounded `search_cases`, `open_case`, and `search_articles` tools if it needs more evidence. The app executes those calls, validates PIDs, and limits result counts. A useful default is up to 10 case candidates, up to 3 detailed case extracts, and up to 5 article abstracts. These are proposed budgets to evaluate, not current behavior. The UI distinguishes **indexed surgeries**, **analyzed cases**, and **articles** throughout.

Preparation runs as a background job and embeds only new or changed records. Store the source fingerprint, text hash, embedding model name, dimensions, and vector with each record. A model change rebuilds its vectors. At 384 float32 dimensions, 19,114 case vectors occupy about 29 MB before database overhead, so exact local similarity search is plausible for this corpus. The embedding computation time still needs measurement on the target workstation. Do not embed every minute of a timeline, raw waveform samples, or just article addresses. Use the existing `case_fetch.py` path for detailed case data after retrieval.

For articles, use a separate local SQLite catalog with stable ID, title, abstract, year, authors, DOI or URL, source, import date, and rights note. Full-text ranking remains useful for exact terms and as a fallback when the embedding model is unavailable. [SQLite FTS5 supports ranked keyword search](https://www.sqlite.org/fts5.html). The chat path stays local. Do not fetch remote pages during a case question or send case text to a cloud model. Ollama provides a [local embedding endpoint](https://docs.ollama.com/api/embed), but the exact model, batch size, and storage format are implementation choices to benchmark.

### What LitSieve contributes

The neighboring `HealthDatabaseAccess` project, called LitSieve, already embeds each article's **title plus abstract** in batches, stores a normalized vector and model name in SQLite, and embeds each search query with the same model. It ranks a semantic shortlist and can blend in exact-word scores. It also skips unchanged vectors and rebuilds them when the model changes. The relevant files are `app/services/embeddings.py`, `app/services/pipeline.py`, and `app/storage/database.py` in that project.

LitSieve's AI explanation then reads one selected abstract, with that abstract named as its evidence in `app/services/llm.py`. MOVER needs a different final step: one question can call for several cases, a signal interval, rule definitions, and perhaps articles. We can borrow LitSieve's preparation, model-version check, and hybrid ranking ideas without copying its multi-user web stack, public-source fetching, sentence-transformers, or FAISS dependency into this desktop app. A local vector matrix plus exact filters is the first performance comparison to run.

### Build sequence and checks

1. **Fix startup state.** Validate data paths before autoload, then show the AI question box. Show chat and embedding model readiness beside it. Check fresh install, saved valid settings, stale paths, cancel, and offline model cases.
2. **Prepare case embeddings.** Build one vector for each indexed surgery's short text in a background job. Keep exact demographic and data-availability filters. Rebuild only changed text or changed model versions. Measure preparation time and disk use on the 19,114-surgery corpus.
3. **Wire one question through retrieval.** Embed the question, show a case shortlist, analyze only a bounded number of cases, and send the resulting extracts to Ollama. Label each source and show retrieval and generation progress separately. Measure first answer time and whether the retrieved cases answer reviewed questions.
4. **Make A and C direct.** Put the question in focus, preserve it when C opens A, allow one-click source inspection, and keep B as an optional browse view. Test keyboard use, narrow windows, empty results, and model-unavailable behavior.
5. **Add article retrieval when a source collection exists.** Import a small, licensed local collection and embed its titles and abstracts in the background. Compare semantic, exact-word, and hybrid results against reviewed questions. Check citation completeness, duplicate handling, and empty results.
6. **Ground the answer.** Make each answer name the active case, analyzed scope, rule version, and cited records. Test that missing case data and missing article matches lead to an explicit “no evidence found” response rather than a confident synthesis.

Proposed performance targets for review, not measured claims: the main workspace becomes usable within 2 seconds for the current local sample; a metadata search responds within 1 second; opening one unanalyzed case shows progress immediately and completes within an agreed budget measured on the target workstation. Report cold and warm timings separately, including Ollama startup if the user asks for a model answer.

## Refinement path for an anesthesia research proof of concept

The first demonstrable workflow is retrospective: ask a question about historical, de-identified surgeries, inspect the retrieved cases and time-aligned signals, review the rule inputs, then read a local summary with source links. The user can always reach the original extract. Add article citations only when the article library exists and the cited abstract or local text is available.

The next evaluation uses synthetic cases with known injected anomalies, then a manually reviewed set of de-identified real cases. Record search relevance, flag agreement, data coverage, misleading answers, time to open a case, and how often users can find the source of a statement. Ask anesthesia researchers to review the interpretation of induction, maintenance, and emergence before treating flag counts as meaningful. Keep thresholds and dataset version visible, because the current rules are still awaiting calibration against real distributions.

### What the clinical decision support guidance means here

The product boundary stays explicit. This version is for research and education using historical data. It has no live patient feed, treatment directive, or time-critical alert. “Research only” describes intended use and user expectations; the label alone does not settle a future product's regulatory status.

The FDA's [January 2026 CDS guidance](https://www.fda.gov/media/109618/download) describes four criteria for a CDS function to be excluded from the medical-device definition under the cited statute. In plain terms, they address what kind of input the software analyzes, whether it displays or analyzes medical information, whether it supports a health professional rather than directing a specific treatment decision, and whether that professional can independently review the basis of the output. All four matter for that exclusion. This is an explanation of the guidance, not a classification of MOVER.

For this app, the first criterion deserves particular attention if the intended use ever changes. MOVER's rule engine analyzes sequences of ventilator and vital-sign measurements. The FDA describes repeated measurements from a signal acquisition system as a possible “pattern” and gives examples of software that analyzes such patterns. It would be unsafe to assume that adding citations or a disclaimer alone makes a future clinical version non-device CDS. [The guidance discusses signals and patterns in Section IV(1)](https://www.fda.gov/media/109618/download).

The fourth criterion explains the emphasis on inspectable evidence. A researcher should see which case, interval, source values, missing data, rule version, and article text support an answer. That is also good scientific practice, even though this proof of concept is not positioned for clinical decisions. The FDA [also discusses independent review and the effects of automation and time pressure](https://www.fda.gov/media/109618/download). Its [FAQ says time-critical decision support generally cannot meet the Non-Device CDS criteria](https://www.fda.gov/medical-devices/software-medical-device-samd/clinical-decision-support-software-frequently-asked-questions-faqs).

A later move toward clinical use would need a separate assessment of intended users and tasks, data rights, clinical validity, human factors, privacy, and regulatory status before deployment. The current proposal keeps the AI in retrospective research and teaching.

## Decisions needed before implementation

- Choose whether C should lead into A, as proposed here, or whether A should open directly for experienced researchers.
- Identify the article source, rights, and approximate record count. The repo has no article database today.
- Decide whether the synthetic demo is part of the shipped desktop experience or only used in research demonstrations.

The proposals would change the UI and data-loading behavior stated in `spec.md`. That contract needs the user's approval before implementation and must not be silently rewritten.
