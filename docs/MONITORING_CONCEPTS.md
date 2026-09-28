# Monitoring workspace drafts and retrieval plan

This document records the interface studies and the remaining research plan. The G evidence workspace, F signal detail, setup-first entry, and indexed-surgery retrieval are implemented in the desktop app. The [interactive drafts](prototypes/monitoring-concepts.html) still use fictional `SYN` cases and scripted responses; they do not connect to MOVER data, Ollama, or the filesystem.

The [selected G and F prototype](prototypes/monitoring-radical-concepts.html) starts in an evidence workspace and opens a signal detail view at a cited sample. H remains an alternate study. All three use the same small fictional dataset and scripted AI responses.

## What the current app does

The desktop validates the EMR folder at startup. On a first run or with an invalid path, it opens the modal Setup wizard before loading processed data. An older install with a valid saved EMR path opens directly into the app. Canceling first-run setup leaves the data tabs disabled until setup is completed.

Research (G) is the first tab. It searches indexed surgery metadata with cached local embeddings or keyword fallback, even without processed parquet. A selected chat model receives bounded case extracts for a research answer. Researchers can select a case with one click, inspect its signals in F, open source rows, and return with the question and comparison intact. Ask, Summary, Case timeline, and Rule reference remain as secondary tabs. Ask still uses the older processed-case tool flow.

The local data snapshot used for this draft has 19,114 rows in `patient_information.csv` and 50 cases in `cases.parquet`. Three warm-process measurements of `load_processed(validate=False)` took 0.022, 0.007, and 0.006 seconds for the 50-case cache. Three rebuilds of `emr_index()` took 0.250, 0.233, and 0.232 seconds. Those earlier timings cover data reads on this host, not window startup, chart rendering, Ollama startup, embedding preparation, or a full-corpus pipeline run. G now labels indexed surgeries separately from analyzed cases.

There is no article or publication database in the current repository. `retrieval.py` indexes EMR metadata, and `case_fetch.py` runs the pipeline for selected PIDs. Some older Ask tools still search only the loaded processed sample. An article library would be a new, optional data source.

## Five layout directions

| Draft | Structure | Best use | Tradeoff |
| --- | --- | --- | --- |
| A. AI workbench | A prominent question box, case shortlist, timeline, and source evidence on one screen. | Repeated questions about cases while keeping measurements and citations visible. | Dense on small screens. The evidence panel must stack below the timeline. |
| B. Cohort board | Wide searchable table, direct row selection, persistent case detail panel. | Manual filtering, comparison, and audit of the AI shortlist. | Adds a second, more complex navigation view. |
| C. Guided AI start | A large question box, then three stages: retrieve, inspect, and verify. | First-time use and teaching through a focused question. | Slower for researchers who already know which case they want. |
| D. Research notebook | A single flowing document: question, short AI brief, and expandable source cards beside each claim. No fixed case rail or timeline panel. | Reading and checking an AI synthesis with minimal navigation. | Cross-case comparisons and detailed signals need an explicit drill-in view. |
| E. Cohort atlas | A full-screen visual field of procedure groups. The AI question highlights a region, then a detail drawer opens the chosen case. | Discovering which part of a cohort an AI search selected. | Spatial placement can imply a clinical relationship that the data does not support; groups and legend must be explicit. |

These are different ways to organize the work, not color variations. A keeps question, case, timeline, and evidence visible at once. C starts with a guided task. D treats the answer as a research document whose citations open in place. E treats a cohort as a visual field and lets AI direct attention within it. B gives direct table control. The question is the first action in A, C, D, and E.

**Earlier direction (2026-09-27):** D led to A for case inspection, with E as an optional cohort discovery route. The user has since selected G as the primary direction and F as its signal detail component. A–E remain comparison drafts.

The earlier A–E prototype starts in D. A question builds an illustrative brief; its case card opens A's source context and a back button restores the brief and question. D can also open E with that question in context. E highlights a fictional group and opens its selected case in A, with a route back to the atlas. These actions use simple keyword selection and fictional cases. They do not run embeddings, case analysis, or the chat model.

E's implementation should group or aggregate results rather than draw 19,114 case markers. The map must state what position means and provide a list of the same results for keyboard and screen-reader users. The prototype places three fictional procedure groups by hand; distance means nothing.

### Further exploration: F, G, and H

