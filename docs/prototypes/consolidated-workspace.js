'use strict';
/* MOVER consolidated G/F workspace study. Fictional data, scripted AI, no persistence, no network. */

const THRESHOLD_VERSION = 'default preset, thresholds.yaml snapshot 2026-09-30';
const RESEARCH_NOTE = 'A flag marks a recorded value beyond a configured threshold. It does not show a cause or indicate what should be done.';

// ---- Rules: ids, descriptions and default thresholds copied from src/config/thresholds.yaml ----
const RULES = [
  { id: 'pip_high', desc: 'Elevated peak inspiratory pressure', warn: 30, crit: 35, unit: 'cmH2O', signal: 'pip', dir: 'high' },
  { id: 'pip_rising', desc: 'Rising PIP suggesting reduced compliance / obstruction', params: 'window_min 10 · min_slope_cmh2o_per_min 0.5 · min_duration_min 5 · min_pip 20', unit: 'cmH2O', signal: 'pip', sev: 'warn' },
  { id: 'peep_high', desc: 'Elevated PEEP', warn: 12, crit: 15, unit: 'cmH2O' },
  { id: 'peep_zero_long', desc: 'Prolonged zero/negative PEEP during mechanical ventilation', params: 'max_peep 0 · min_duration_min 15 · min_tv 200 · min_rr 6' },
  { id: 'tv_low_mlkg', desc: 'Low tidal volume per ideal body weight', warn: 4, crit: 3, unit: 'mL/kg IBW', low: true },
  { id: 'tv_high_mlkg', desc: 'High tidal volume per ideal body weight', warn: 10, crit: 12, unit: 'mL/kg IBW' },
  { id: 'tv_abs_extreme', desc: 'Absolute tidal volume outside adult physiologic band', params: 'adult_age_min 16 · low 100 · high 1000', unit: 'mL' },
  { id: 'rr_low', desc: 'Low respiratory rate', warn: 6, crit: 4, params: 'min_tv 200', low: true },
  { id: 'rr_high', desc: 'High respiratory rate', warn: 20, crit: 30 },
  { id: 'etco2_high', desc: 'Elevated end-tidal CO2', warn: 50, crit: 60, unit: 'mmHg', signal: 'co2', dir: 'high' },
  { id: 'etco2_low', desc: 'Low end-tidal CO2 (excluding zeros)', warn: 28, crit: 22, unit: 'mmHg', signal: 'co2', dir: 'low', low: true },
  { id: 'etco2_zero_vent', desc: 'Zero ETCO2 while ventilated (disconnect / sensor / esophageal)', params: 'min_tv 200 · min_rr 8 · min_duration_min 3' },
  { id: 'fio2_high_long', desc: 'Prolonged high FiO2 after induction window', params: 'fio2_min 90 · min_duration_min 30 · skip_first_min 15' },
  { id: 'fio2_room_air_vent', desc: 'Near room-air FiO2 while mechanically ventilated', params: 'fio2_max 25 · min_tv 200 · min_rr 6 · min_duration_min 5' },
  { id: 'agent_high', desc: 'High end-tidal agent relative to age-adjusted MAC', warn: 1.5, crit: 2.0, unit: 'MAC' },
  { id: 'agent_low_maint', desc: 'Very low agent MAC during mid-case maintenance window', params: 'max_mac 0.3 · case_frac_start 0.2 · case_frac_end 0.8 · n2o_et_exempt 30' },
  { id: 'agent_drift', desc: 'Agent concentration drift from recent median', params: 'window_min 15 · min_duration_min 5 · delta_vol_pct S 0.5, I 0.5, D 1.5, N 0.5' },
  { id: 'agent_fi_et_gap', desc: 'Large Fi-Et agent gap mid-case', params: 'min_duration_min 10 · case_frac_start 0.15 · case_frac_end 0.85 · gap_vol_pct S 1.0, I 1.0, D 3.0' },
  { id: 'spo2_low', desc: 'Low oxygen saturation', warn: 92, crit: 88, unit: '%', low: true },
  { id: 'hr_high', desc: 'Tachycardia', warn: 120, crit: 140, unit: 'bpm', signal: 'hr', dir: 'high' },
  { id: 'hr_low', desc: 'Bradycardia', warn: 45, crit: 40, unit: 'bpm', signal: 'hr', dir: 'low', low: true },
  { id: 'map_low', desc: 'Low noninvasive MAP', warn: 60, crit: 50, unit: 'mmHg', low: true },
  { id: 'compliance_concern', desc: 'Rising PIP with stable/falling TV (compliance pattern)', params: 'composite of other rules' },
  { id: 'hypoventilation_pattern', desc: 'High ETCO2 with low TV or low RR', params: 'composite of other rules' },
  { id: 'desat_with_vent_issue', desc: 'Desaturation concurrent with ventilation anomaly', params: 'composite of other rules' }
];
const RULE = Object.fromEntries(RULES.map(rule => [rule.id, rule]));
const thresholdText = rule => rule.warn === undefined ? (rule.params || 'no numeric thresholds') : `${rule.low ? '<=' : '>='} warn ${rule.warn} · ${rule.low ? '<=' : '>='} critical ${rule.crit}${rule.unit ? ' ' + rule.unit : ''}${rule.params ? ' · ' + rule.params : ''}`;

const SIGNALS = [
  { key: 'pip', label: 'PIP', unit: 'cmH2O', color: '#f0b17b', min: 10, max: 42 },
  { key: 'co2', label: 'ETCO2', unit: 'mmHg', color: '#8bd4c0', min: 15, max: 65 },
  { key: 'hr', label: 'Heart rate', unit: 'bpm', color: '#b6afe9', min: 35, max: 150 }
];
const SIGNAL = Object.fromEntries(SIGNALS.map(signal => [signal.key, signal]));

// ---- Fictional dataset: per-minute tracks are generated deterministically, gaps are null ----
const wob = (i, seed) => Math.sin(i * 1.7 + seed) * 0.6 + Math.sin(i * 0.63 + seed * 2) * 0.4;
const tri = (i, a, p, b, amp) => (i <= a || i >= b ? 0 : amp * (i < p ? (i - a) / (p - a) : (b - i) / (b - p)));
const makeTrack = (dur, base, noise, seed, bumps = [], gaps = []) => {
  const values = Array.from({ length: dur }, (_, i) => Math.round(base + noise * wob(i, seed) + bumps.reduce((sum, bump) => sum + tri(i, ...bump), 0)));
  gaps.forEach(([from, to]) => { for (let i = from; i <= to; i++) values[i] = null; });
  return values;
};
const SPECS = [
  { pid: 'SYN-1041', proc: 'Laparoscopic cholecystectomy', age: 54, dur: 120, pip: [19, 1, 1, [[38, 62, 80, 17]]], co2: [36, 1, 2, [[50, 64, 84, 17]], [[44, 46]]], hr: [74, 2, 3, [[50, 62, 80, 9]]] },
  { pid: 'SYN-1108', proc: 'Total knee arthroplasty', age: 68, dur: 110, pip: [17, 1, 4, [[30, 60, 90, 5]], [[75, 76]]], co2: [34, 1, 5, [], [[51, 51]]], hr: [68, 2, 6, [[30, 60, 90, 6]]] },
  { pid: 'SYN-1223', proc: 'Thyroidectomy', age: 47, dur: 90, pip: [16, 1, 7, []], co2: [35, 1, 8, []], hr: [72, 2, 9, []] },
  { pid: 'SYN-1305', proc: 'Robotic-assisted laparoscopic prostatectomy', age: 63, dur: 150, pip: [22, 1, 10, [[30, 118, 150, 12]]], co2: [37, 1, 11, [[60, 110, 150, 15]]], hr: [66, 2, 12, []] },
  { pid: 'SYN-1377', proc: 'Posterior lumbar spinal fusion', age: 59, dur: 140, pip: [24, 1, 13, [[60, 72, 84, 8]]], co2: [34, 1, 14, []], hr: [62, 2, 15, [[24, 32, 40, -20]], [[92, 95]]] },
  { pid: 'SYN-1412', proc: 'Hip fracture repair', age: 81, dur: 100, pip: [20, 1, 16, []], co2: [33, 1, 17, [[36, 44, 52, -8]]], hr: [84, 2, 18, [[62, 72, 82, 40]]] },
  { pid: 'SYN-1526', proc: 'Laparoscopic colectomy', age: 71, dur: 130, pip: [23, 1, 19, [[66, 86, 102, 16]]], co2: [36, 1, 20, [], [[100, 114]]], hr: [78, 2, 21, []] },
  { pid: 'SYN-1590', proc: 'Ventral hernia repair', age: 66, dur: 105, pip: [18, 1, 22, [], [[10, 10]]], co2: [38, 1, 23, [[24, 31, 38, 23]]], hr: [72, 2, 24, []] }
];
const OTHER_INDEXED = [
  ['SYN-1650', 'Laparoscopic appendectomy', 57, 'novent'], ['SYN-1688', 'Cataract extraction', 77, 'novent'],
  ['SYN-1701', 'Cystoscopy', 61], ['SYN-1712', 'Laparoscopic inguinal hernia repair', 45], ['SYN-1723', 'Total hip arthroplasty', 72],
  ['SYN-1735', 'Carotid endarterectomy', 69], ['SYN-1741', 'Mastectomy', 52], ['SYN-1756', 'Lumbar laminectomy', 58],
  ['SYN-1768', 'Laparoscopic sleeve gastrectomy', 41], ['SYN-1779', 'Craniotomy', 56], ['SYN-1783', 'Cesarean delivery', 33],
  ['SYN-1794', 'Rotator cuff repair', 49], ['SYN-1806', 'Hysterectomy', 50], ['SYN-1817', 'Coronary artery bypass', 67],
  ['SYN-1828', 'Thoracotomy', 63], ['SYN-1839', 'Whipple procedure', 64], ['SYN-1842', 'Shoulder arthroplasty', 70],
  ['SYN-1855', 'Nephrectomy', 60], ['SYN-1861', 'Abdominal aortic repair', 74], ['SYN-1877', 'Parotidectomy', 55],
  ['SYN-1884', 'Femoral hernia repair', 79], ['SYN-1896', 'Laparoscopic cholecystectomy', 38]
];

