# G/F consolidation prototype plan

Status: planned. This document does not change the desktop app or the existing
G/F prototype.

## Decision to test

Test whether researchers can complete the work now split across Research, Ask,
Summary, Case timeline, and Rule reference within two connected views. G remains
the question-first evidence workspace. F remains the focused case and signal
view. Setup remains a modal before data loading.

The prototype should compare two placements for the same AI conversation: a
side panel beside the G evidence board and an inline thread above the board.
Both use the same case data and open the same F view. This tests conversation
placement without changing the chosen G/F structure.

## Where current functions go

| Current tab | Proposed home | Capability the prototype must show |
| --- | --- | --- |
| Research | G | Ask a research question, inspect retrieved cases and evidence, add a comparison, and open F at a cited minute. |
| Ask | G, with a contextual prompt in F | Keep a visible local AI conversation. Scenario analysis and comparison become question intents in G. A follow-up in F uses the selected case and minute. |
| Summary | G's Cases overview | Show the loaded cases-in-focus table and its case and rule charts. A single click selects a case and opens F. Label the analyzed subset separately from the indexed surgery count. |
| Case timeline | F | Show synchronized signals, episode and minute flags, source values, and the selected minute. |
| Rule reference | G reference drawer and F flag details | Keep the complete rule list searchable in G. A flag in F opens its rule definition, measured values, units, missingness, and threshold version. |

The prototype has no top-level tabs for the four older views. It must still
make every listed function reachable. The app's old tabs remain in place until
the prototype is reviewed and production parity is verified.

## Proposed screen structure

1. **Setup.** Open a modal first. Show fictional data readiness and simulated
   local model status. A canceled setup leaves the workspace unavailable.
2. **G evidence workspace.** Keep the question box and model status visible.
   Show a short answer as inspectable claim cards with source links, a case
   shortlist, comparison, and a stated limit. A Cases control swaps the board
   for the analyzed cases overview without clearing the question or conversation.
   A Rules control opens the full reference in a drawer.
3. **F case detail.** Open from a case, claim, chart point, or rule marker at the
   cited minute. Show PIP, ETCO2, and heart rate tracks, episode and minute
   flags, the matching source row, and a compact AI follow-up field. A rule
   marker opens its explanation beside the measurement. Back returns to the
   exact G question, case selection, comparison, and overview state.

The AI is part of the main workflow. If a model is unavailable, G still shows
search results and F still shows source data. The answer area says why no AI
answer appeared; it does not invent one.

## Clickable prototype scenarios

Use the existing fictional `SYN` records and scripted responses. No Ollama,
embeddings, EMR files, or persistence are involved.

1. Ask where pressure peaks. Follow the answer's case and minute into F,
   inspect the exact source row, then return to the same G question.
2. Enter a patient or procedure scenario as in today's Ask tab. Show a labeled
   candidate list and a bounded, source-linked scripted answer. Add a second
   case and keep it selected after returning from F.
3. Open Cases in G. Sort or select a row with one click, inspect the two summary
   charts, and open that case in F. Show the difference between indexed
   surgeries and analyzed cases in focus.
4. In F, scrub to a missing sample and an episode marker. Open the source row
   and the rule detail, including threshold version and the values used.
5. Open the complete rule list from G, find a rule, and return to the prior
   evidence state. Try an unsupported question and the model-unavailable
   switch; both show explicit messages.

All measurements, peaks, missingness, chart cursors, and source rows must come
from one in-page fictional dataset. Scripted AI must label itself as a demo.
The interface must not imply that a signal pattern proves cause or directs care.

## Review gate before desktop changes

- A researcher can perform each scenario without looking for an old tab.
- Every AI claim opens its case or source row. Missing samples remain missing.
- A return from F preserves the question, conversation, selected case, and
  comparison.
- The Cases overview preserves the current Summary functions and labels its
  analyzed scope. The complete rule list remains available from G.
- Primary actions work with one click and keyboard focus. Test the prototype
  at desktop and narrow widths, including long answers and empty results.
- Compare the two AI placements with the same tasks. Record where the evidence
  board becomes crowded and choose one placement before production work.

After that review, plan the desktop implementation in small steps: unify the
Ask conversation with G's retrieval and source scope; move Summary's charts
and table into the G Cases overview; place timeline flags and rule details in
F; then remove old tabs only after their functions and tests have a G/F home.
The current tab contract in `spec.md` needs explicit approval before it changes.