The user selected G as the overall structure and F as an additional component. H was less convincing and remains an alternate experiment. G and F are now one connected prototype, not two equal top-level product destinations.

| Study | What organizes the screen | What the AI does | Try it | Tradeoff |
| --- | --- | --- | --- | --- |
| F. Signal studio | One wide timeline with synchronized signal tracks, a time scrubber, and a compact assistant below. | Moves the selected time to a peak or missing sample, then explains the source values. | Jump to peak pressure, inspect its source row, move through time with arrow keys, and overlay the knee case. | Strong for examining a case; a long literature answer would need a separate reading view. |
| G. Evidence canvas | A question, observation, source record, comparison, and uncertainty appear as connected objects. | Builds an inspectable evidence trail and adds a second case when asked. | Change the question to missing data, add a comparison, and open either source table. | Makes provenance visible; large investigations would need grouping to prevent clutter. |
| H. Review deck | One retrieved case fills a card, with an AI brief and source action. Save and leave-out actions advance the queue. | Organizes a small queue by a research question. | Save one case, leave out another, open the shortlist, then rebuild the queue around missing data. | Reduces simultaneous decisions; cross-case comparison is less immediate than A or F. |

F develops A's signal inspection into a full timeline workspace. G develops D's inspectable answer into an evidence board. H tests a different route through retrieved cases, with a single review decision at a time. The diagrams in G show provenance rather than causation or semantic distance.

### Selected G → F flow

1. After setup, G opens with an AI question. The answer is an evidence trail: an observation, the underlying case and coverage, a clearly stated limit, and an optional second case. The question stays visible and editable.
2. A researcher selects **Explore this signal in F** on an observation. F opens the same case at the cited minute, with synchronized PIP, ETCO₂, and heart-rate tracks. The user can scrub nearby samples and open the exact source row. A comparison observation opens F on the comparison case and minute.
3. **Back to evidence workspace** restores the same question and expanded comparison. F is a focused inspection surface, so the canvas does not have to display a full timeline in every evidence node.

The prototype demonstrates both peak-pressure and missing-sample questions. The source table remains available directly from G. F also works as a standalone comparison study from the draft navigation, and H remains available for evaluating case review. The desktop product now opens in G after setup and opens F from a selected case. H remains a prototype.

These studies contain three fictional records with 13 sample times each, spaced ten minutes apart. Peaks, missingness, chart cursors, comparisons, and source tables come from the same in-page records. The samples are manually authored for the interface draft and are not outputs of the MOVER pipeline. The AI controls recognize only peak-pressure and missing-data examples; unsupported questions receive an explicit message. H keeps its saved cases when the queue changes, and its reset button clears the review decisions. Nothing persists after a reload.

The setup preview opens before the new studies and identifies the fictional records and simulated AI connection. It links to the earlier folder-setup draft. It does not read folders or start a model.

The next evaluation should test whether users can ask in G, find the source of a statement, inspect the relevant minute in F, and return without losing the question. Test both a peak and a missing sample, then a second case. A short researcher review can identify when the canvas becomes too crowded and whether an optional H-style review queue would help with larger shortlists.

### Shared setup flow

Steps 1 and 2 are implemented for the required EMR path. The synthetic demo
entry, explicit case counts inside Setup, and a limited welcome screen remain
design work. Canceling the current first-run wizard disables the data tabs.

1. Open a modal before the workspace loads or starts Ollama. Ask for the EMR folder and show whether a local chat model and embedding model are ready. Offer the processed cache as an optional field. Keep the wave folder under Advanced settings.
2. Validate the chosen folder and show a precise result: “19,114 surgeries indexed,” “50 cases already analyzed,” or an error naming the missing file. Do not mark setup complete until the required path passes validation.
3. Offer a clearly marked synthetic demo path for first-time exploration. A canceled setup returns to a limited welcome screen instead of starting the app behind the dialog.
4. After setup, place the AI question box in focus and show the data-scope label. Allow Settings to reopen the same source panel. If Ollama is unavailable, say so beside the question box and keep manual case browsing usable.

The prototype's setup modal only demonstrates order, copy, and focus. It does not validate paths.

### Clickability and feedback requirements

The G/F desktop flow provides one-click case selection, 44 px question and
action controls, retrieval progress, and source rows. The remaining items below
are evaluation criteria rather than claims about the current release.

