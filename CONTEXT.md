# MOVER SIS Monitor — Living Project Context

**Purpose:** Keep agents and humans aligned on structure, behavior, and process.  
**Version file:** `VERSION` (semver `x.y.z`). Intended line after this work: **0.5.2** (may be uncommitted / uninstalled — see handoff).

---

## Maintenance rules (mandatory)

1. **Version numbers** — Keep `VERSION`, `src/__version__.py`, and release tags in sync (`x.y.z`).
2. **Ship local exe** after app changes: `./scripts/install_local.sh` → `~/.local/share/mover-sis-monitor`.
3. **Context hygiene** — Structural facts only; drop stale narrative after updates.
4. **PRs** preferred for non-trivial work. CI: Qt libs + `QT_QPA_PLATFORM=offscreen`.
5. **Research data only** — never present outputs as clinical decision support.

## Standing rules for every change (non-negotiable)

**Update this file as you program, not afterwards.** If a change alters structure,
adds a subsystem, or teaches you something about the data that cost effort to learn,
write it here in the same commit. A fact rediscovered twice is a process failure.

**De-bloat always.** Every change should leave the tree smaller or no larger where it
can. Before adding: is there an existing helper? Before shipping: what can be deleted?
Concretely — no new dependency without a written reason; prune PyInstaller includes
(the 0.6.0 artifacts were 465 MB Linux / 408 MB Windows, bundling all of scipy,
matplotlib, pyarrow, Qt); delete superseded code rather than leaving it dormant; keep
dead options out of `thresholds.yaml`.

**Test thoroughly, and test against real data.** Unit tests over synthetic fixtures
catch shape errors; only real MOVER files catch reality. Every finding in the
Waveforms and Research-safety sections below came from a real-data test, not a mock.
Rules: a test that skips is not a passing test — make it run or delete it; when a live
model or real corpus can be exercised, do so before claiming something works; verify
process-signalling code inside `unshare --pid` (see Handoff); a bug found in
production code means a regression test lands with the fix.

**Guardrails must keep this health-compliant.** The safety posture is code, not
convention. When adding a feature ask: does this create a path to patient-specific
advice, real-time use, or data egress? If yes, add a guardrail in the same change and
a test that proves it fires. See **Regulatory & clinical standards** below — the
FDA non-device CDS criteria in particular constrain what waveform analysis may claim.

---

## What the product is

Research **desktop app** for **MOVER SIS** perioperative data (UCI, 2015–2017).  
De-identified; **not for clinical care**.

- **EMR** = tabular SIS CSVs (`patient_*.csv`)
- **Wave** = waveform archives (`sis_wave*.tar.gz` or `Waveforms/<prefix>/<PID>/`)
- **PID** = surgery-level ID (not longitudinal patient)

**User paths (this machine, typical):** EMR `/var/mnt/games/EMR`, wave `/var/mnt/games/sis_wave_v2`, processed `/var/mnt/games/processed`, Ollama models `OLLAMA_MODELS=/var/mnt/games/LLM_Models`.

---

## Architecture

```
src/
  pipeline/          load → clean → merge → features → flags → run
  services/          ensure_data / load_processed
    case_fetch.py    on-demand EMR retrieval (index shortlist → flag shortlist)
  wave_decode.py     Bernoulli/GE S5 cpcArchive waveform decoding
  desktop/
    app.py           main window (LLM-first UI)
    theme.py         light/dark/system styles (low corner radius)
    charts.py        matplotlib ChartView (scrollable tall timelines)
    settings_ui.py   Setup wizard + Settings dialog
  llm/               Ollama co-pilot (scheduler-style tool loop)
  guardrails/        config/IO/data validation
  runtime_paths.py   EMR / wave / processed resolution
  search.py          keyword case filter
  user_settings.py   ~/.config/mover-sis-monitor/settings.json
```

**Processed cache:** `cases`, `timeseries`, `flags`, `episodes` parquet under `processed_dir`.

### Retrieval: fetch on demand, do not index

Measured on the real corpus (19,114 surgeries, 5,574 with ventilator rows):

