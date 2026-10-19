const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const cache = new Map();
function load(file) {
  if (cache.has(file)) return cache.get(file);
  const exports = {};
  cache.set(file, exports);
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  new Function('exports', 'require', compiled)(exports, (name) => {
    if (name.endsWith('.css')) return {};
    if (!name.startsWith('.') && !name.startsWith('@/')) return require(name);
    const base = name.startsWith('@/') ? path.resolve(__dirname, '..', name.slice(2)) : path.resolve(path.dirname(file), name);
    return load(['.ts', '.tsx'].map(ext => base + ext).find(fs.existsSync));
  });
  return exports;
}
const { DurabilityCard } = load(path.resolve(__dirname, '../components/TerrainCards.tsx'));
const half = { time_s: 13140, distance_mi: 9.9, gain_ft: 2740, flat_pace_min_mi: 18 + 47/60, avg_hr: 125, climb_rate_ft_h: 1491, efficiency: 1 };
const d = { first: half, second: { ...half, flat_pace_min_mi: 19 + 43/60, avg_hr: 122, climb_rate_ft_h: 1406 }, decoupling_pct: .5, usual_decoupling_pct: 1.5, usual_runs: 8, verdict: 'held' };
const render = data => renderToStaticMarkup(React.createElement(DurabilityCard, { d: data }));
test('comparison explains falling HR, history scope and changes', () => {
  const html = render(d);
  for (const text of ['0.5% efficiency drop', '56 sec/mi slower', '−3 bpm', '−6%', '3h 39m', '8 activities', 'prior 90 days', 'not matched by duration', 'average heart rate fell', 'included in the displayed pace']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('Heart rate drifted'));
  assert.ok(!html.includes('runs this long'));
});
test('missing HR never inherits pace-only held verdict', () => {
  const html = render({ ...d, decoupling_pct: null, usual_decoupling_pct: null, first: { ...half, avg_hr: null, climb_rate_ft_h: null } });
  assert.ok(html.includes('Aerobic efficiency unavailable'));
  assert.ok(html.includes('Recent median unavailable'));
  assert.ok(html.includes('Uphill speed'));
  assert.ok(!html.includes('Aerobic effort held steady'));
});
test('boundary labels distinguish zero and negative changes', () => {
  for (const [value, label] of [[0, 'Aerobic efficiency unchanged'], [-3, '3% efficiency gain'], [4.9, 'Aerobic effort held steady'], [5, 'Aerobic efficiency declined'], [10, 'Aerobic efficiency declined substantially']]) {
    assert.ok(render({ ...d, decoupling_pct: value }).includes(label));
  }
});
