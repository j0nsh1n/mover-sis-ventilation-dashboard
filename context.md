# context.md: MOVER SIS EMR data facts (M0)

Written Oct 17, 2026 from a read-only run of `scripts/inspect_schema.py` against
`/var/mnt/games/EMR` on jonathannobara. This file holds **no patient values**: only file sizes,
row counts, column names, types, null rates, distinct counts, timestamp *formats*,
aggregate relative-time statistics, and drug/measurement/event label names.
Educational trainer only, not clinical decision support.

## 1. Files at a glance

All 9 tables link to `patient_information` by **`PID`** (string id, unique per row there,
so one PID = one surgical case). There is no other case id. 0.1-0.2% of rows in the
child tables have a PID that isn't in `patient_information` (drop them on join).

| table | size | rows | distinct PID | cases covered (of 19,114) | full-row dups |
|---|---|---|---|---|---|
| patient_information | 3.4 MB | 19,114 | 19,114 | all | 0 |
| a_line | 0.18 MB | 2,989 | 2,951 | 2,947 (15.4%) | 38 |
| input_output | 6.0 MB | 100,993 | 18,267 | 18,252 (95.5%) | 26 |
| labs | 1.4 MB | 14,733 | 3,476 | 3,473 (18.2%) | 0 |
| medication | 20.2 MB | 373,852 | 19,001 | 19,001 (99.4%) | 236 |
| observations | 179 MB | 2,700,509 | 17,976 | 17,960 (94.0%) | 16,205 |
| procedure_events | 2.2 MB | 40,801 | 18,836 | 18,822 (98.5%) | 104 |
| ventilator | 76 MB | 1,048,575 | 5,574 | 5,567 (29.1%) | 0 |
| vitals | 201 MB | 3,595,591 | 19,023 | 19,007 (99.4%) | 0 |

Note: there are **19,114 cases**, not ~23.9k as first thought.

### Columns (inferred type, null %; "numeric x%" = share of values that parse as numbers)

Missing values are mostly written as the literal string `\N` (MySQL export style), **not**
as empty cells. So "null %" below is usually 0% and the real missing rate is
100% minus the numeric-parse share.

- **patient_information**: PID (string), Age (int), Ht (int; 99.3% numeric), Wt (int),
  Gender (string, 4 distinct values), OR_start, OR_end, Surgery_start, Surgery_end (time,
  0 missing), Procedure (free text, 17,120 distinct, 0.08% null). Never display Procedure,
  Age, Ht, Wt or Gender in logs.
- **a_line**: PID, PlacementTime (time), Location (5 labels), Laterality (3 labels).
- **input_output**: PID, Type (51 labels), Volume (int; no unit column, presumably mL), IO_datetime (time).
- **labs** (wide): PID, Obs_time, Na, K, Ca, Gluc, Ph, PCO2, PO2, BE, HCO3, HgB.
  About 53% numeric each (BE only 12%); the rest is `\N`. One row = one blood gas/panel.
- **medication**: PID, Start_time (time), End_time (time, 93.7% `\N`), Dose (float, 100%
  numeric), Drug_name (178 labels), Drug_units (10 labels). **No route column, and no
  explicit bolus/infusion column.**
- **observations** (wide): PID, Obs_time, SVV, PAPs, PAPm, PAPd, LAPm, CO, Cer_ox_r,
  Cer_ox_l, Temp, Temp2, TOF, SBP_FEM, MAP_FEM, DBP_FEM, ICPm, SBP_ART, MAP_ART, DBP_ART, CVPm.
  Mostly sparse; MAP_ART 38% of rows, Temp 81%.
- **procedure_events**: PID, Event_time (time), Event_name (18 labels).
- **ventilator** (wide): PID, Obs_time, Agent (4 labels), Agent_Fi, Agent_Et, ETC02, FIC02,
  TV, RR, PEEP, PIP, FIN20, ETN20, FI02, ET02.
- **vitals** (wide): PID, Obs_time, HRe, HRp, nSBP, nMAP, nDBP, SP02.

## 2. How time is stored

All times are **absolute wall-clock datetimes** (de-identified/shifted dates), no time zone,
no offsets. Two different formats are used:

| format | tables |
|---|---|
| `M/D/YY H:MM` (no zero padding, 2-digit year, minute resolution, no seconds) | patient_information (all 4 columns), medication (Start_time, End_time), ventilator, and **~1% of observations rows** |
| `YYYY-MM-DD HH:MM:SS` (ISO, seconds are present) | a_line, input_output, labs, observations (~99%), procedure_events, vitals |