const sevOf = (rule, value) => {
  if (value === null) return null;
  const beyond = limit => (rule.low ? value <= limit : value >= limit);
  return beyond(rule.crit) ? 'critical' : beyond(rule.warn) ? 'warn' : null;
};

// Minute flags follow the rule thresholds; a missing sample is never flagged and always ends an episode.
function computeFlags(c) {
  const minuteFlags = [];
  ['pip_high', 'etco2_high', 'etco2_low', 'hr_high', 'hr_low'].forEach(id => {
    const rule = RULE[id];
    c[rule.signal].forEach((value, minute) => {
      if (id === 'etco2_low' && !(value > 0)) return;
      const sev = sevOf(rule, value);
      if (sev) minuteFlags.push({ minute, rule: id, sev, value, signal: rule.signal });
    });
  });
  const runs = [];
  let run = [];
  c.pip.forEach((value, minute) => {
    const before = c.pip[minute - 10];
    const slope = minute >= 10 && value !== null && before !== null && before !== undefined ? (value - before) / 10 : null;
    if (slope !== null && slope >= 0.5 && value >= 20) run.push({ minute, rule: 'pip_rising', sev: 'warn', value, signal: 'pip', slope });
    else { runs.push(run); run = []; }
  });
  runs.push(run);
  runs.filter(items => items.length >= 5).forEach(items => minuteFlags.push(...items));
  minuteFlags.sort((a, b) => a.minute - b.minute || a.rule.localeCompare(b.rule));
  const episodes = [];
  const open = {};
  minuteFlags.forEach(flag => {
    const current = open[flag.rule];
    if (current && current.end === flag.minute - 1) { current.end = flag.minute; current.flags.push(flag); }
    else { open[flag.rule] = { rule: flag.rule, start: flag.minute, end: flag.minute, flags: [flag], signal: flag.signal }; episodes.push(open[flag.rule]); }
  });
  episodes.forEach(ep => {
    ep.sev = ep.flags.some(flag => flag.sev === 'critical') ? 'critical' : ep.flags[0].sev;
    const values = ep.flags.map(flag => flag.value);
    ep.extreme = RULE[ep.rule].low ? Math.min(...values) : Math.max(...values);
    ep.extremeMinute = ep.flags.find(flag => flag.value === ep.extreme).minute;
  });
  episodes.sort((a, b) => a.start - b.start);
  return { minuteFlags, episodes };
}

const CASES = SPECS.map(spec => {
  const c = { pid: spec.pid, proc: spec.proc, age: spec.age, dur: spec.dur, status: 'analyzed' };
  SIGNALS.forEach(signal => { c[signal.key] = makeTrack(spec.dur, ...spec[signal.key]); });
  Object.assign(c, computeFlags(c));
  c.missing = SIGNALS.flatMap(signal => c[signal.key].flatMap((value, minute) => (value === null ? [{ signal: signal.key, minute }] : [])));
  const pipValues = c.pip.filter(value => value !== null);
  c.peak = Math.max(...pipValues);
  c.peakMinute = c.pip.indexOf(c.peak);
  c.warnMin = c.minuteFlags.filter(flag => flag.sev === 'warn').length;
  c.critMin = c.minuteFlags.filter(flag => flag.sev === 'critical').length;
  c.score = c.warnMin + 3 * c.critMin;
  const worst = c.episodes.find(ep => ep.sev === 'critical') || c.episodes[0];
  c.focusMinute = worst ? worst.extremeMinute : c.peakMinute;
  return c;
});
const CASE = Object.fromEntries(CASES.map(c => [c.pid, c]));
const INDEXED = [...CASES, ...OTHER_INDEXED.map(([pid, proc, age, kind]) => ({ pid, proc, age, status: kind || 'notanalyzed' }))];
const COUNTS = { indexed: INDEXED.length, analyzed: CASES.length, novent: INDEXED.filter(c => c.status === 'novent').length, notAnalyzed: INDEXED.filter(c => c.status === 'notanalyzed').length };
const STATUS_LABEL = { analyzed: 'Analyzed', notanalyzed: 'Indexed, not analyzed', novent: 'No ventilator data' };

// ---- UI ----
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = text => String(text).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
const show = value => (value === null || value === undefined ? '—' : value);
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const announce = message => { $('#live').textContent = ''; setTimeout(() => { $('#live').textContent = message; }, 30); };
const sevLabel = sev => (sev === 'critical' ? 'critical' : sev === 'warn' ? 'warn' : 'info');
const flagsAt = (c, minute) => c.minuteFlags.filter(flag => flag.minute === minute);
const short = (text, n = 60) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

const BLOCKED_MESSAGE = 'This demo does not answer questions about causes, proof, or what care to give. It only describes recorded values, missing samples, and rule flags. Try asking where a signal peaks, which samples are missing, or which rules fired.';
const UNSUPPORTED_MESSAGE = 'The scripted demo does not recognize this question, so no answer was produced. It supports four kinds: where pressure peaks, which samples are missing, which rules flagged episodes, and a patient or procedure scenario such as a 58-year-old laparoscopic case.';
const NO_AI_MESSAGE = 'No AI answer: the local model is unavailable (simulated). Nothing was generated or guessed. Keyword search results and source data are still shown.';

const state = {
  ready: false, modelOn: true, place: 'side', view: 'board', turns: [], active: -1,
  selected: null, compare: null, drawer: { open: false, query: '', rule: null },
  sort: { key: 'score', dir: 'desc' }, f: null, gScroll: 0, returnKey: null, question: ''
};

// ---- Retrieval and scripted answers (all numbers come from CASES) ----
const tokens = text => text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
const VOCAB = new Set(INDEXED.flatMap(c => tokens(c.proc)).filter(w => w.length >= 5 && !['total', 'repair', 'procedure'].includes(w)));
const STOP = new Set(['patient', 'case', 'cases', 'with', 'high', 'airway', 'pressure', 'year', 'years', 'old', 'undergoing', 'surgery', 'procedure', 'during', 'that', 'this', 'have', 'which', 'what', 'where', 'does', 'find', 'show', 'similar']);
const parseAge = text => { const m = text.match(/(\d{1,3})[\s-]*(?:year|yr|yo|y\/o)/i); return m ? Number(m[1]) : null; };
const signalFilter = text => (/etco2|co2/.test(text) ? 'co2' : /heart|\bhr\b|tachy|brady/.test(text) ? 'hr' : /\bpip\b|pressure/.test(text) ? 'pip' : null);

function classify(q) {
  const t = q.toLowerCase();
  if (/\b(why|cause[sd]?|because|treat\w*|should|dos(e|ing)|recommend\w*|diagnos\w*|prove[sd]?|manage|intervene|harm\w*)\b/.test(t)) return 'blocked';
  if (/missing|gap|coverage|dropout/.test(t)) return 'missing';
  if (parseAge(t) !== null || /patient|surgery|procedure|scenario|undergo/.test(t) || tokens(t).some(w => VOCAB.has(w))) return 'scenario';
  if (/etco2|co2|heart|\bhr\b|tachy|brady|rule|flag|episode|threshold|critical|warn/.test(t)) return 'flags';
  if (/peak|highest|maximum|\bmax\b|pressure|pip/.test(t)) return 'peak';
  return 'unsupported';
}