| Step | Cost |
|------|------|
| Parse + search all of `patient_information.csv` | **0.045 s** — indexing buys nothing |
| Flag a 25-case shortlist on demand | **3.9 s** (≈2 s fixed CSV scan + ≈0.04 s/case) |
| Repeat query (memoised per `(pid, preset)`) | **0.004 s** |

So `services/case_fetch.py` shortlists from the EMR index (procedure text / age / gender,
no pipeline) and flags **only** the shortlist. Flags do not exist in any file — they are
derived — so this is compute-now vs compute-ahead, and compute-ahead goes stale whenever
`thresholds.yaml` or the preset changes.

**Known gap:** `corpus_overview` / `top_anomaly_cases` / `filter_by_agent` still answer from
whatever is cached, so the model can report "corpus size: 50 surgeries" as if that were the
corpus. Corpus-wide claims need either a full sweep or explicit scoping in the tool output.

### UI (LLM-first)

| Area | Behavior |
|------|----------|
| Tab **1 · Ask** | Primary: local co-pilot chat |
| Tab **2 · Summary** | Charts + case table for **focus** list |
| Tab **3 · Case timeline** | Active PID charts (scrollable PNG) |
| Tab **4 · Rule reference** | Threshold YAML text |
| Sidebar | Ollama lifecycle + model + corpus/active/focus — **no** pipeline/filter panels |
| File menu | Setup, Reload processed, **Run pipeline**, Open EMR/Wave |
| ⚙ icon (menu-bar corner) | Settings: paths, Ollama models dir, theme (`Ctrl+,`; no Edit menu) |
| View | Theme Light / Dark / System |

Modes on Ask tab: **Chat** · **Case analysis** (default) · **Compare**.

---

## Local LLM (Ollama) — research co-pilot