The `M/D/YY` reading (month first) is consistent with the data: every format parsed 100%
and relative offsets look sensible (e.g. median medication start is 13 min after
Surgery_start; surgery is inside the OR window in 98.3% of cases).

**Conversion:** parse each column with an explicit format (try ISO, then `%m/%d/%y %H:%M`),
join on PID, then `minutes = (t - Surgery_start) / 60 s`. Negative = before incision.
With pandas' default inference only 98.96% of observations times parsed, because the
1% of `M/D/YY` rows get dropped, so always try both formats.

Case timing (all cases, minutes):
- Surgery duration: median 100, p5 19, p95 361. OR duration: median 155, p95 449.
- Surgery_start - OR_start: median 41 (p5 19, p95 94). OR_end - Surgery_end: median 11.
- 0 cases with end before start, 0 missing Surgery_start/OR_start/OR_end/Surgery_end.

Where each table sits relative to Surgery_start (minutes, p5 / median / p95), and data per case:

| table.time | p5 | median | p95 | rows before Surgery_start | rows outside OR window | per-case span median / p95 |
|---|---|---|---|---|---|---|
| vitals.Obs_time | -43 | 53 | 324 | 23.3% | 0.87% | 156 / 452 |
| observations.Obs_time | -31 | 68 | 344 | 15.9% | 0.66% | 128 / 436 |
| ventilator.Obs_time | -45 | 53 | 316 | 23.6% | 0.72% | 154 / 442 |
| medication.Start_time | -57 | 13 | 283 | 42.7% | 4.04% | 140 / 436 |
| medication.End_time | -14 | 103 | 411 | 0.65% | 9.44% | - |
| input_output.IO_datetime | -21 | 79 | 347 | 11.2% | 2.94% | 77 / 368 |
| labs.Obs_time | -13 | 104 | 426 | 9.2% | 0.71% | 86 / 446 |
| procedure_events.Event_time | -59 | 2 | 297 | 48.8% | 1.40% | 120 / 408 |
| a_line.PlacementTime | -123 | -45 | 16 | 91.8% | 7.77% | single event |

So the timeline should run from **OR_start to OR_end** (not Surgery_start to Surgery_end):
induction drugs, intubation, and line placement happen before incision.

## 3. Vitals and observations

Both are **wide** (one row per PID + timestamp, one column per measure). Units are not
stored; they are implied by the column name.

| measure | column | table | coverage | sampling | notes |
|---|---|---|---|---|---|
| HR (ECG) | `HRe` | vitals | 94.8% of rows, 19,011 cases | 1 min | bpm |
| HR (pulse oximeter) | `HRp` | vitals | 95.9%, 19,000 cases | 1 min | bpm |
| SpO2 | `SP02` (zero, not letter O) | vitals | 96.1%, 19,021 cases | 1 min | %, 100% of values in 50-100 |
| MAP noninvasive (cuff) | `nMAP` (+ nSBP, nDBP) | vitals | 19.9%, 18,935 cases | every 4-5 min (median 4, p90 5) | mmHg |
| MAP invasive, arterial line | `MAP_ART` (+ SBP_ART, DBP_ART) | observations | 38.4%, 3,800 cases | 1 min | mmHg; only 93.5% of values in 20-200, so artifacts exist (flush, zeroing) |
| MAP invasive, femoral | `MAP_FEM` | observations | 0.1%, 24 cases | 1 min | 81% plausible; ignore for MVP |
| Temperature | `Temp`, `Temp2` | observations | 81%, 10.5% | 1 min | values fit Celsius |

Other observations: CVPm (735 cases), PA pressures (~300), ICP (120), SVV/CO (~110),
cerebral oximetry (127, ~39 min gaps), TOF (8,193 cases, ~32 min gaps).
Ventilator: 1-min rows; Agent labels `S`, `N`, `D`, `I` (likely sevoflurane, nitrous oxide,
desflurane, isoflurane; unconfirmed).

## 4. Medication

- Drug name = `Drug_name` (178 clean, title-case generic names), dose = `Dose` (numeric),
  unit = `Drug_units`, start = `Start_time`, end = `End_time`. **No route column.**
- Units (10): mg 217,941; mcg 97,082; mL 46,040; units 11,588; mEq 602; puffs 572;
  spray 14; drops 9; pack 3; tablet 1. Infusions are also in plain `mcg`/`units`, not
  rates, so `Dose` for an interval row is probably a total amount; treat it as unconfirmed.