const gapRanges = (c, key) => {
  const ranges = [];
  c.missing.filter(item => !key || item.signal === key).forEach(item => {
    const last = ranges[ranges.length - 1];
    if (last && last.signal === item.signal && last.to === item.minute - 1) last.to = item.minute;
    else ranges.push({ signal: item.signal, from: item.minute, to: item.minute });
  });
  return ranges;
};
const rangeText = r => `${SIGNAL[r.signal].label} ${r.from === r.to ? `minute ${r.from}` : `minutes ${r.from} to ${r.to}`}`;
const pipHighMinutes = c => c.minuteFlags.filter(flag => flag.rule === 'pip_high').length;

function retrieve(q) {
  const t = q.toLowerCase();
  const kind = classify(q);
  const total = COUNTS.analyzed;
  const base = { kind, intent: kind, claims: [], cands: [], spark: 'pip', summary: '', limit: '', listLabel: '' };
  if (kind === 'blocked') return { ...base, summary: BLOCKED_MESSAGE };
  if (kind === 'unsupported') return { ...base, summary: UNSUPPORTED_MESSAGE };
  if (kind === 'peak') {
    const ranked = [...CASES].sort((a, b) => b.peak - a.peak);
    return { ...base, kind: 'ok', listLabel: 'Analyzed cases ranked by peak PIP',
      cands: ranked.slice(0, 5).map(c => ({ c, minute: c.peakMinute, note: `Peak PIP ${c.peak} cmH2O at minute ${c.peakMinute}` })),
      claims: ranked.slice(0, 3).map(c => ({ pid: c.pid, minute: c.peakMinute, source: `PIP row, minute ${c.peakMinute}`, text: `${c.pid} (${c.proc}) records a PIP of ${c.peak} cmH2O at minute ${c.peakMinute}. ${pipHighMinutes(c) ? `${plural(pipHighMinutes(c), 'minute')} carry a pip_high flag.` : 'No pip_high flag is raised in this case.'}` })),
      summary: `Among the ${total} analyzed cases, the highest recorded PIP is ${ranked[0].peak} cmH2O in ${ranked[0].pid} at minute ${ranked[0].peakMinute}. The three highest peaks are listed as claims; each opens its source row.`,
      limit: `These are recorded pressures in ${total} analyzed cases of ${COUNTS.indexed} indexed surgeries. They describe values, not why pressure changed or what to do.` };
  }
  if (kind === 'missing') {
    const key = signalFilter(t);
    const withGaps = CASES.filter(c => c.missing.some(item => !key || item.signal === key)).sort((a, b) => gapRanges(b, key).length - gapRanges(a, key).length || b.missing.length - a.missing.length);
    const first = c => gapRanges(c, key)[0];
    return { ...base, kind: withGaps.length ? 'ok' : 'empty', spark: key || 'co2', listLabel: `Analyzed cases with missing ${key ? SIGNAL[key].label : 'PIP, ETCO2 or heart rate'} samples`,
      cands: withGaps.map(c => ({ c, minute: first(c).from, note: gapRanges(c, key).map(rangeText).join('; ') })),
      claims: withGaps.slice(0, 3).map(c => ({ pid: c.pid, minute: first(c).from, source: `${SIGNAL[first(c).signal].label} column, minute ${first(c).from}`, text: `${c.pid} has missing samples: ${gapRanges(c, key).map(rangeText).join('; ')}. The gaps stay blank on the trace and in the source row.` })),
      summary: withGaps.length ? `${withGaps.length} of ${total} analyzed cases have at least one missing ${key ? SIGNAL[key].label : 'PIP, ETCO2 or heart rate'} sample. Missing samples are never filled or set to zero.` : `No analyzed case has a missing ${SIGNAL[key].label} sample.`,
      limit: 'A missing sample means the value is unknown at that minute. Coverage should be checked before comparing signal shapes.' };
  }
  if (kind === 'flags') {
    const key = signalFilter(t);
    const matching = c => c.episodes.filter(ep => !key || ep.signal === key);
    const ranked = CASES.filter(c => matching(c).length).sort((a, b) => b.score - a.score);
    const worst = c => matching(c).find(ep => ep.sev === 'critical') || matching(c)[0];
    return { ...base, kind: ranked.length ? 'ok' : 'empty', spark: key || 'pip', listLabel: `Analyzed cases with ${key ? SIGNAL[key].label : 'rule'} flag episodes`,
      cands: ranked.map(c => ({ c, minute: worst(c).extremeMinute, note: `${plural(matching(c).length, 'episode')}; most severe ${worst(c).rule} (${worst(c).sev})` })),
      claims: ranked.slice(0, 3).map(c => { const ep = worst(c); return { pid: c.pid, minute: ep.extremeMinute, rule: ep.rule, source: `${SIGNAL[ep.signal].label} column, minutes ${ep.start} to ${ep.end}`, text: `${c.pid} has a ${ep.sev} ${ep.rule} episode over minutes ${ep.start} to ${ep.end}, with ${ep.extreme} ${RULE[ep.rule].unit || ''} at minute ${ep.extremeMinute}.` }; }),
      summary: ranked.length ? `${ranked.length} of ${total} analyzed cases have flagged episodes${key ? ` on ${SIGNAL[key].label}` : ''}. Flags compare recorded values with the ${THRESHOLD_VERSION} thresholds.` : `No analyzed case has flagged episodes${key ? ` on ${SIGNAL[key].label}` : ''}.`,
      limit: RESEARCH_NOTE };
  }
  // scenario: keyword and age match on procedure text over all indexed surgeries
  const age = parseAge(t);
  const words = tokens(t).filter(w => w.length >= 4 && !STOP.has(w) && !/^\d/.test(w) && VOCAB.has(w));
  const pressure = /pressure|pip|airway/.test(t);
  const scored = INDEXED.map(c => {
    const hits = words.filter(w => c.proc.toLowerCase().includes(w));
    const ageGap = age === null ? null : Math.abs(c.age - age);
    const ok = words.length ? hits.length > 0 : ageGap !== null && ageGap <= 10;
    const score = hits.length * 3 + (ageGap === null ? 0 : Math.max(0, 2 - ageGap / 10)) + (pressure && c.peak ? c.peak / 50 : 0);
    return { c, ok, score, hits };
  }).filter(item => item.ok).sort((a, b) => b.score - a.score).slice(0, 7);
  const analyzed = scored.filter(item => item.c.status === 'analyzed');
  const others = scored.filter(item => item.c.status !== 'analyzed');
  const noVent = others.filter(item => item.c.status === 'novent');
  const cands = scored.map(item => ({ c: item.c, minute: item.c.peakMinute, note: item.c.status === 'analyzed' ? `${item.hits.length ? `Matches "${item.hits.join(', ')}"` : 'Age match'}${age !== null ? `, age ${item.c.age}` : ''}; peak PIP ${item.c.peak} at minute ${item.c.peakMinute}` : `Matches the procedure text; ${STATUS_LABEL[item.c.status].toLowerCase()}` }));
  const parts = [`${plural(analyzed.length, 'analyzed case')} match${analyzed.length === 1 ? 'es' : ''} the scenario`];
  if (others.length) parts.push(`${others.length} other indexed ${others.length === 1 ? 'surgery matches' : 'surgeries match'} but cannot be opened in F${noVent.length ? ` (${noVent.map(item => item.c.pid).join(', ')}: no ventilator data)` : ''}`);
  return { ...base, kind: scored.length ? 'ok' : 'empty', intent: 'scenario', spark: 'pip', listLabel: 'Candidate list: keyword and age match on procedure text, not a validated similarity', cands,
    claims: analyzed.slice(0, 3).map(item => ({ pid: item.c.pid, minute: item.c.peakMinute, source: `PIP row, minute ${item.c.peakMinute}`, text: `${item.c.pid}, age ${item.c.age}, ${item.c.proc}: peak PIP ${item.c.peak} cmH2O at minute ${item.c.peakMinute}${item.c.missing.length ? `; ${plural(item.c.missing.length, 'missing sample')}` : ''}.` })),
    summary: scored.length ? `${parts.join('. ')}. Candidates are ranked by keyword overlap and age closeness; this is a lookup, not a similarity model.` : `No indexed surgery matches this scenario. ${COUNTS.analyzed} analyzed cases and ${COUNTS.notAnalyzed + COUNTS.novent} other indexed surgeries were searched by procedure text${age !== null ? ' and age within 10 years' : ''}.`,
    limit: 'A match here means similar words or age, not similar physiology. Nothing here establishes cause or what care a scenario needs.' };
}

// ---- Shared drawing helpers ----
function segments(values, xOf, yOf) {
  const out = [];
  let current = [];
  values.forEach((value, i) => {
    if (value === null) { if (current.length) out.push(current); current = []; } else current.push([xOf(i), yOf(value)]);
  });
  if (current.length) out.push(current);
  return out;
}
const drawSegments = (segs, attrs) => segs.map(s => (s.length === 1
  ? `<line x1="${s[0][0].toFixed(1)}" x2="${(s[0][0] + 0.01).toFixed(1)}" y1="${s[0][1].toFixed(1)}" y2="${s[0][1].toFixed(1)}" stroke-linecap="round" ${attrs}/>`
  : `<polyline points="${s.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')}" fill="none" stroke-linejoin="round" ${attrs}/>`)).join('');
