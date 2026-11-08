# Roadmap

Build of M1+ waits until after the FlexWeek CAC submission (Oct 25, 2026).

- [x] **M0** Read-only schema inspection of the MOVER SIS CSVs; `context.md` written. (Oct 17, 2026)
- [ ] **M1** Rust core: load one case, convert timestamps to minutes from Surgery_start, write to processed/.
- [ ] **M2** Three transparent rules: MAP<65 sustained >5 min, SpO2<90, vasopressor given.
- [ ] **M3** Streamlit synchronized timeline (vitals lines; med/fluid/procedure markers; hidden event labels).
- [ ] **M4** I-PASS free-text handoff form.
- [ ] **M5** Keyword scoring: mentioned / missed / unsupported, with timeline evidence.