Pattern inspired by [Local-Schedule-Assistant](https://github.com/j0nsh1n/Local-Schedule-Assistant): native tool schemas, multi-round tool loop, text-tool recovery, per-model guidance, Start/Stop/Unload.

| Module | Role |
|--------|------|
| `llm/schemas.py` | `AI_TOOLS`, `extract_tool_calls`, `strip_think`, `MAX_TOOL_ROUNDS=8` |
| `llm/tools.py` | App-executed tools + `SessionState` |
| `llm/ollama_client.py` | `/api/chat` (+ tools), `/api/generate` unload (`keep_alive: 0`) |
| `llm/agent.py` | Turn loop; **app pre-fetch** for patient vignettes; verify answer |
| `llm/prompts.py` | Safety, auto-workflow, modes; `wrap_patient_description()` |
| `llm/model_profiles.py` | Curated models + family guidance |
| `llm/service.py` | `probe` / `start_ollama` / `stop_ollama` / `unload` / `ensure_ollama` |
| `llm/case_context.py` | Full case briefing text |

### Tools (app-executed)

`corpus_overview`, `search_cases`, `find_similar_cases`, `top_anomaly_cases`, `filter_by_agent`, `select_case`, `get_case_briefing`, `summarize_management`, `compare_cases`, `list_case_flags`, `rule_reference`, `wave_status`, `verify_selection`.

### Intended UX for the user

User should only **describe the patient/scenario** (e.g. “55y woman hysterectomy sevo high PIP”).  
In **Case analysis** mode the app + model should **automatically**:

1. `find_similar_cases` / search  
2. Select best match(es)  
3. `summarize_management` (+ flags/rules as needed)  
4. `verify_selection`  
5. Structured research answer (matched cases, documented management, flags, gaps, disclaimer)

**Language:** “In matching extracts, documented management included…” — **not** live clinical orders.

### Research-safety enforcement (answer gate)

A local model *will* drift into prescriptive advice — qwen3:14b produced “Consider reducing
TV”, “optimizing PEEP”, “require immediate intervention” on a plain vignette, and the
original narrow regex passed all of it. Three layers now, all in `prompts.py` / `agent.py`:

| Layer | What it does |
|-------|--------------|
| `prompts.PHRASING` | Explicit GOOD/BAD rewrite pairs + banned advice verbs; past tense mandated |
| `prompts.ACCURACY` | Must state the matched case's **actual** age/sex and call out differences from the described patient |
| `agent.find_directive_phrases` | Modal (“should be reduced”), imperative (“- Optimize PEEP”), urgency (“requires immediate intervention”) and goal (“focus on avoiding”) framing |
| `agent.find_unstated_case_facts` | Answer must contain the selected case's real age |
| `run_agent` rewrite round | On any violation the draft is **rejected and rewritten** (temperature 0), quoting the offending text back; the better draft wins |

Descriptive past tense stays legal — “PEEP was increased at t=40” must not trip the
detector, and a test asserts that. Annotating prescriptive text with a disclaimer is not
sufficient: it still ships the prescription.

Observed failures this guards (do not regress), all from live qwen3:14b runs on the plain
vignette `55y woman, hysterectomy, sevoflurane, elevated PIP`:

- prescriptive: “Consider reducing TV”, “optimizing PEEP”, “require immediate intervention”,
  and worst — **“Administer albuterol”**, a drug order for a drug absent from the record
- accuracy: claimed “Age: 55 (aligned)” for case `aab853b9b64fbc65`, which is **60**
- doses: “Phenylephrine 50 mcg” where the record documents **100 mcg**; “Vecuronium
  0.15 mg/kg” where doses are absolute mg

Confirmed working end-to-end: the draft is rejected, rewritten, and the accepted answer
reads “**Patient**: 60F (described patient: 55F)” with doses matching the record.

Two traps when writing these checks — both were hit:

- `\b60\b` does not match `60F` (no word boundary between two word characters)
- testing whether the digits “60” appear *anywhere* is useless: they turn up in flag
  timestamps and in the verification note the checker itself appends. Extract **asserted**
  ages instead.

### Ollama lifecycle (sidebar)

| Control | Behavior |
|---------|----------|
| **Start** | `ollama serve` with `OLLAMA_MODELS` from settings; retries after Stop |
| **Stop** | Kill serve + runners; wait until API down so Start works without app restart |
| **Unload** | `keep_alive: 0` for selected model (VRAM free, server stays up) |
| **Refresh** | Probe only (does not start) |

Status label: running / stopped. First **Ask** may auto-start if server is down.

**Process-signalling invariant (`stop_ollama`):** never signal anything we did not spawn.

- `_child_pgid()` validates the held child's pid is a real `int > 1` before `killpg`, and rejects our own group. `os.killpg(1, sig)` is `kill(-1, sig)` — SIGTERM to **every** process the user owns (kills the desktop session). A non-int pid (e.g. a `MagicMock` from a patched `Popen`) coerces to `1` via `__index__`, so validation is mandatory.
- Process matching uses `pgrep` with patterns anchored to the binary (`(^|/)ollama serve( |$)`, …), never a bare `ollama` substring — that also matches `pytest tests/test_ollama_service.py` and editors.
- `_is_safe_signal_target()` excludes pid ≤ 1, self, parent, and our own process group / session.

---

## Waveforms (`src/wave_decode.py`)

Bernoulli / GE S5 `cpcArchive` XML: 30-min files, `<cpc>` blocks holding one `<mg>` per
signal. `Wave` is base64 little-endian ints (`PointsBytes` wide), scaled `value = sample *
gain + offset`. Signals: `GE_ART`, `INVP1` (pressures), `GE_ECG`/`ECG1`, `AWP`, `FLOW`,
`CO2`, `PLETH`, `RESP`.

Facts established against the real corpus — each one cost a wrong assumption, so do not
re-derive them by guessing:

1. **Gains.** The reference decoder (`waveform_decode.py`, Feb 2023) hard-codes `GE_ART`
   → 0.25, `INVP1` → 0.01 because v1 gains were wrong. The dataset README's v2 release
   (2024-05, "some of the wave gains were off") fixed them in-file, and v2 XML now carries
   exactly those values — so override-first is correct for v1 and a no-op for v2.
2. **`-32767`/`-32768` are NODATA sentinels**, not measurements (samples are 12-bit,
   −2048…2047). Decoded to NaN; an unused channel otherwise reports a median of −32767.
3. **Four channel states** — `nodata` (all sentinel), `idle` (flat ≈0), `implausible`
   (saturated ±450, median ≈0, ~70% outside physiological bounds), `active`. A span check
   alone accepts saturated junk, so pressures also get :func:`looks_physiological_pressure`.
   **Only plot `active` under a clinical label.**
4. **Archives interleave.** Files with no measurement groups sit between files holding real
   traces, so reading `files[0]` alone reports "no signals" for a case with a good A-line.
5. **A live arterial line is rare** — of 40 sampled cases, 27 had no `GE_ART` at all, 11
   idle, 2 saturated. Reference case with a clean trace: `02004a742e0d1bc4` (on `INVP1`,
   median 76 mmHg, range 45–132).

## Regulatory & clinical standards

Not legal advice — confirm scope with the IRB / privacy office. These are the frames
this tool must stay inside, and the reference points its measures should use.

| Frame | What it means here |
|-------|--------------------|
| **HIPAA Safe Harbor** (45 CFR §164.514(b)(2)) | MOVER is de-identified under it. Ages > 89 must be aggregated — the 211 cases ≥ 89 and the `Age` range reaching 117 are de-identification artifacts, **not** real ages. Never re-identify or join to identifiable data. |
| **MOVER DUA** (UC Irvine) | Governs the data regardless of this repo's GPL-3.0. No redistribution of data, no re-identification attempts. Code is licensed; data is not. |
| **Common Rule** (45 CFR §46) | De-identified secondary analysis is usually "not human subjects research", but that determination is the IRB's, not ours. |
| **FDA CDS guidance** (2022, per Cures Act §3060) | Four criteria keep software outside the device definition. Criterion 1 excludes software that **analyzes signals from a signal-acquisition system** — so waveform analysis is the risky area. Stay retrospective, de-identified, non-patient-specific, and always show the basis so a reader can independently review it. Never real-time, never a directive. |
| **IEC 62304 / ISO 14971** | Only if scope ever shifts toward a device. Named so the decision is deliberate. |
| **STROBE + RECORD** | Reporting standards for observational / routinely-collected health data. Export artifacts should carry what these require (cohort definition, denominators, missingness). |
| **MPOG / ASPIRE measures** | Published intraoperative quality measures. Prefer these over bespoke thresholds so results are comparable to the literature. |

Clinical reference points for measures (cite in output, do not present as advice):

- **Lung-protective ventilation** — TV 6–8 mL/kg *predicted* body weight, PEEP ≥ 5,
  driving pressure (Pplat − PEEP) < 15 cmH₂O. Note the corpus example ran 10.9 mL/kg.
- **Intraoperative hypotension** — MAP < 65 mmHg; the standard exposure metric is
  time-weighted area under the threshold, not a count of flagged minutes.
- **Hyperoxia** — sustained high FiO₂ without indication (`fio2_high_long`).
- **PIP is not driving pressure.** Peak pressure conflates resistance and compliance;
  say so wherever PIP is reported.

## Settings keys

`emr_dir`, `wave_dir`, `processed_dir`, `data_dir`, `ollama_models_dir`, `ollama_model`, `theme` (`light`|`dark`|`system`), `setup_complete`.  
Config dir: `MOVER_CONFIG_DIR` or `~/.config/mover-sis-monitor/`.

Path resolution: **env** → session/settings → defaults. External processed next to EMR allowed (`resolve_output_dir` / `MOVER_ALLOW_EXTERNAL_OUTPUT`).

---

## How to run

```bash
PYTHONPATH=. python -m src.desktop
./scripts/install_local.sh && mover-sis-monitor
PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q
PYTHONPATH=. python -m src.pipeline.run --n-cases 50
```

---

## Handoff / unfinished work (for next agent)

Work was **in progress** when interrupted. Intended version **0.5.2** on branch `feature/v0.2-ollama-integrated-build` (many files modified/untracked — not necessarily committed or installed).

### Done in tree (verify before shipping)

- LLM-first UI; Setup/Settings; themes; scrollable charts  
- Scheduler-style tools/agent/prompts  
- Sidebar Start / Stop / Unload / Refresh  
- **Patient-description-only workflow** (prompts auto-wrap + `agent._heuristic_prefetch` runs find → manage → verify before model write)  
- **Start-after-Stop hardening** in `service.py` (wait until port/API free, start retries, UI re-enables Start after Stop)

### Still need next agent to finish

1. ~~**Tests**~~ — **done 2026-07-26: 133 passed** (`PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q`).  
   Earlier runs were not flaky — they were **killing the user's desktop session**. `stop_ollama()` fed a leaked `MagicMock` (left in the `_started_proc` module global by a `Popen`-patching test) to `os.killpg(os.getpgid(pid), 15)`; the mock pid coerced to `1`, and `killpg(1, …)` = `kill(-1, …)` = SIGTERM to every process of uid 1000 → instant Plasma logout. The old `pkill -f ollama` fallback separately matched the pytest command line, which is why runs came back "incomplete/killed". Both fixed in `llm/service.py`; `tests/conftest.py` now resets the service globals per test, stubs `_pgrep`, and hard-fails any real `os.killpg`.  
   Still true for mocks: keep `list_models` failing **until** `Popen` is called (`if Popen.call_count == 0: raise`).  
   When changing process-signalling code, verify in a sandbox first:  
   ```bash
   unshare --user --map-root-user --pid --fork --mount-proc \
     env PYTHONPATH=. QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
   ```
2. **`./scripts/install_local.sh`** so local `mover-sis-monitor` is **0.5.2** (or current `VERSION`) — the installed build still predates this fix.
3. **Manual check:** Stop Ollama → Start again **without** restarting the app; Unload frees VRAM; Ask with a short patient line only, e.g.  
   `55y woman, hysterectomy, sevoflurane, elevated PIP`  
   in **Case analysis** mode.
4. **Optional:** PR for the feature branch; sync `VERSION` / README if not already 0.5.2 everywhere.
5. Confirm untracked files are intentional and should be committed:  
   `src/desktop/settings_ui.py`, `theme.py`, `src/llm/agent.py`, `schemas.py`, `tools.py`, `model_profiles.py`, `tests/test_llm_agent.py`, `tests/test_settings_theme.py`.

### Key files to read first

- `src/llm/service.py` — Start/Stop lifecycle  
- `src/llm/agent.py` — `_heuristic_prefetch`, `run_agent`  
- `src/llm/prompts.py` — `wrap_patient_description`, `AUTO_WORKFLOW`  
- `src/desktop/app.py` — `_ollama_start` / `_ollama_stop`, Ask tab UI  
- `tests/test_ollama_service.py`, `tests/test_llm_agent.py`

### Reference product

Local Schedule Assistant: https://github.com/j0nsh1n/Local-Schedule-Assistant  
(`ai.py` / `aipanel.py` / `ai_tools.py` for tool loop + Start/Stop/Unload patterns).

---

## Roadmap (approved 2026-07-28)

Ordered by value and dependency. Phase 0 first — later phases inherit its correctness.

### Phase 0 — correctness and trust (unblocked)

| # | Item | Why it is first |
|---|------|-----------------|
| 0.1 | **Data-quality gating** | `Ht` spans 0.0–254.0 cm and `Wt` 0–839 kg. `tv_high_mlkg` is the most-fired rule and divides by predicted body weight, so `Ht=0` produces a confident critical flag from nonsense. Suppress weight-normalised rules when anthropometrics are implausible and label the case. Everything downstream inherits this. |
| 0.2 | **Localhost guard for Ollama** | `OllamaClient(base_url=…)` accepts any URL. A remote host silently ships DUA-covered case text off-machine. Refuse non-loopback unless an explicit override env var is set; show the destination in the UI. |
| 0.3 | **Denominator honesty + wire `case_fetch`** | `corpus_overview` reports the cache size (50) as if it were the corpus (5,574 analysable of 19,114). Every aggregate must carry its denominator. |
| 0.4 | **Facts from code, prose from model** | The model retypes numbers and has altered them (100 mcg → 50 mcg). App renders demographics / vent ranges / flag counts / drug table as a block the model cannot edit; the LLM writes only narrative. Collapses a whole error class instead of patching it. |

### Phase 1 — make the output usable as research

| # | Item | Notes |
|---|------|-------|
| 1.1 | **Reproducible export** | JSON + Markdown artifact: run id, `VERSION`, model + quantization + temperature, threshold preset, PIDs, tool trace, denominators, missingness. Without this nothing here can support a paper. Align fields with STROBE/RECORD. |
| 1.2 | **Quality-measure alignment** | Re-express rules as MPOG/ASPIRE-style measures so results are comparable to published work. Keep bespoke thresholds only where no standard exists. |
| 1.3 | **Hypotension exposure** | Time-weighted AUC under MAP 65. Caveat: `nMAP` is present in only ~20 % of vitals rows (intermittent cuff), so the interpolation choice (sample-and-hold vs linear) changes the number — make it explicit and configurable. Analyse arterial-line cases separately. |
| 1.4 | **Ventilation beyond PIP** | Driving pressure / compliance where derivable; state plainly when plateau pressure is absent. `AWP` + `FLOW` waveforms can give dynamic compliance and P–V loops. |
| 1.5 | **Peer percentiles** | "PIP 33, 88th percentile for this procedure" beats any threshold. Cheap once 0.3 lands. |
| 1.6 | **Timeline context** | Overlay intubation / incision / extubation from `patient_procedure_events.csv` and drug administrations. Charts currently shade critical flags only (`charts.py` `axvline`), so a PIP rise cannot be read against airway events. |
| 1.7 | **Guideline comparison layer** | Factual comparison, never advice: "documented TV 10.9 mL/kg PBW; lung-protective practice is 6–8". Descriptive-only framing otherwise risks normalising non-guideline care for trainees. |
| 1.8 | **Surface the tool trace + de-bloat** | `tool_trace` exists but is not shown; showing which tools ran and what they returned is the cheapest trust win. Trim PyInstaller includes (465 MB / 408 MB artifacts). |

### Phase 2 — EPIC outcome linkage (blocked on NAS extraction)

The single biggest scientific unlock: SIS holds **no outcomes**, so today the tool can
only describe process. EPIC adds postoperative creatinine (AKI), troponin, LOS.

**Extract only these — 27.5 GB, not 600:**

| Archive | Size | Purpose |
|---------|------|---------|
| `EPIC_EMR.tar.gz` | 1.0 GB | encounters / diagnoses |
| `Epic_flowsheets_cleaned.tar.gz` | 9.9 GB | nursing + ward observations |
| `EPIC_patient_measurments.tar.gz` | 16.6 GB | labs — creatinine, troponin |
| `EPIC_MRN_PAT_ID.csv` | 3.4 MB | **the crosswalk** |

The six `epic_wave_*.tar.gz` are ~571 GB and contribute **nothing** to outcomes. Skip
them.

**HDD-aware ingest (the NAS is spinning disk over the network):**

1. Read each archive **exactly once**, streaming; never re-scan the NAS.
2. Convert to parquet on fast local storage (next to `processed_dir`), partitioned by
   patient/encounter, with only the columns needed.
3. Verify against the shipped `md5sum_*.txt` before ingest; record a manifest
   (file, checksum, row counts, ingest timestamp) so a partial run is resumable.
4. All later queries hit parquet. Budget the NAS pass once, in the background.

**First investigation, before any code:** confirm that SIS `PID` can actually reach
EPIC identifiers via `EPIC_MRN_PAT_ID.csv`. SIS `PID` is surgery-level and
de-identified; if no defensible join exists, Phase 2 stops there and we say so rather
than inventing a fuzzy match on time and demographics. **Do not guess a join.**

### Phase 3 — depth

- **Cohort builder** (filter → N → aggregate → CSV) — research is aggregate, not
  case-at-a-time; more valuable than further LLM polish.
- **Waveform rendering** — decoder is ready (`wave_decode.py`). Needs decimation
  (min/max envelope per pixel column) and must draw only `active` channels. Watch the
  FDA criterion-1 line: retrospective display, never real-time interpretation.
- Per-file GPL headers if wanted (mechanical, deferred).

## Release checklist

- [ ] Bump `VERSION` + `src/__version__.py`
- [ ] This `CONTEXT.md` accurate
- [x] Tests green (`pytest -q` offscreen) — 133 passed
- [ ] `./scripts/install_local.sh`
- [ ] PR / merge / GitHub Release asset for `v{x.y.z}`