const yOfSignal = (signal, height, pad) => value => height - pad - (value - signal.min) / (signal.max - signal.min) * (height - 2 * pad);

function spark(c, key) {
  const signal = SIGNAL[key];
  const w = 200, h = 44, n = c[key].length;
  const gapBands = c[key].map((value, i) => (value === null ? `<rect class="gap" x="${(i / n * w).toFixed(1)}" y="0" width="${(w / n).toFixed(1)}" height="${h}"/>` : '')).join('');
  const line = drawSegments(segments(c[key], i => (i + 0.5) / n * w, yOfSignal(signal, h, 5)), 'stroke="currentColor" stroke-width="2"');
  const missing = c[key].filter(value => value === null).length;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${signal.label} trend for ${c.pid}${missing ? `, ${plural(missing, 'missing sample')} shown as gaps` : ''}">${gapBands}${line}</svg>`;
}

// ---- G: rendering ----
function renderModel() {
  const on = state.modelOn;
  $('#model-switch').setAttribute('aria-checked', String(on));
  $('#model-switch-label').textContent = `Local model: ${on ? 'available' : 'unavailable'} (simulated)`;
  $('#setup-model-switch').setAttribute('aria-checked', String(!on));
  $('#setup-ready').innerHTML = [
    ['Data source', 'Fictional demo dataset, no folder read'], ['Indexed surgeries', COUNTS.indexed], ['Analyzed cases in focus', COUNTS.analyzed],
    ['Ventilator data', `${COUNTS.indexed - COUNTS.novent} of ${COUNTS.indexed} indexed (${COUNTS.novent} have none)`], ['Threshold set', THRESHOLD_VERSION],
    ['Local chat model', on ? 'Ready (simulated)' : 'Unavailable (simulated)'], ['Embedding model', on ? 'Ready (simulated)' : 'Unavailable (simulated)']
  ].map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('');
  if (state.f) renderFModel();
}

function renderPlacement() {
  const side = state.place === 'side';
  $('#place-side').setAttribute('aria-pressed', String(side));
  $('#place-inline').setAttribute('aria-pressed', String(!side));
  $('#g-body').classList.toggle('place-side', side);
  (side ? $('#side-slot') : $('#inline-slot')).appendChild($('#thread'));
  $('#thread').classList.toggle('inline', !side);
  $('#side-slot').hidden = !side;
  $('#thread-place').textContent = side ? 'SIDE PANEL' : 'INLINE THREAD';
  scrollThread();
}

function scrollThread() {
  const list = $('#thread-list'), last = list.querySelector('.turn:last-child');
  list.scrollTop = 0;
  if (last) list.scrollTop = last.getBoundingClientRect().top - list.getBoundingClientRect().top;
}

const chipFor = turn => (turn.noAi ? '<span class="chip off">No AI answer</span>' : '<span class="chip demo">Demo — scripted response</span>');

function renderThread() {
  const list = $('#thread-list');
  if (!state.turns.length) { list.innerHTML = '<p class="empty">No questions yet. Ask above. The conversation stays here while you open a case in F and come back.</p>'; return; }
  list.innerHTML = state.turns.map((turn, index) => {
    const isG = !turn.ctx;
    const sources = (turn.claims || []).map((claim, i) => `<button type="button" class="src-link" data-open-f="${claim.pid}|${claim.minute}" data-fk="tsrc-${index}-${i}" aria-label="Claim ${i + 1}: open ${claim.pid} at minute ${claim.minute} in F">${i + 1} · ${claim.pid} · min ${claim.minute} ↗</button>`).join('');
    const action = !isG ? '' : turn.noAi ? `<button type="button" class="text-action" data-rerun="${index}" data-fk="rerun-${index}">Ask again</button>` : index === state.active ? '<span class="shown">Shown on the board</span>' : `<button type="button" class="text-action" data-show-turn="${index}" data-fk="show-${index}">Show on board</button>`;
    return `<article class="turn"><p class="turn-q"><span class="who">You</span>${esc(turn.q)}${turn.ctx ? `<span class="ctx">${esc(turn.ctx)}</span>` : ''}</p><div class="turn-a">${chipFor(turn)}<p>${esc(turn.text)}</p>${sources ? `<div class="sources"><span>Sources</span>${sources}</div>` : ''}${action}</div></article>`;
  }).join('');
  scrollThread();
}

function renderWorkingSet() {
  const chip = (label, pid, clear) => (pid ? `<span class="ws-chip"><b>${label}</b> ${pid} <button type="button" data-clear="${clear}" data-fk="clear-${clear}" aria-label="Clear ${label.toLowerCase()} ${pid}">×</button></span>` : `<span class="ws-empty"><b>${label}</b> none</span>`);
  $('#working-set').innerHTML = `<span class="ws-label">Working set</span>${chip('Selected', state.selected, 'selected')}${chip('Comparison', state.compare, 'compare')}<span class="ws-hint">Kept when you open F and come back.</span>`;
}

function candidateCard(cand, fkPrefix) {
  const c = cand.c;
  if (c.status !== 'analyzed') {
    const reason = c.status === 'novent' ? 'No ventilator data: F needs per-minute signals, so this case cannot be opened.' : 'Indexed but not analyzed: no signals are loaded, so this case cannot be opened.';
    return `<article class="cand cand-off"><div class="cand-head"><b class="mono">${c.pid}</b><span class="chip ${c.status}">${STATUS_LABEL[c.status]}</span></div><p class="cand-proc">${esc(c.proc)} · age ${c.age}</p><p class="cand-note">${esc(cand.note)}</p><button type="button" class="ghost-light" aria-disabled="true" data-noopen="${c.pid}" data-fk="${fkPrefix}-${c.pid}">Open in F unavailable</button><p class="cand-status" data-status="${c.pid}" role="status">${reason}</p></article>`;
  }
  const isSel = state.selected === c.pid, isCmp = state.compare === c.pid;
  return `<article class="cand${isSel ? ' is-selected' : ''}${isCmp ? ' is-compare' : ''}"><div class="cand-head"><b class="mono">${c.pid}</b><span class="chip analyzed">Analyzed</span></div><p class="cand-proc">${esc(c.proc)} · age ${c.age}</p><div class="cand-spark">${spark(c, state.sparkKey)}</div><p class="cand-note">${esc(cand.note)}</p><div class="cand-actions"><button type="button" data-select="${c.pid}" data-fk="sel-${fkPrefix}-${c.pid}" aria-pressed="${isSel}">${isSel ? 'Selected' : 'Select'}</button><button type="button" data-compare="${c.pid}" data-fk="cmp-${fkPrefix}-${c.pid}" aria-pressed="${isCmp}" ${isSel ? 'disabled' : ''}>${isCmp ? 'In comparison' : 'Compare'}</button><button type="button" class="primary-mini" data-open-f="${c.pid}|${cand.minute}" data-fk="${fkPrefix}-${c.pid}">Open in F, min ${cand.minute} ↗</button></div></article>`;
}

function comparisonCard() {
  const a = CASE[state.selected], b = CASE[state.compare];
  if (!a || !b) return `<section class="card compare-card"><span class="node-id">COMPARISON</span><h2>${state.selected ? 'Add a second case to compare.' : 'Select a case, then add one to compare.'}</h2><p>Use Select and Compare on any analyzed case above. The two cases stay in the working set when you open F and return.</p></section>`;
  const col = c => `<div class="cmp-col"><b class="mono">${c.pid}</b><span>${esc(c.proc)}, age ${c.age}</span><div class="cand-spark">${spark(c, 'pip')}</div><dl><dt>Peak PIP</dt><dd>${c.peak} cmH2O at minute ${c.peakMinute}</dd><dt>Flag minutes</dt><dd>${c.minuteFlags.length} in ${plural(c.episodes.length, 'episode')}</dd><dt>Missing samples</dt><dd>${c.missing.length}</dd></dl><button type="button" class="text-action" data-open-f="${c.pid}|${c.peakMinute}" data-fk="cmpopen-${c.pid}">Open ${c.pid} in F ↗</button></div>`;
  return `<section class="card compare-card"><span class="node-id">COMPARISON</span><h2>${a.pid} beside ${b.pid}</h2><div class="cmp-grid">${col(a)}${col(b)}</div><p class="cmp-note">Two cases do not establish a difference between groups. Each value opens its own source row in F.</p></section>`;
}

