const samples = Array.from({ length: 13 }, (_, index) => index * 10 - 10);
const records = [
  { id: 'SYN-041', short: 'Laparoscopic', title: 'Laparoscopic surgery', pip: [18, 19, 19, 20, 20, 24, 31, 35, 29, 23, 21, 20, 19], co2: [34, 35, 35, null, 36, 38, 41, 42, 40, 37, 36, 35, 35], hr: [74, 73, 75, 75, 76, 78, 83, 87, 82, 78, 76, 74, 73] },
  { id: 'SYN-108', short: 'Knee', title: 'Total knee arthroplasty', pip: [17, 17, 18, 18, 19, 21, 23, 22, 21, 20, 19, 18, 18], co2: [33, 34, 34, 35, 35, null, 37, 36, 36, 35, 34, 34, 34], hr: [68, 69, 70, 71, 70, 73, 75, 76, 73, 72, 70, 69, 69] },
  { id: 'SYN-223', short: 'Thyroid', title: 'Thyroid procedure', pip: [16, 17, 16, 17, 17, 18, 18, 17, 17, 18, 17, 17, 16], co2: [34, 34, 35, 34, 35, 35, 36, 35, 35, 35, 34, 35, 34], hr: [72, 71, 73, 72, 72, 74, 74, 73, 73, 74, 72, 73, 72] }
];
const signals = [
  { key: 'pip', label: 'PIP', unit: 'cmH₂O', color: '#f0b17b', min: 12, max: 40 },
  { key: 'co2', label: 'ETCO₂', unit: 'mmHg', color: '#8bd4c0', min: 28, max: 47 },
  { key: 'hr', label: 'HEART RATE', unit: 'bpm', color: '#b6afe9', min: 60, max: 95 }
];
const studio = { record: records[0], index: 7, compare: false };
const canvas = { mode: 'peak', comparison: false };
const deck = { queue: [...records], index: 0, mode: 'peak', decisions: new Map() };
let sourceReturn = null;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const peakIndex = record => record.pip.indexOf(Math.max(...record.pip));
const minuteLabel = index => `${samples[index] > 0 ? '+' : ''}${samples[index]} min`;
const missingSamples = record => signals.flatMap(signal => record[signal.key].flatMap((value, index) => value === null ? [{ signal, index }] : []));
const revealOnSmallScreen = selector => { if (innerWidth <= 760) $(selector).scrollIntoView({ block: 'start' }); };