- A single click or keyboard Enter selects a case. A visible **Open timeline** button performs the next action. No primary action depends on a double-click.
- The question box is the dominant control. Suggested questions demonstrate finding similar cases, explaining a rule episode, and comparing documented patterns. The app reports retrieval progress before generation starts.
- Search, case selection, comparison, source rows, and question entry have labeled controls and a visible focus style. Interactive targets are at least 44 px high in the drafts.
- Every data state says what is available: **indexed**, **analyzed**, **loading this case**, **no matching cases**, or **source unavailable**. A failed load gives a retry action in context.
- A rule marker opens the measured values, source table, units, missingness, and threshold version. The assistant answer links to the same evidence view.
- The interface keeps “research and teaching only” visible without making it the main headline of every screen.

## AI-first retrieval plan

An embedding model turns text into a numeric vector. A question and a relevant record should land near each other in that vector space. The chat LLM does **not** read vectors as evidence. The app uses vectors to find record IDs, loads the corresponding text or case values, and gives bounded extracts to the chat LLM. Direct claim-to-source links in the answer remain a next step; researchers can inspect retrieved cases and source rows beside the answer. [Ollama describes this retrieval pattern](https://docs.ollama.com/capabilities/embeddings).

The current app builds one short factual record per indexed surgery from EMR metadata and caches local embeddings by text hash and model. Exact case fields remain filterable, and keyword ranking is available when embeddings fail. An article collection remains a proposal. Its records would use titles and abstracts; a URL or DOI is a locator and citation, not meaningful text to embed.

```text
Preparation on the first research search (implemented)
  └─ EMR index → short case text → cached case vector per PID

Researcher asks one question
  ├─ embed the question once with the same embedding model
  ├─ rank indexed surgeries; use keyword ranking if embeddings are unavailable
  ├─ show a short, labeled candidate list while selected cases load
  ├─ fetch and analyze only the chosen or top bounded cases
  └─ pass up to three case extracts to the selected local chat model

Future article path
  └─ local article catalog → title + abstract → article vector per paper
```

The current default returns up to 10 case candidates and loads up to 3 detailed case extracts for the chat model. The UI distinguishes indexed surgeries from analyzed cases. Bounded article search and an article scope label belong to a future source collection. A 5-abstract budget is a proposal to evaluate when that collection exists.

Preparation runs in a background worker on the first research question and embeds only new or changed records. SQLite stores the text hash, embedding model name, dimensions, and vector. A model change creates vectors under the new model ID. At 384 float32 dimensions, 19,114 case vectors occupy about 29 MB before database overhead. Embedding time still needs measurement on the target workstation. The app does not embed minute rows or waveforms; it uses `case_fetch.py` for detailed case data after retrieval.

For articles, use a separate local SQLite catalog with stable ID, title, abstract, year, authors, DOI or URL, source, import date, and rights note. Full-text ranking remains useful for exact terms and as a fallback when the embedding model is unavailable. [SQLite FTS5 supports ranked keyword search](https://www.sqlite.org/fts5.html). The chat path stays local. Do not fetch remote pages during a case question or send case text to a cloud model. Ollama provides a [local embedding endpoint](https://docs.ollama.com/api/embed), but the exact model, batch size, and storage format are implementation choices to benchmark.

### What LitSieve contributes

The neighboring `HealthDatabaseAccess` project, called LitSieve, already embeds each article's **title plus abstract** in batches, stores a normalized vector and model name in SQLite, and embeds each search query with the same model. It ranks a semantic shortlist and can blend in exact-word scores. It also skips unchanged vectors and rebuilds them when the model changes. The relevant files are `app/services/embeddings.py`, `app/services/pipeline.py`, and `app/storage/database.py` in that project.

LitSieve's AI explanation then reads one selected abstract, with that abstract named as its evidence in `app/services/llm.py`. MOVER's current research path can load several case extracts for one question. LitSieve's article handling and hybrid ranking remain references for a future collection. MOVER has not added LitSieve's web stack, public-source fetching, sentence-transformers, or FAISS.

### Implementation status and next checks

1. **Implemented: setup gate.** The EMR path is validated before autoload; G shows the question and model controls after setup. Invalid-path startup and model-unavailable behavior have focused automated tests. Fresh-install and cancel usability reviews remain.
2. **Implemented: case embeddings.** A background worker indexes one short record per surgery and caches vectors for unchanged text and model IDs. Measure preparation time and disk use on the 19,114-surgery corpus.
3. **Implemented: bounded retrieval and AI handoff.** A question returns a labeled case shortlist and up to three source extracts for the local model. Measure first-answer time and retrieval relevance with a running model and reviewed cases.
4. **Implemented: G-to-F flow.** G opens F at an observed or missing sample. F shows the source row and returns to the same question and comparison. Review keyboard use, narrow windows, long investigations, empty results, and model-unavailable behavior with researchers.
5. **Add article retrieval when a source collection exists.** Import a small, licensed local collection and embed its titles and abstracts in the background. Compare semantic, exact-word, and hybrid results against reviewed questions. Check citation completeness, duplicate handling, and empty results.
6. **Ground the answer.** Make each answer name the active case, analyzed scope, rule version, and cited records. Test that missing case data and missing article matches lead to an explicit “no evidence found” response rather than a confident synthesis.

Proposed performance targets for review, not measured claims: the main workspace becomes usable within 2 seconds for the current local sample; a metadata search responds within 1 second; opening one unanalyzed case shows progress immediately and completes within an agreed budget measured on the target workstation. Report cold and warm timings separately, including Ollama startup if the user asks for a model answer.

## Refinement path for an anesthesia research proof of concept

The first demonstrable workflow is retrospective: ask a question about historical, de-identified surgeries, inspect the retrieved cases and time-aligned signals, review the source values, then read a local summary. The user can open a source row from F. Claim-to-source links in the answer and article citations remain future work.

The next evaluation uses synthetic cases with known injected anomalies, then a manually reviewed set of de-identified real cases. Record search relevance, flag agreement, data coverage, misleading answers, time to open a case, and how often users can find the source of a statement. Ask anesthesia researchers to review the interpretation of induction, maintenance, and emergence before treating flag counts as meaningful. Keep thresholds and dataset version visible, because the current rules are still awaiting calibration against real distributions.

### What the clinical decision support guidance means here

The product boundary stays explicit. This version is for research and education using historical data. It has no live patient feed, treatment directive, or time-critical alert. “Research only” describes intended use and user expectations; the label alone does not settle a future product's regulatory status.

The FDA's [January 2026 CDS guidance](https://www.fda.gov/media/109618/download) describes four criteria for a CDS function to be excluded from the medical-device definition under the cited statute. In plain terms, they address what kind of input the software analyzes, whether it displays or analyzes medical information, whether it supports a health professional rather than directing a specific treatment decision, and whether that professional can independently review the basis of the output. All four matter for that exclusion. This is an explanation of the guidance, not a classification of MOVER.

For this app, the first criterion deserves particular attention if the intended use ever changes. MOVER's rule engine analyzes sequences of ventilator and vital-sign measurements. The FDA describes repeated measurements from a signal acquisition system as a possible “pattern” and gives examples of software that analyzes such patterns. It would be unsafe to assume that adding citations or a disclaimer alone makes a future clinical version non-device CDS. [The guidance discusses signals and patterns in Section IV(1)](https://www.fda.gov/media/109618/download).

The fourth criterion explains the emphasis on inspectable evidence. A researcher should see which case, interval, source values, missing data, rule version, and article text support an answer. That is also good scientific practice, even though this proof of concept is not positioned for clinical decisions. The FDA [also discusses independent review and the effects of automation and time pressure](https://www.fda.gov/media/109618/download). Its [FAQ says time-critical decision support generally cannot meet the Non-Device CDS criteria](https://www.fda.gov/medical-devices/software-medical-device-samd/clinical-decision-support-software-frequently-asked-questions-faqs).

A later move toward clinical use would need a separate assessment of intended users and tasks, data rights, clinical validity, human factors, privacy, and regulatory status before deployment. The current proposal keeps the AI in retrospective research and teaching.

## Next product decisions

- Decide the exact evidence-card fields and maximum number of visible cards after testing the G-to-F prototype with researchers.
- Identify the article source, rights, and approximate record count. The repo has no article database today.
- Decide whether the synthetic demo is part of the shipped desktop experience or only used in research demonstrations.

The implemented G/F and retrieval behavior is recorded in `spec.md`. Article retrieval and a shipped synthetic demo need separate scope decisions.