function renderBoard() {
  const turn = state.turns[state.active];
  const board = $('#board');
  if (!turn) {
    board.innerHTML = `<section class="card empty-board"><span class="node-id">EVIDENCE BOARD</span><h2>Start with a question.</h2><p>Answers appear here as claim cards with a link to a case and minute, followed by a case shortlist, a comparison, and the limits of the evidence. With no answer yet, use Cases overview to browse the ${COUNTS.analyzed} analyzed cases.</p></section>`;
    return;
  }
  const r = turn.r;
  state.sparkKey = r.spark;
  let claimsHtml;
  if (turn.noAi) claimsHtml = `<div class="notice off" role="status"><b>No AI answer.</b> The local model is unavailable (simulated). No claims were generated. ${r.cands.length ? 'Search results below still come from the fictional records.' : 'Keyword search found no matching cases.'}</div>`;
  else if (r.kind === 'blocked' || r.kind === 'unsupported') claimsHtml = `<div class="notice" role="status"><b>${r.kind === 'blocked' ? 'Not answered.' : 'Question not recognized.'}</b> ${esc(r.summary)}</div>`;
  else if (r.kind === 'empty') claimsHtml = `<div class="notice" role="status"><b>No matching cases.</b> ${esc(r.summary)}</div>`;
  else claimsHtml = `<div class="claims">${r.claims.length ? r.claims.map((claim, i) => `<article class="claim"><span class="claim-n">${i + 1}</span><p>${esc(claim.text)}</p><p class="claim-src">Source: ${esc(claim.source)}</p><div class="claim-actions"><button type="button" class="text-action" data-open-f="${claim.pid}|${claim.minute}" data-fk="claim-${i}">Open ${claim.pid} at minute ${claim.minute} in F ↗</button>${claim.rule ? `<button type="button" class="text-action" data-rule="${claim.rule}" data-fk="claimrule-${i}">Rule ${claim.rule}</button>` : ''}</div></article>`).join('') : '<p class="empty">No analyzed case supports a claim for this question. See the candidate list for indexed matches.</p>'}</div>`;
  const cands = r.cands.length ? `<h3>${esc(r.listLabel)}</h3><div class="cands">${r.cands.map(cand => candidateCard(cand, 'cand')).join('')}</div>` : '';
  board.innerHTML = `<div class="board-head"><span class="node-id">EVIDENCE FOR</span><h2>“${esc(turn.q)}”</h2>${turn.noAi ? '<span class="chip off">No AI answer</span>' : '<span class="chip demo">Demo — scripted response</span>'}</div>${claimsHtml}${cands}${comparisonCard()}<section class="card limit-card"><span class="node-id">WHAT THIS DOES NOT ESTABLISH</span><p>${esc(r.limit || RESEARCH_NOTE)}</p></section>`;
}

function renderView() {
  const cases = state.view === 'cases';
  $('#view-board').setAttribute('aria-pressed', String(!cases));
  $('#view-cases').setAttribute('aria-pressed', String(cases));
  $('#board').hidden = cases;
  $('#cases').hidden = !cases;
}

const SORTERS = {
  pid: c => c.pid, proc: c => c.proc, age: c => c.age, dur: c => c.dur, flagMin: c => c.minuteFlags.length,
  episodes: c => c.episodes.length, peak: c => c.peak, missing: c => c.missing.length, score: c => c.score
};
const COLUMNS = [['pid', 'PID'], ['proc', 'Procedure'], ['age', 'Age'], ['dur', 'Minutes'], ['flagMin', 'Flag minutes'], ['episodes', 'Episodes'], ['peak', 'Peak PIP'], ['missing', 'Missing'], ['score', 'Score']];

function renderCases() {
  const { key, dir } = state.sort;
  const rows = [...CASES].sort((a, b) => { const x = SORTERS[key](a), y = SORTERS[key](b); return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'asc' ? 1 : -1) || a.pid.localeCompare(b.pid); });
  const maxScore = Math.max(...CASES.map(c => c.score), 1);
  const byScore = [...CASES].sort((a, b) => b.score - a.score);
  const ruleCounts = RULES.filter(rule => rule.signal).map(rule => ({ rule, warn: CASES.reduce((s, c) => s + c.minuteFlags.filter(f => f.rule === rule.id && f.sev === 'warn').length, 0), crit: CASES.reduce((s, c) => s + c.minuteFlags.filter(f => f.rule === rule.id && f.sev === 'critical').length, 0) }));
  const maxRule = Math.max(...ruleCounts.map(item => item.warn + item.crit), 1);
  const badge = c => `${state.selected === c.pid ? '<span class="chip sel">Selected</span>' : ''}${state.compare === c.pid ? '<span class="chip cmp">Comparison</span>' : ''}`;
  $('#cases').innerHTML = `
    <section class="scope" aria-label="Data scope"><div><b>${COUNTS.indexed}</b><span>Indexed surgeries</span></div><div class="focus"><b>${COUNTS.analyzed}</b><span>Analyzed cases in focus</span></div><div><b>${COUNTS.notAnalyzed}</b><span>Indexed, not analyzed</span></div><div><b>${COUNTS.novent}</b><span>No ventilator data</span></div><p>The charts and table describe only the ${COUNTS.analyzed} analyzed cases, not all ${COUNTS.indexed} indexed surgeries.</p></section>
    <div class="charts">
      <section class="card" aria-labelledby="chart1"><h2 id="chart1">Top cases by anomaly score</h2><p class="chart-note">Score = warn minutes + 3 × critical minutes. Each bar opens that case in F.</p><div class="bars">${byScore.map(c => `<button type="button" class="bar-row" data-open-case="${c.pid}|${c.focusMinute}" data-fk="bar-${c.pid}"><span class="bar-label"><b class="mono">${c.pid}</b> ${esc(short(c.proc, 30))}</span><span class="bar-track"><span class="bar-fill" style="width:${Math.max(c.score / maxScore * 100, c.score ? 2 : 0)}%"></span></span><span class="bar-val">${c.score}</span></button>`).join('')}</div></section>
      <section class="card" aria-labelledby="chart2"><h2 id="chart2">Flag minutes by rule</h2><p class="chart-note">Warn and critical minutes across the ${COUNTS.analyzed} cases. Each row opens the rule.</p><div class="bars">${ruleCounts.map(item => `<button type="button" class="bar-row" data-rule="${item.rule.id}" data-fk="rulebar-${item.rule.id}"><span class="bar-label"><b class="mono">${item.rule.id}</b></span><span class="bar-track"><span class="bar-fill warn" style="width:${item.warn / maxRule * 100}%"></span><span class="bar-fill crit" style="width:${item.crit / maxRule * 100}%"></span></span><span class="bar-val">${item.warn}w ${item.crit}c</span></button>`).join('')}</div><p class="chart-note">Only ${ruleCounts.length} of ${RULES.length} rules can be evaluated from PIP, ETCO2 and heart rate.</p></section>
    </div>
    <section class="card" aria-labelledby="tbl-title"><h2 id="tbl-title">Cases in focus (${CASES.length})</h2><p class="chart-note">Select a case name or row to select it and open it in F. Sort with the column buttons.</p>
      <div class="table-scroll" tabindex="0" role="region" aria-label="Cases in focus table"><table class="cases-table"><thead><tr>${COLUMNS.map(([k, label]) => `<th scope="col" aria-sort="${k === key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" data-sort="${k}" data-fk="sort-${k}">${label}${k === key ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>`).join('')}</tr></thead><tbody>${rows.map(c => `<tr data-row="${c.pid}|${c.focusMinute}" class="${state.selected === c.pid ? 'is-selected' : ''}"><th scope="row"><button type="button" class="row-open" data-open-case="${c.pid}|${c.focusMinute}" data-fk="row-${c.pid}">${c.pid}</button>${badge(c)}</th><td>${esc(c.proc)}</td><td>${c.age}</td><td>${c.dur}</td><td>${c.minuteFlags.length}</td><td>${c.episodes.length}</td><td>${c.peak}</td><td>${c.missing.length}</td><td>${c.score}</td></tr>`).join('')}</tbody></table></div></section>
    <details class="card indexed"><summary>Indexed surgeries not analyzed (${COUNTS.notAnalyzed + COUNTS.novent})</summary><p class="chart-note">These are counted in the indexed total but have no analyzed signals here, so they cannot open F.</p><ul>${INDEXED.filter(c => c.status !== 'analyzed').map(c => `<li><b class="mono">${c.pid}</b><span>${esc(c.proc)}, age ${c.age}</span><span class="chip ${c.status}">${STATUS_LABEL[c.status]}</span></li>`).join('')}</ul></details>`;
}

function renderG() {
  const activeKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fk : null;
  renderModel(); renderPlacement(); renderThread(); renderWorkingSet(); renderBoard(); renderCases(); renderView();
  if (activeKey) { const again = document.querySelector(`[data-fk="${activeKey}"]`); if (again) again.focus(); }
}