function linePaths(values, signal, width, height, padding = 9) {
  const segments = [];
  let segment = [];
  values.forEach((value, index) => {
    if (value === null) { if (segment.length) segments.push(segment); segment = []; return; }
    const x = padding + index / (values.length - 1) * (width - 2 * padding);
    const y = height - padding - (value - signal.min) / (signal.max - signal.min) * (height - 2 * padding);
    segment.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (segment.length) segments.push(segment);
  return segments.map(points => points.join(' '));
}

function sparkline(record, signal = signals[0], color = '#9b5839') {
  const paths = linePaths(record[signal.key], signal, 360, 90);
  return `<svg viewBox="0 0 360 90" preserveAspectRatio="none" role="img" aria-label="Fictional ${signal.label} trend for ${record.id}. Exact values are available in the source table."><path d="M0 75H360" stroke="currentColor" opacity=".15"/>${paths.map(points => `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.8" stroke-linejoin="round"/>`).join('')}</svg>`;
}

function showStudy(id) {
  $$('.study').forEach(section => { section.hidden = section.id !== id; });
  $$('[data-study]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.study === id)));
  history.replaceState(null, '', `#${id}`);
  window.scrollTo(0, 0);
}

function renderStudio() {
  const record = studio.record;
  $('#studio-cases').innerHTML = records.map(item => `<button data-studio-case="${item.id}" aria-pressed="${item.id === record.id}">${item.short}</button>`).join('');
  $('#studio-case-label').textContent = `${record.id} / ${record.title}`;
  $('#studio-minute').textContent = samples[studio.index];
  $('#studio-scrub').value = studio.index;
  $('#studio-scrub').setAttribute('aria-valuetext', `${samples[studio.index]} minutes relative to incision`);
  $('#studio-compare').setAttribute('aria-pressed', String(studio.compare));
  $('#studio-compare').disabled = record.id === 'SYN-108';
  $('#studio-compare').textContent = studio.compare ? '− Remove knee comparison' : '+ Compare knee case';
  $('#compare-key').textContent = studio.compare ? 'DASHED: SYN-108 · ALIGNED BY INCISION' : 'ONE CASE';
  $('#studio-tracks').innerHTML = signals.map(signal => {
    const paths = linePaths(record[signal.key], signal, 1100, 92);
    const x = 9 + studio.index / 12 * 1082;
    const value = record[signal.key][studio.index];
    const y = value === null ? 46 : 83 - (value - signal.min) / (signal.max - signal.min) * 74;
    const comparison = studio.compare ? linePaths(records[1][signal.key], signal, 1100, 92).map(points => `<polyline points="${points}" fill="none" stroke="${signal.color}" opacity=".55" stroke-width="1.8" stroke-dasharray="6 5"/>`).join('') : '';
    return `<div class="track" style="--signal:${signal.color}"><div class="track-metric"><span>${signal.label}</span><strong>${value ?? '—'}</strong><small>${signal.unit}</small></div><div class="track-plot"><svg viewBox="0 0 1100 92" preserveAspectRatio="none" role="img" aria-label="${signal.label} at ${minuteLabel(studio.index)}: ${value ?? 'missing'} ${value === null ? '' : signal.unit}"><rect x="${Math.max(0, x - 24)}" y="0" width="48" height="92" fill="#c4ed9311"/><line x1="${x}" y1="0" x2="${x}" y2="92" stroke="#d7f8ac" opacity=".5"/>${comparison}${paths.map(points => `<polyline points="${points}" fill="none" stroke="${signal.color}" stroke-width="2.5" stroke-linejoin="round"/>`).join('')}<circle cx="${x}" cy="${y}" r="4" fill="${value === null ? '#142124' : signal.color}" stroke="${signal.color}" stroke-width="2"/></svg><span class="plot-scale" aria-hidden="true"><span>${signal.max}</span><span>${signal.min}</span></span></div></div>`;
  }).join('');
}

function studioAnswer(mode) {
  const record = studio.record;
  if (mode === 'peak') {
    studio.index = peakIndex(record);
    $('#studio-answer').textContent = `The highest sampled PIP in ${record.id} is ${record.pip[studio.index]} cmH₂O at ${minuteLabel(studio.index)}. The playhead is on that sample. The cause is not established by these values.`;
  } else {
    const gaps = missingSamples(record);
    if (gaps.length) {
      studio.index = gaps[0].index;
      $('#studio-answer').textContent = `${record.id} has ${gaps.length} missing ${gaps.length === 1 ? 'sample' : 'samples'} in these three signals. ${gaps[0].signal.label} is missing at ${minuteLabel(studio.index)}. The gap stays visible in the trace and source table.`;
    } else $('#studio-answer').textContent = `${record.id} has all 39 values in this small fictional extract. This describes only the displayed 13 sample times.`;
  }
  renderStudio();
}

function promptMode(question) {
  if (/missing|gap|coverage/i.test(question)) return 'missing';
  if (/peak|pressure|pip/i.test(question)) return 'peak';
  return null;
}

function renderCanvas() {
  const record = records[0];
  const isPeak = canvas.mode === 'peak';
  const index = isPeak ? peakIndex(record) : missingSamples(record)[0].index;
  const signal = isPeak ? signals[0] : signals[1];
  $('#canvas-finding-title').textContent = isPeak ? `${record.pip[index]} cmH₂O at ${minuteLabel(index)}` : `ETCO₂ is missing at ${minuteLabel(index)}`;
  $('#canvas-finding-copy').textContent = isPeak ? 'This is the largest PIP value in the 13 displayed samples. Open the table to check the values before and after it.' : 'Twelve of thirteen ETCO₂ values are present. The plotted line stops at the gap; the missing value is not replaced with zero.';
  $('#canvas-spark').innerHTML = sparkline(record, signal);
  $('#canvas-source-signal').textContent = `${signal.label} · ${signal.unit}`;
  $('#canvas-source-coverage').textContent = `${record[signal.key].filter(value => value !== null).length} of 13 samples`;
  $('#canvas-limit').textContent = isPeak ? 'These samples describe a pressure pattern. They do not explain its cause or establish what a clinician should do.' : 'A missing value leaves the signal unknown at that sample time. An AI summary must retain that gap.';
  $('#canvas-comparison').hidden = !canvas.comparison;
  $('#canvas-add-hint').hidden = canvas.comparison;
  $('#canvas-expand').setAttribute('aria-pressed', String(canvas.comparison));
  $('#canvas-expand').textContent = canvas.comparison ? '− Remove comparison' : '+ Add a comparison';
  const other = records[1];
  $('#canvas-compare-title').textContent = isPeak ? `Another case peaks at ${Math.max(...other.pip)} cmH₂O.` : 'The second case also has a gap.';
  $('#canvas-compare-copy').textContent = isPeak ? `${other.id} reaches that value at ${minuteLabel(peakIndex(other))}. These two examples do not establish a cohort-wide difference.` : `${other.id} has a missing ETCO₂ value at ${minuteLabel(missingSamples(other)[0].index)}. Check coverage before comparing signal shapes.`;
}

function openSource(record, index = null, fromShortlist = false) {
  sourceReturn = fromShortlist ? 'shortlist' : null;
  if (fromShortlist) $('#shortlist-dialog').close();
  $('#source-title').textContent = `${record.id} / ${record.title}`;
  $('#source-description').textContent = index === null ? 'All sample times are relative to incision.' : `The highlighted row is the selected sample at ${minuteLabel(index)}.`;
  $('#source-rows').innerHTML = samples.map((minute, row) => `<tr class="${row === index ? 'current' : ''}"><th scope="row">${minute}</th><td>${record.pip[row] ?? '—'}</td><td>${record.co2[row] ?? '—'}</td><td>${record.hr[row] ?? '—'}</td></tr>`).join('');
  $('#source-dialog').showModal();
  $('#source-rows tr.current')?.scrollIntoView({ block: 'nearest' });
}

function renderDeck() {
  const record = deck.queue[deck.index];
  const peak = peakIndex(record);
  const gaps = missingSamples(record);
  $('#deck-progress').textContent = `CASE ${String(deck.index + 1).padStart(2, '0')} / ${String(deck.queue.length).padStart(2, '0')}`;
  $('#deck-order').textContent = deck.mode === 'peak' ? 'Sorted by peak PIP · synthetic examples' : 'Cases with missing samples · synthetic examples';
  $('#deck-case-id').textContent = record.id;
  $('#deck-number').textContent = String(deck.index + 1).padStart(2, '0');
  $('#deck-title').textContent = record.title;
  $('#deck-spark').innerHTML = sparkline(record, signals[0], '#ffe0a4');
  $('#deck-observation').textContent = deck.mode === 'peak' ? `A pressure peak at ${minuteLabel(peak)}.` : `${gaps.length} missing sample to inspect.`;
  $('#deck-copy').textContent = deck.mode === 'peak' ? `The highest PIP in this fictional extract is ${record.pip[peak]} cmH₂O. Read the surrounding values before interpreting the pattern.` : `${gaps[0].signal.label} is missing at ${minuteLabel(gaps[0].index)}. The pressure trace is complete, but a joint interpretation must account for the missing signal.`;
  $('#deck-peak').textContent = record.pip[peak];
  $('#deck-time').textContent = samples[peak];
  $('#deck-prev').disabled = deck.index === 0;
  $('#deck-next').disabled = deck.index === deck.queue.length - 1;
  $('#deck-save').disabled = deck.decisions.get(record.id) === 'saved';
  $('#deck-save').innerHTML = deck.decisions.get(record.id) === 'saved' ? 'Saved to your shortlist <span>✓</span>' : 'Save for closer review <span>+</span>';
  $('#deck-skip').disabled = deck.decisions.get(record.id) === 'skipped';
  $('#deck-skip').textContent = deck.decisions.get(record.id) === 'skipped' ? 'Left out' : 'Leave out';
  $('#saved-count').textContent = [...deck.decisions.values()].filter(value => value === 'saved').length;
}

function decideCase(decision) {
  const record = deck.queue[deck.index];
  deck.decisions.set(record.id, decision);
  const advance = deck.index < deck.queue.length - 1;
  if (advance) deck.index += 1;
  renderDeck();
  const remaining = deck.queue.filter(item => !deck.decisions.has(item.id)).length;
  $('#deck-status').textContent = `${record.id} ${decision === 'saved' ? 'saved to your shortlist' : 'left out'}. ${remaining ? `${remaining} ${remaining === 1 ? 'case remains' : 'cases remain'} to review.` : 'Review complete. Open your shortlist to inspect the saved cases.'}`;
  if (advance) revealOnSmallScreen('.review-card');
}

function showShortlist() {
  const saved = records.filter(record => deck.decisions.get(record.id) === 'saved');
  $('#shortlist-items').innerHTML = saved.length ? saved.map(record => `<div class="shortlist-item"><div><strong>${record.title}</strong><small>${record.id} · peak PIP ${Math.max(...record.pip)} cmH₂O</small></div><button data-saved-source="${record.id}">Inspect source ↗</button></div>`).join('') : '<p>No cases saved yet. Use “Save for closer review” on a case card.</p>';
  $('#shortlist-dialog').showModal();
}

document.addEventListener('click', event => {
  const study = event.target.closest('[data-study]');
  if (study) showStudy(study.dataset.study);
  const close = event.target.closest('[data-close]');
  if (close) $(`#${close.dataset.close}`).close();
  const caseButton = event.target.closest('[data-studio-case]');
  if (caseButton) { studio.record = records.find(record => record.id === caseButton.dataset.studioCase); if (studio.record.id === 'SYN-108') studio.compare = false; studioAnswer('peak'); }
  const action = event.target.closest('[data-studio-action]');
  if (action) { studioAnswer(action.dataset.studioAction); revealOnSmallScreen('.timeline-shell'); }
  const savedSource = event.target.closest('[data-saved-source]');
  if (savedSource) { const record = records.find(item => item.id === savedSource.dataset.savedSource); openSource(record, peakIndex(record), true); }
});

$('#studio-scrub').addEventListener('input', event => {
  studio.index = Number(event.target.value);
  renderStudio();
  $('#studio-answer').textContent = `Inspecting ${studio.record.id} at ${minuteLabel(studio.index)}. The values at left match this selected source row. Ask AI to jump to a peak or a missing sample.`;
});
$('#studio-compare').addEventListener('click', () => { studio.compare = !studio.compare; renderStudio(); });
$('#studio-source').addEventListener('click', () => openSource(studio.record, studio.index));
$('#studio-form').addEventListener('submit', event => {
  event.preventDefault();
  const mode = promptMode($('#studio-question').value);
  if (mode) { studioAnswer(mode); revealOnSmallScreen('.timeline-shell'); }
  else $('#studio-answer').textContent = 'This scripted study supports “peak pressure” and “missing data”. Your question has not been sent to a model.';
});
$('#canvas-form').addEventListener('submit', event => {
  event.preventDefault();
  const mode = promptMode($('#canvas-question').value);
  if (!mode) { $('#canvas-status').textContent = 'This scripted study supports “peak pressure” and “missing data”. The existing evidence has not changed.'; return; }
  canvas.mode = mode; renderCanvas();
  $('#canvas-status').textContent = `Evidence updated for ${mode === 'peak' ? 'peak pressure' : 'missing data'}. Open a source card to check the observation. No model ran.`;
  revealOnSmallScreen('.finding-node');
});
function toggleComparison() { canvas.comparison = !canvas.comparison; renderCanvas(); $('#canvas-status').textContent = canvas.comparison ? 'Added SYN-108 as a second fictional example. Each observation has its own source.' : 'Comparison removed. The original source remains.'; if (canvas.comparison) revealOnSmallScreen('#canvas-comparison'); }
$('#canvas-expand').addEventListener('click', toggleComparison);
$('#canvas-add').addEventListener('click', toggleComparison);
$('#canvas-reset').addEventListener('click', () => { canvas.mode = 'peak'; canvas.comparison = false; $('#canvas-question').value = 'Where does pressure peak?'; renderCanvas(); $('#canvas-status').textContent = 'Canvas reset to the peak-pressure example.'; });
$('#canvas-open-finding').addEventListener('click', () => openSource(records[0], canvas.mode === 'peak' ? peakIndex(records[0]) : missingSamples(records[0])[0].index));
$('#canvas-source').addEventListener('click', () => openSource(records[0]));
$('#canvas-compare-source').addEventListener('click', () => openSource(records[1], canvas.mode === 'peak' ? peakIndex(records[1]) : missingSamples(records[1])[0].index));
$('#deck-form').addEventListener('submit', event => {
  event.preventDefault();
  const mode = promptMode($('#deck-question').value);
  if (!mode) { $('#deck-status').textContent = 'Try “peak pressure” or “missing data”. This demo has no model connection, so the current queue is unchanged.'; return; }
  deck.mode = mode;
  deck.queue = mode === 'peak' ? [...records].sort((a, b) => Math.max(...b.pip) - Math.max(...a.pip)) : records.filter(record => missingSamples(record).length);
  deck.index = 0; renderDeck();
  $('#deck-status').textContent = `${deck.queue.length} fictional cases in this queue. Your saved cases are retained. AI retrieval is simulated.`;
  revealOnSmallScreen('.review-card');
});
$('#deck-prev').addEventListener('click', () => { deck.index = Math.max(0, deck.index - 1); renderDeck(); $('#deck-status').textContent = `Viewing ${deck.queue[deck.index].id}.`; });
$('#deck-next').addEventListener('click', () => { deck.index = Math.min(deck.queue.length - 1, deck.index + 1); renderDeck(); $('#deck-status').textContent = `Viewing ${deck.queue[deck.index].id}.`; });
$('#deck-save').addEventListener('click', () => decideCase('saved'));
$('#deck-skip').addEventListener('click', () => decideCase('skipped'));
$('#deck-saved').addEventListener('click', showShortlist);
$('#deck-source').addEventListener('click', () => { const record = deck.queue[deck.index]; openSource(record, deck.mode === 'peak' ? peakIndex(record) : missingSamples(record)[0].index); });
$('#deck-reset').addEventListener('click', () => { deck.decisions.clear(); deck.index = 0; renderDeck(); $('#deck-status').textContent = 'Review decisions cleared. The current queue is ready again.'; });
$('#source-dialog').addEventListener('close', () => { if (sourceReturn === 'shortlist') { sourceReturn = null; showShortlist(); } });
$('#setup-open').addEventListener('click', () => $('#setup-dialog').showModal());
studioAnswer('peak');
renderCanvas();
renderDeck();
const requestedStudy = location.hash.slice(1);
showStudy(['studio', 'canvas', 'deck'].includes(requestedStudy) ? requestedStudy : 'studio');
$('#setup-dialog').showModal();
