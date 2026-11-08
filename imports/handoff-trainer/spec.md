# Spec (stub)

Status: stub from the M0 scaffold. Superseded by "Decided practice contracts" in the
suite's `spec.md`, which uses fictional cases only. Kept for history.

## Scope
- Retrospective, educational replay of one case at a time. Not clinical decision support.

## Event rules (visible to the learner)
1. **Hypotension:** MAP < 65 mmHg sustained for more than 5 minutes (consecutive readings).
   Source priority: arterial MAP (observations.MAP_ART), else noninvasive (vitals.nMAP).
2. **Desaturation:** SpO2 < 90% (vitals.SP02). Persistence threshold TBD at M2.
3. **Vasopressor given:** any medication row whose Drug_name matches the vasopressor list
   in `context.md`.

## Handoff + scoring (to be specified)
- I-PASS free-text form; keyword matching per event type.
- Score: mentioned / missed / unsupported, each with timeline evidence.