// ---- G: rule drawer ----
function renderDrawer() {
  const drawer = state.drawer;
  $('#rules-drawer').hidden = !drawer.open;
  $('#rules-open').setAttribute('aria-expanded', String(drawer.open));
  $('#rules-version').textContent = `Threshold set: ${THRESHOLD_VERSION}. Ids, descriptions and defaults are copied from src/config/thresholds.yaml.`;
  const q = drawer.query.trim().toLowerCase();
  const matches = RULES.filter(rule => !q || `${rule.id} ${rule.desc} ${rule.signal ? SIGNAL[rule.signal].label : ''} ${rule.unit || ''} ${rule.params || ''}`.toLowerCase().includes(q));
  $('#rules-count').textContent = `${matches.length} of ${RULES.length} rules`;
  $('#rules-list').innerHTML = matches.length ? matches.map(rule => {
    const n = rule.signal ? CASES.reduce((s, c) => s + c.minuteFlags.filter(f => f.rule === rule.id).length, 0) : null;
    return `<article class="rule${drawer.rule === rule.id ? ' focus' : ''}" id="rule-${rule.id}"><h3 class="mono">${rule.id}</h3><p>${esc(rule.desc)}</p><p class="rule-thr">${esc(thresholdText(rule))}</p><p class="rule-use">${rule.signal ? `In this extract: ${plural(n, 'flagged minute')} across ${COUNTS.analyzed} cases.` : 'Not evaluated in this extract: it needs signals other than PIP, ETCO2 and heart rate.'}</p></article>`;
  }).join('') : '<p class="empty">No rule matches this search. Clear the search to see all rules.</p>';
}
function openDrawer(ruleId = null) {
  state.drawer.open = true;
  if (ruleId) { state.drawer.rule = ruleId; state.drawer.query = ''; $('#rules-search').value = ''; }
  renderDrawer();
  if (ruleId) $(`#rule-${ruleId}`).scrollIntoView({ block: 'center' });
  $('#rules-search').focus();
}
function closeDrawer() {
  state.drawer.open = false; state.drawer.rule = null;
  renderDrawer();
  $('#rules-open').focus();
}

// ---- G: actions ----
function ask(question) {
  const q = question.trim();
  const r = retrieve(q);
  const noAi = !state.modelOn;
  const text = noAi ? NO_AI_MESSAGE + (r.cands.length ? '' : ' Keyword search found no matching cases.') : r.summary;
  state.turns.push({ q, r, noAi, text, claims: noAi || r.kind !== 'ok' ? [] : r.claims });
  state.active = state.turns.length - 1;
  state.question = q;
  state.view = 'board';
  renderG();
  announce(noAi ? 'No AI answer: local model unavailable. Search results are shown.' : 'Answer ready on the evidence board.');
  if (innerWidth <= 900) $('#board').scrollIntoView({ block: 'start' });
}
const setSelected = pid => { state.selected = pid; if (state.compare === pid) state.compare = null; };

function openF(pid, minute, fk) {
  state.gScroll = scrollY;
  state.returnKey = fk || null;
  state.f = { pid, minute, sel: null, overlay: false, answer: null };
  $('#g-view').hidden = true;
  $('#f-view').hidden = false;
  document.body.classList.add('in-f');
  renderFStatic(); renderFCursor(); renderFRule(); renderFModel();
  window.scrollTo(0, 0);
  $('#f-heading').focus();
}
function closeF() {
  $('#f-view').hidden = true;
  $('#g-view').hidden = false;
  document.body.classList.remove('in-f');
  window.scrollTo(0, state.gScroll);
  const target = state.returnKey && document.querySelector(`[data-fk="${state.returnKey}"]`);
  (target || $('#ask-input')).focus({ preventScroll: true });
  state.f = null;
  announce('Back in the evidence workspace. Your question, conversation, selection and comparison are unchanged.');
}

// ---- F: rendering ----
const otherCase = () => { const f = state.f; return [state.compare, state.selected].map(pid => CASE[pid]).find(c => c && c.pid !== f.pid) || null; };
const trackLines = signal => RULES.filter(rule => rule.signal === signal.key && rule.warn !== undefined).flatMap(rule => [['warn', rule.warn, rule.id], ['critical', rule.crit, rule.id]]);
const bandLeft = (minute, n) => `${(minute / n * 100).toFixed(3)}%`;

function renderFStatic() {
  const f = state.f, c = CASE[f.pid], other = f.overlay ? otherCase() : null;
  const n = Math.max(c.dur, other ? other.dur : 0);
  f.n = n;
  $('#f-heading').textContent = `${c.pid} · ${c.proc}`;
  $('#f-sub').textContent = `Age ${c.age} · ${c.dur} one-minute samples · ${THRESHOLD_VERSION}`;
  $('#f-context').textContent = `From G · ${state.turns[state.active] && state.view === 'board' ? `“${short(state.turns[state.active].q, 48)}”` : 'Cases overview'}`;
  $('#f-case-label').textContent = `${c.pid} / ${c.proc}`;
  $('#f-case').innerHTML = CASES.map(item => `<option value="${item.pid}"${item.pid === c.pid ? ' selected' : ''}>${item.pid} · ${esc(short(item.proc, 34))}</option>`).join('');
  const o = otherCase();
  $('#f-overlay').hidden = !o;
  if (o) { $('#f-overlay').textContent = `${f.overlay ? 'Remove overlay of' : 'Overlay'} ${o.pid} (dashed)`; $('#f-overlay').setAttribute('aria-pressed', String(f.overlay)); }
  const scrub = $('#f-scrub');
  scrub.max = c.dur - 1;
  $('#f-ruler').innerHTML = [0, 1, 2, 3, 4].map(k => `<span>${Math.round((n - 1) * k / 4)}</span>`).join('');
  const H = 104, pad = 8;
  const xOf = i => (i + 0.5) / n * 1000;
  const tracks = SIGNALS.map(signal => {
    const yOf = yOfSignal(signal, H, pad);
    const bands = c[signal.key].map((value, i) => (value === null ? `<rect class="gap" x="${(i / n * 1000).toFixed(2)}" y="0" width="${(1000 / n).toFixed(2)}" height="${H}"/>` : '')).join('');
    const epBands = c.episodes.filter(ep => ep.signal === signal.key).map(ep => `<rect class="ep-band ${ep.sev}" x="${(ep.start / n * 1000).toFixed(2)}" y="0" width="${((ep.end - ep.start + 1) / n * 1000).toFixed(2)}" height="${H}"/>`).join('');
    const lines = trackLines(signal).map(([sev, value]) => `<line class="thr ${sev}" x1="0" x2="1000" y1="${yOf(value).toFixed(1)}" y2="${yOf(value).toFixed(1)}"/>`).join('');
    const labels = trackLines(signal).map(([sev, value]) => `<span class="thr-label ${sev}" style="top:${(yOf(value) / H * 100).toFixed(1)}%">${value}</span>`).join('');
    const overlay = other ? drawSegments(segments(other[signal.key], xOf, yOf), `stroke="${signal.color}" stroke-width="2" stroke-dasharray="6 5" opacity=".6" vector-effect="non-scaling-stroke"`) : '';
    const main = drawSegments(segments(c[signal.key], xOf, yOf), `stroke="${signal.color}" stroke-width="2.4" vector-effect="non-scaling-stroke"`);
    const missing = c[signal.key].filter(v => v === null).length;
    return `<div class="track" style="--signal:${signal.color}"><div class="track-metric"><span>${signal.label}</span><strong data-val="${signal.key}"></strong><small>${signal.unit}${missing ? ` · ${plural(missing, 'gap')}` : ''}</small></div><div class="track-plot" data-plot><svg viewBox="0 0 1000 ${H}" preserveAspectRatio="none" role="img" aria-label="${signal.label} for ${c.pid}, ${plural(missing, 'missing sample')}. Exact values are in the source row.">${bands}${epBands}${lines}${overlay}${main}</svg>${labels}<i class="playhead"></i></div></div>`;
  }).join('');
  const rules = [...new Set(c.episodes.map(ep => ep.rule))];
  const lane = `<div class="track lane"><div class="track-metric"><span>FLAG EPISODES</span><small>${rules.length ? 'Select a marker for its rule' : 'No episodes in this case'}</small></div><div class="lane-plot" data-plot>${rules.map(rule => `<div class="lane-row"><span class="lane-name mono">${rule}</span>${c.episodes.map((ep, i) => (ep.rule === rule ? `<button type="button" class="ep-mark ${ep.sev}" style="left:${bandLeft(ep.start, n)};width:${((ep.end - ep.start + 1) / n * 100).toFixed(3)}%" data-ep="${i}" aria-label="${rule} ${ep.sev} episode, minutes ${ep.start} to ${ep.end}"></button>` : '')).join('')}</div>`).join('') || '<div class="lane-row"><span class="lane-name">none</span></div>'}<i class="playhead"></i></div></div>`;
  $('#f-tracks').innerHTML = tracks + lane;
  $('#f-episodes').innerHTML = c.episodes.length ? `<div class="table-scroll"><table class="mini"><thead><tr><th scope="col">Rule</th><th scope="col">Severity</th><th scope="col">Minutes</th><th scope="col">Extreme</th><th scope="col"><span class="visually-hidden">Action</span></th></tr></thead><tbody>${c.episodes.map((ep, i) => `<tr><th scope="row" class="mono">${ep.rule}</th><td><span class="chip ${ep.sev}">${sevLabel(ep.sev)}</span></td><td>${ep.start}–${ep.end} (${ep.end - ep.start + 1})</td><td>${ep.extreme} ${RULE[ep.rule].unit || ''}</td><td><button type="button" class="text-action" data-ep="${i}">Show, min ${ep.extremeMinute}</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No episodes: no recorded value crosses a threshold in this case.</p>';
  $('#f-flags-summary').textContent = `Minute flags (${c.minuteFlags.length})`;
  $('#f-flags').innerHTML = c.minuteFlags.length ? `<div class="table-scroll short"><table class="mini"><thead><tr><th scope="col">Minute</th><th scope="col">Rule</th><th scope="col">Severity</th><th scope="col">Value</th></tr></thead><tbody>${c.minuteFlags.map(flag => `<tr><th scope="row"><button type="button" class="text-action" data-goto="${flag.minute}">${flag.minute}</button></th><td class="mono">${flag.rule}</td><td>${sevLabel(flag.sev)}</td><td>${flag.rule === 'pip_rising' ? `PIP ${flag.value}, slope +${flag.slope.toFixed(1)}/min` : flag.value}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No minute flags.</p>';
  $$('.ep-mark').forEach(mark => mark.classList.toggle('selected', !!f.sel && f.sel.index === Number(mark.dataset.ep)));
}