- **Bolus vs infusion is inferred:** End_time `\N` (93.7%) = point dose (bolus);
  End_time > Start_time (6.2%) = interval (infusion or drip; median 63 min, p95 325);
  End == Start 0.05%; End < Start 0%. Some names flag drips too ("Propofol  drip" with two
  spaces, "Sufentanil drip", "Amiodarone drip ml/hr", "Remifentanyl-d", "Octreotide - d").
- Fluids and blood products show up in medication **and** input_output (Plasmalyte, LR,
  PRBC, FFP...). Don't double count.

### Vasopressor and inotrope labels (no spelling variants; one label each)

| Drug_name | units | point (bolus?) rows / cases | interval (infusion?) rows / cases |
|---|---|---|---|
| Phenylephrine | mcg | 29,044 / 7,471 | 946 / 675 |
| Ephedrine | mg | 18,320 / 7,253 | 43 / 40 |
| Vasopressin | units | 3,130 / 788 | 197 / 161 |
| Epinephrine | mcg | 583 / 172 | 139 / 113 |
| Norepinephrine | mcg | 87 / 25 | 422 / 318 |
| Dobutamine (inotrope) | mcg | 0 | 260 / 223 |
| Milrinone (inotrope) | mcg | 9 / 9 | 49 / 36 |
| Dopamine | mcg | 1 / 1 | 47 / 39 |
| Isoproterenol | mcg | 0 | 7 / 4 |
| *Phenylephrine nasal* | drops | 9 / 8 | 0 (topical, **exclude**) |

10,770 cases have at least one of these rows. Also seen, but not vasopressors for this
rule: Methylene blue (224 rows, mostly a dye), Calcium chloride, Atropine, Glycopyrrolate.
Vasopressin appears once in input_output too (ignore).

## 5. Category labels

**input_output.Type** (51 labels; no in/out column, so direction comes from the label):
Urine 32,561; Plasmalyte 20,555; Ebl 17,253; Lactated ringers 12,537; Sodium chloride 0.9% 6,291;
Albumin human 5% 4,787; PRBC 3,178; Fresh frozen plasma 1,412; D5W 874; Platelets 516;
Cell saver blood 265; Cryoprecipitate 159; Dextrose 50% 80; TPN 63; Dextrose 5% 0.9 ns 57;
Dextrose 5% 0.45 ns 53; Sodium chloride 3% 49; Gastric contents 38; 3%NS 30;
Albumin human 25% 29; Dextrose 10% 24; Saline 3% 21; Dextrose 5% lr 21; Dextrose 5% 18;
D10halfNS 18; Dextran 40 14; Ng tube 13; D10w 11; Chest Tube 10; Hetastarch 9; 0.5NS 7;
Dexmedetomidine 5; Ascites 5; Argatroban 4; Liposuction 3; Mannitol 3; D5NS 2;
Dextrose 5% water 2; Ventriculostomy 2; Lumbar csf drain 2; Chest tube #2-right 2;
and 1 each: Drain contents, Vasopressin, D5NSk, Jp drain #1, D5HNk, Vancomycin,
Calcium chloride, Perfusion pump blood, Oxytocin, Bupivacaine 0.5%.
Outputs = Urine, Ebl, Gastric contents, Ng tube, Chest Tube/tube #2, Ascites, Liposuction,
drains, Ventriculostomy, Lumbar csf drain. Everything else = input.

**procedure_events.Event_name** (18): Extubation 15,258; Intubation 15,007; MD Handoff 4,732;
Sedation 2,343; LMA 1,675; TQ Up 911; CPBP On 197; Spinal 181; CPBP Off 178; Mayfield 165;
Aortic CC 99; CSE 31; Epidural 8; CPR 5; ANH out 3; ANH In 3; Circ Arrest 3; Defib 2.
("MD Handoff" is a real in-case handoff marker, useful for the trainer.)

**a_line.Location**: unknown 2,021; radial 885; femoral 48; brachial 32; dorsalis 3.
**a_line.Laterality**: unknown 2,090; left 487; right 412.

## 6. Data quality

- Cases missing Surgery_start: **0** (also 0 missing OR_start/OR_end/Surgery_end).
  Surgery window inside OR window: 98.3%.
- Full-row duplicates: observations 16,205 (0.6%; these are also exactly the duplicate
  PID+Obs_time rows), medication 236, procedure_events 104, a_line 38, input_output 26;
  0 in vitals, ventilator, labs, patient_information. Vitals has 0 duplicate PID+Obs_time.
