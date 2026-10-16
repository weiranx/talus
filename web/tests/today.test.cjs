const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');

// Exercise the same pure TypeScript helpers the server-rendered page uses, on Node 22 too.
const source = fs.readFileSync(require.resolve('../lib/today.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const helpers = {};
new Function('exports', compiled)(helpers);
const { loadComparison, weekDays } = helpers;

test('missing, invalid and impossible baselines remain unavailable, never zero load', () => {
  const scale = loadComparison([null, NaN, Infinity, -2]);
  assert.deepEqual(scale.widths, [null, null, null, null]);
  assert.ok(Number.isFinite(scale.usual));
});

test('comparison geometry shares a baseline and preserves ratios, including zero and large loads', () => {
  const values = [-1, 0, 0.08, 0.24, 4];
  const { usual, widths } = loadComparison(values);
  for (let i = 0; i < values.length; i++) {
    assert.ok(Math.abs(widths[i] / usual - (1 + values[i])) < 1e-10);
    assert.ok(widths[i] >= 0 && widths[i] <= 100);
  }
  assert.equal(widths[0], 0);
  assert.equal(widths.at(-1), 100);
});

test('week dates survive DST and month boundaries; empty past days are not marked completed', () => {
  const days = weekDays('2026-10-26', '2026-11-01', ['2026-10-27', '2026-10-27']);
  assert.equal(days.length, 7);
  assert.equal(days[6].day, '2026-11-01');
  assert.equal(days[6].today, true);
  assert.deepEqual(days.filter(d => d.completed).map(d => d.day), ['2026-10-27']);
  assert.equal(days[0].completed, false);
  assert.equal(days[0].future, false);
});

test('future calendar days remain distinct from recorded activities', () => {
  const days = weekDays('2026-10-12', '2026-10-16', ['2026-10-13', '2026-10-16']);
  assert.equal(days[4].today, true);
  assert.equal(days[4].completed, true);
  assert.deepEqual(days.filter(d => d.future).map(d => d.day), ['2026-10-17', '2026-10-18']);
});