function renderFCursor() {
  const f = state.f, c = CASE[f.pid], m = f.minute;
  $('#f-minute').textContent = m;
  $('#f-minute-of').textContent = `of ${c.dur - 1} · minutes from start of recording`;
  $('#f-scrub').value = m;
  $('#f-scrub').setAttribute('aria-valuetext', `Minute ${m}`);
  $$('.playhead').forEach(head => { head.style.left = `${((m + 0.5) / f.n * 100).toFixed(3)}%`; });
  SIGNALS.forEach(signal => { const v = c[signal.key][m]; const el = $(`[data-val="${signal.key}"]`); el.textContent = show(v); el.classList.toggle('missing', v === null); });
  const flags = flagsAt(c, m);
  $('#f-source').innerHTML = `<table class="src"><caption class="visually-hidden">Source row for ${c.pid}, minute ${m}</caption><thead><tr><th scope="col">Signal</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">State</th></tr></thead><tbody>${SIGNALS.map(signal => { const v = c[signal.key][m]; return `<tr class="${v === null ? 'is-missing' : ''}"><th scope="row">${signal.label}</th><td>${show(v)}</td><td>${signal.unit}</td><td>${v === null ? 'Missing sample: no value recorded' : 'Recorded'}</td></tr>`; }).join('')}</tbody></table><p class="src-flags"><span>Flags at this minute</span>${flags.length ? flags.map(flag => `<button type="button" class="chip ${flag.sev} chip-button" data-flag="${flag.rule}">${flag.rule} · ${sevLabel(flag.sev)}</button>`).join('') : '<em>none</em>'}</p>${c.missing.some(item => item.minute === m) ? '<p class="src-note">A missing value cannot be compared with a threshold, so no flag is raised for it. It is not filled or set to zero.</p>' : ''}`;
  $('#f-question-label').textContent = `Ask about ${c.pid} at minute ${m}`;
}

function renderFRule() {
  const f = state.f, c = CASE[f.pid], box = $('#f-rule');
  if (!f.sel) { box.innerHTML = '<p class="empty">Select an episode marker, a flag chip in the source row, or a flag in the table to see its rule, the values used, and the threshold version.</p>'; return; }
  const ep = c.episodes[f.sel.index], rule = RULE[ep.rule], sig = SIGNAL[ep.signal];
  const rows = ep.flags.length > 12 ? [...ep.flags.slice(0, 6), null, ...ep.flags.slice(-5)] : ep.flags;
  const ownGaps = c.missing.filter(item => item.signal === ep.signal);
  const cell = flag => (flag ? `<tr class="${flag.minute === f.minute ? 'current' : ''}"><th scope="row">${flag.minute}</th><td>${flag.value}</td><td>${flag.rule === 'pip_rising' ? `${c.pip[flag.minute - 10]} → slope +${flag.slope.toFixed(1)}/min` : sevLabel(flag.sev)}</td></tr>` : '<tr><th scope="row">…</th><td>…</td><td>…</td></tr>');
  box.innerHTML = `<h3 class="mono">${rule.id} <span class="chip ${ep.sev}">${sevLabel(ep.sev)}</span></h3><p>${esc(rule.desc)}</p><dl class="rule-dl"><dt>Threshold</dt><dd>${esc(thresholdText(rule))}</dd><dt>Threshold version</dt><dd>${THRESHOLD_VERSION}</dd><dt>Signal and unit</dt><dd>${sig.label}, ${sig.unit}</dd><dt>Episode</dt><dd>Minutes ${ep.start} to ${ep.end} (${plural(ep.end - ep.start + 1, 'minute')}); extreme ${ep.extreme} ${rule.unit || ''} at minute ${ep.extremeMinute}</dd><dt>Missingness</dt><dd>${ownGaps.length ? `${plural(ownGaps.length, `missing ${sig.label} sample`)} elsewhere in this case (${gapRanges(c, ep.signal).map(r => r.from === r.to ? r.from : `${r.from}–${r.to}`).join(', ')}); none inside this episode, because a missing sample ends an episode.` : `No missing ${sig.label} samples in this case.`}</dd></dl><table class="mini"><caption>Values used</caption><thead><tr><th scope="col">Minute</th><th scope="col">${sig.label}</th><th scope="col">${ep.rule === 'pip_rising' ? 'PIP 10 min earlier' : 'Severity'}</th></tr></thead><tbody>${rows.map(cell).join('')}</tbody></table><p class="src-note">${RESEARCH_NOTE}</p>`;
}

function renderFModel() {
  const answer = $('#f-answer'), f = state.f;
  if (!f) return;
  if (!f.answer) {
    answer.innerHTML = state.modelOn ? '<p><span class="chip demo">Demo — scripted response</span></p><p>Ask about the selected case and minute, for example “explain this minute”, “where is the peak”, or “any missing samples”.</p>' : `<p><span class="chip off">No AI answer</span></p><p>${NO_AI_MESSAGE.replace(' Keyword search results and source data are still shown.', ' Source values, flags and rule details on this page still work.')}</p>`;
  } else answer.innerHTML = `<p><span class="chip ${f.answer.noAi ? 'off' : 'demo'}">${f.answer.noAi ? 'No AI answer' : 'Demo — scripted response'}</span></p><p>${esc(f.answer.text)}</p>`;
}

function setMinute(minute, announceText) {
  const f = state.f;
  f.minute = Math.min(Math.max(minute, 0), CASE[f.pid].dur - 1);
  renderFCursor();
  if (announceText) announce(announceText);
}
function selectEpisode(index, minute) {
  const f = state.f, ep = CASE[f.pid].episodes[index];
  f.sel = { index };
  renderFStatic(); // refresh selected marker class
  setMinute(minute === undefined ? ep.extremeMinute : minute);
  renderFRule();
  announce(`${ep.rule} ${ep.sev} episode, minutes ${ep.start} to ${ep.end}. Rule detail updated.`);
  if (innerWidth <= 900) $('#rule-panel').scrollIntoView({ block: 'nearest' });
}

function fReply(q) {
  const f = state.f, c = CASE[f.pid], m = f.minute, t = q.toLowerCase();
  const kind = classify(q);
  if (!state.modelOn) return { noAi: true, text: NO_AI_MESSAGE.replace(' Keyword search results and source data are still shown.', ' Source values, flags and rule details on this page still work.') };
  if (kind === 'blocked') return { text: BLOCKED_MESSAGE };
  if (/missing|gap|coverage/.test(t)) {
    const next = c.missing.find(item => item.minute > m) || c.missing[0];
    if (!next) return { text: `${c.pid} has no missing samples among PIP, ETCO2 and heart rate.` };
    return { minute: next.minute, text: `${SIGNAL[next.signal].label} is missing at minute ${next.minute}. The selected minute moved there. The gap is not filled or set to zero. This case has ${plural(c.missing.length, 'missing sample')} in total.` };
  }
  if (/peak|highest|maximum|pressure|pip/.test(t)) return { minute: c.peakMinute, text: `The highest recorded PIP in ${c.pid} is ${c.peak} cmH2O at minute ${c.peakMinute}. The selected minute moved there. This describes the recorded value only.` };
  if (/flag|rule|episode|threshold|explain|minute|value|here|this|what/.test(t)) {
    const flags = flagsAt(c, m);
    return { text: `At minute ${m} of ${c.pid}: ${SIGNALS.map(s => `${s.label} ${c[s.key][m] === null ? 'missing' : `${c[s.key][m]} ${s.unit}`}`).join(', ')}. ${flags.length ? `Flags at this minute: ${flags.map(fl => `${fl.rule} (${fl.sev})`).join(', ')}; the rule detail shows the values used.` : 'No rule flag is raised at this minute.'} The source row is shown beside the tracks.` };
  }
  return { text: UNSUPPORTED_MESSAGE };
}

