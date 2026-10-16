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
const { benchmark, readinessValue } = load(path.resolve(__dirname, '../lib/readiness.ts'));
const Readiness = load(path.resolve(__dirname, '../components/plan/Readiness.tsx')).default;
const time = { key: 'time_on_feet', name: 'Time on feet', demand: 41.3, capacity: 13.4, rule: .3, ratio: 13.4 / 41.3,
  unit: 'h', status: 'ready', status_text: 'Ready', demand_label: 'Estimated time', capacity_label: 'Longest single day or back-to-back pair, last 8 weeks' };
const r = { dims: [time], verdict: 'ready', demand: { time: { low_h: 35.1, high_h: 45.4, pace_learned: true } }, pace: { min_per_effort_mi: 11.5 } };

test('race duration and training benchmark are distinct; missing requirements stay unavailable', () => {
  assert.equal(readinessValue(time, benchmark(time)), '12.4 h');
  assert.equal(benchmark({ ...time, demand: null }), null);
  assert.equal(benchmark({ ...time, rule: null }), null);
  assert.equal(readinessValue(time, null), 'Not available');
  assert.equal(readinessValue(time, 0), '0.0 h');
});
test('altitude evidence retains hours even when it equals the route altitude', () => {
  const d = { ...time, key: 'altitude', demand: 8000 };
  assert.equal(readinessValue(d, 8000, true), '8,000 ft');
  assert.equal(readinessValue(d, 8000), '8000.0 h above 8,000 ft');
});
test('rendered comparison exposes projected regression, threshold and pair basis without claiming ready', () => {
  const projected = { dims: [{ ...time, capacity: 11.8, status: 'close' }], verdict: 'close' };
  const html = renderToStaticMarkup(React.createElement(Readiness, { r, projected, note: 'Fitness only' }));
  for (const text of ['41.3 h', '12.4 h', '13.4 h', '11.8 h', 'Benchmark met', 'Near benchmark', '85%', 'future window']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('>Ready<'));
  const stale = renderToStaticMarkup(React.createElement(Readiness, { r, projected, projectionCurrent: false, note: '' }));
  assert.ok(stale.includes('Needs updated draft'));
  assert.ok(!stale.includes('Near benchmark'));
});
test('unknown dimension prevents an unqualified all-benchmarks-met summary', () => {
  const html = renderToStaticMarkup(React.createElement(Readiness, {
    r: { ...r, dims: [time, { ...time, key: 'descent_durability', demand: null, status: 'unknown' }] }, projected: null, note: '',
  }));
  assert.ok(html.includes('Some benchmarks not assessed'));
  assert.ok(html.includes('Not assessed'));
});