- Rows outside [OR_start, OR_end]: vitals 0.87%, observations 0.66%, ventilator 0.72%,
  labs 0.71%, procedure_events 1.4%, input_output 2.9%, medication start 4.0% (end 9.4%),
  a_line 7.8%.
- **ventilator has exactly 1,048,575 rows = Excel's maximum row count.** The file was very
  likely cut off when it went through Excel, which explains why it covers only 29% of cases.
  Don't rely on it for MVP.
- **a_line covers 2,947 cases, but 3,800 cases have MAP_ART data.** The a_line table is
  incomplete (and Location/Laterality are mostly "unknown"). Use MAP_ART presence to decide
  "has arterial line".
- MAP_ART: 1.9% of non-null values aren't numeric and 6.5% of numeric values are outside
  20-200 mmHg (artifacts). Filter before the hypotension rule.
- `\N` strings everywhere stand for missing values (see section 1).

## 7. Demo case selection rule (counts only, no PID)

Suggested rule, applied in code with a fixed deterministic tie-break (for example, lowest
artifact rate, then sorted PID hash). Never print the chosen PID in chat or commits.

1. Surgery duration 60-240 min (10,683 cases)
2. Arterial MAP present: at least 30 MAP_ART readings (3,657 cases)
3. At least one vasopressor row from the list above, nasal excluded (10,770 cases)
4. At least one arterial MAP < 65 run lasting more than 5 min (1,872 cases)
5. At least one SpO2 < 90 run lasting more than 1 min (1,130 cases)

Cases meeting all 5: **112**. Relax 5 to "any SpO2 < 90 reading" with the a_line table
instead of 2: 287. Criteria 1+2(a_line)+3+4: 607. Nice to have: an "MD Handoff" or
Intubation event, and fewer than 5% MAP_ART artifacts.
Noninvasive-only hypotension (nMAP<65 >5 min) occurs in 5,944 cases.

## Implications for M1/M2

**Join keys.** `PID` everywhere, 1:1 with `patient_information`. Inner-join and drop
orphan PIDs (~0.1%). Deduplicate full-row duplicates first (observations especially).

**Reading.** Read with `na_values=["\\N", ""]` (Polars: `null_values=["\\N"]`), and
read every column as string first, then cast measure columns to float with errors turned
into nulls.

**Time conversion.** Parse with explicit formats, trying `%Y-%m-%d %H:%M:%S` then
`%m/%d/%y %H:%M`, per value (observations mixes both). Then
`t_min = (ts - Surgery_start).total_minutes()`. Keep negative minutes and use
OR_start..OR_end as the display window. Medication and case anchors only have minute
resolution, so vitals and drugs align to about ±1 min.

**Items for the three rules (MVP).**
1. *MAP < 65 sustained > 5 min:* `observations.MAP_ART` if the case has arterial data
   (1-min sampling; filter to 20-200 mmHg first), else `vitals.nMAP` (cuff, every 4-5
   min). With the cuff, "> 5 min" means **at least 2 consecutive low readings** spanning
   more than 5 min. Show which source was used. Optionally merge the two by time later.
2. *SpO2 < 90:* `vitals.SP02` (1-min). Filter 50-100 first. Decide at M2 whether one
   reading counts or a short persistence (for example ≥ 2 consecutive minutes) is needed
   to cut probe-off artifacts; one reading occurs in 6,140 cases, more than 1 min in 1,130.
3. *Vasopressor given:* `medication.Drug_name` in {Phenylephrine, Ephedrine, Vasopressin,
   Epinephrine, Norepinephrine} (core), optionally {Dopamine, Dobutamine, Milrinone,
   Isoproterenol} as inotropes; **exclude "Phenylephrine nasal"**. Event time =
   Start_time; bolus if End_time is null, infusion if End_time > Start_time.

**Gotchas.**
- Two datetime formats; 2-digit years; observations mixes both formats.
- `\N` strings mean missing; pandas reports 0% null unless you set na_values.
- Column `SP02` uses a zero; `FI02`, `ET02`, `ETC02`, `FIC02`, `FIN20`, `ETN20` too.
- No route column; no explicit bolus/infusion flag; infusion `Dose` units are amounts, not rates.
- Ventilator file is truncated at the Excel row limit; a_line table is incomplete.
- MAP_ART artifacts (~6.5% out of range).
- Fluids appear in both medication and input_output.
- Time zone unknown and dates are shifted; only relative minutes are meaningful.
- Never log PIDs, demographics, Procedure text, or raw timestamps; derived per-case files
  go to `/var/mnt/games/processed`, never into git.