// ---- Events ----
document.addEventListener('click', event => {
  const t = event.target;
  const btn = selector => t.closest(selector);
  let el;
  if ((el = btn('[data-example]'))) { $('#ask-input').value = el.dataset.example; $('#ask-error').hidden = true; ask(el.dataset.example); return; }
  if ((el = btn('[data-open-f]'))) { const [pid, minute] = el.dataset.openF.split('|'); openF(pid, Number(minute), el.dataset.fk); return; }
  if ((el = btn('[data-open-case]'))) { const [pid, minute] = el.dataset.openCase.split('|'); setSelected(pid); renderG(); openF(pid, Number(minute), el.dataset.fk); return; }
  if ((el = btn('tr[data-row]'))) { const [pid, minute] = el.dataset.row.split('|'); setSelected(pid); renderG(); openF(pid, Number(minute), `row-${pid}`); return; }
  if ((el = btn('[data-select]'))) { const pid = el.dataset.select; if (state.selected === pid) state.selected = null; else setSelected(pid); renderG(); return; }
  if ((el = btn('[data-compare]'))) { const pid = el.dataset.compare; state.compare = state.compare === pid ? null : pid; renderG(); return; }
  if ((el = btn('[data-clear]'))) { state[el.dataset.clear] = null; renderG(); return; }
  if ((el = btn('[data-show-turn]'))) { state.active = Number(el.dataset.showTurn); state.view = 'board'; renderG(); return; }
  if ((el = btn('[data-rerun]'))) { ask(state.turns[Number(el.dataset.rerun)].q); return; }
  if ((el = btn('[data-noopen]'))) { const note = document.querySelector(`[data-status="${el.dataset.noopen}"]`); note.classList.add('flash'); announce(note.textContent); return; }
  if ((el = btn('[data-rule]'))) { openDrawer(el.dataset.rule); return; }
  if ((el = btn('[data-sort]'))) { const key = el.dataset.sort; state.sort = { key, dir: state.sort.key === key && state.sort.dir === 'desc' ? 'asc' : (key === 'pid' || key === 'proc' ? 'asc' : 'desc') }; renderG(); announce(`Sorted by ${key}.`); return; }
  if (state.f && (el = btn('[data-ep]'))) { selectEpisode(Number(el.dataset.ep)); return; }
  if (state.f && (el = btn('[data-flag]'))) { const c = CASE[state.f.pid]; const index = c.episodes.findIndex(ep => ep.rule === el.dataset.flag && state.f.minute >= ep.start && state.f.minute <= ep.end); if (index >= 0) selectEpisode(index, state.f.minute); return; }
  if (state.f && (el = btn('[data-goto]'))) { setMinute(Number(el.dataset.goto), `Minute ${el.dataset.goto}`); $('#f-tracks').scrollIntoView({ block: 'nearest' }); return; }
  if (state.f && (el = btn('[data-plot]'))) {
    const rect = el.getBoundingClientRect();
    if (!btn('.ep-mark')) setMinute(Math.floor((event.clientX - rect.left) / rect.width * state.f.n));
  }
});

$('#ask-form').addEventListener('submit', event => {
  event.preventDefault();
  const q = $('#ask-input').value.trim();
  $('#ask-error').hidden = !!q;
  if (q) ask(q);
});
$('#view-board').addEventListener('click', () => { state.view = 'board'; renderView(); });
$('#view-cases').addEventListener('click', () => { state.view = 'cases'; renderView(); announce(`Cases overview: ${COUNTS.analyzed} analyzed cases of ${COUNTS.indexed} indexed surgeries.`); });
$('#place-side').addEventListener('click', () => { state.place = 'side'; renderPlacement(); });
$('#place-inline').addEventListener('click', () => { state.place = 'inline'; renderPlacement(); });
const toggleModel = () => { state.modelOn = !state.modelOn; renderModel(); announce(`Local model ${state.modelOn ? 'available' : 'unavailable'} (simulated).`); };
$('#model-switch').addEventListener('click', toggleModel);
$('#setup-model-switch').addEventListener('click', toggleModel);
$('#rules-open').addEventListener('click', () => (state.drawer.open ? closeDrawer() : openDrawer()));
$('#rules-close').addEventListener('click', closeDrawer);
$('#rules-search').addEventListener('input', event => { state.drawer.query = event.target.value; state.drawer.rule = null; renderDrawer(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && state.drawer.open && !$('#g-view').hidden) closeDrawer(); });

$('#f-back').addEventListener('click', closeF);
$('#f-scrub').addEventListener('input', event => setMinute(Number(event.target.value)));
$('#f-case').addEventListener('change', event => { const f = state.f; f.pid = event.target.value; f.sel = null; f.overlay = false; f.minute = Math.min(f.minute, CASE[f.pid].dur - 1); renderFStatic(); renderFCursor(); renderFRule(); announce(`Showing ${f.pid}.`); });
$('#f-overlay').addEventListener('click', () => { state.f.overlay = !state.f.overlay; renderFStatic(); renderFCursor(); });
$('#jump-peak').addEventListener('click', () => setMinute(CASE[state.f.pid].peakMinute, `Peak PIP at minute ${CASE[state.f.pid].peakMinute}`));
$('#jump-missing').addEventListener('click', () => {
  const c = CASE[state.f.pid], next = c.missing.find(item => item.minute > state.f.minute) || c.missing[0];
  if (!next) { announce(`${c.pid} has no missing samples.`); $('#f-answer').innerHTML = `<p><span class="chip demo">Demo — scripted response</span></p><p>${c.pid} has no missing samples among PIP, ETCO2 and heart rate.</p>`; return; }
  setMinute(next.minute, `${SIGNAL[next.signal].label} missing at minute ${next.minute}`);
});
$('#jump-flag').addEventListener('click', () => {
  const c = CASE[state.f.pid], next = c.minuteFlags.find(flag => flag.minute > state.f.minute) || c.minuteFlags[0];
  if (!next) { announce(`${c.pid} has no flags.`); return; }
  setMinute(next.minute, `Flag at minute ${next.minute}`);
});
$('#src-all').addEventListener('click', () => {
  const c = CASE[state.f.pid];
  $('#source-title').textContent = `${c.pid} / ${c.proc}`;
  $('#source-rows').innerHTML = c.pip.map((_, minute) => `<tr class="${minute === state.f.minute ? 'current' : ''}"><th scope="row">${minute}</th><td>${show(c.pip[minute])}</td><td>${show(c.co2[minute])}</td><td>${show(c.hr[minute])}</td><td>${flagsAt(c, minute).map(flag => flag.rule).join(', ')}</td></tr>`).join('');
  $('#source-dialog').showModal();
  const row = $('#source-rows tr.current'); if (row) row.scrollIntoView({ block: 'center' });
});
$('#source-close').addEventListener('click', () => $('#source-dialog').close());
$('#f-form').addEventListener('submit', event => {
  event.preventDefault();
  const q = $('#f-question').value.trim();
  $('#f-error').hidden = !!q;
  if (!q) return;
  const f = state.f, ctx = `F · ${f.pid} · minute ${f.minute}`;
  const reply = fReply(q);
  f.answer = reply;
  state.turns.push({ q, r: { kind: 'f' }, noAi: !!reply.noAi, text: reply.text, claims: reply.minute === undefined ? [] : [{ pid: f.pid, minute: reply.minute }], ctx });
  renderThread();
  if (reply.minute !== undefined) setMinute(reply.minute);
  renderFModel();
  $('#f-question').value = '';
  announce(reply.noAi ? 'No AI answer: local model unavailable.' : 'Scripted answer ready.');
});

// ---- Setup ----
const setupDialog = $('#setup-dialog');
function finishSetup() {
  state.ready = true;
  setupDialog.close();
  $('#welcome').hidden = true;
  if (!state.f) $('#g-view').hidden = false;
  renderG(); renderDrawer();
  $('#ask-input').focus();
}
function cancelSetup() {
  setupDialog.close();
  if (!state.ready) { $('#welcome').hidden = false; $('#g-view').hidden = true; $('#welcome-setup').focus(); }
}
const openSetup = () => { renderModel(); setupDialog.showModal(); $('#setup-ok').focus(); };
$('#setup-ok').addEventListener('click', finishSetup);
$('#setup-cancel').addEventListener('click', cancelSetup);
setupDialog.addEventListener('cancel', event => { event.preventDefault(); cancelSetup(); });
$('#setup-open').addEventListener('click', openSetup);
$('#welcome-setup').addEventListener('click', openSetup);

renderModel();
openSetup();
