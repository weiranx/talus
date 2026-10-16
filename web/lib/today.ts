/** One shared scale keeps every load comparison aligned to the same usual level. */
export function loadComparison(values: (number | null)[]) {
  const ratios = values.map((value) => value != null && Number.isFinite(value) && value >= -1 ? 1 + value : null);
  const top = Math.max(1.6, ...ratios.map((ratio) => ratio ?? 0));
  return { usual: 100 / top, widths: ratios.map((ratio) => ratio == null ? null : ratio / top * 100) };
}

/** Calendar dates use UTC arithmetic so DST and the host timezone cannot shift a day. */
export function weekDays(start: string, today: string, activityDays: string[]) {
  const [y, m, d] = start.split("-").map(Number);
  const completed = new Set(activityDays);
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(Date.UTC(y, m - 1, d + index)).toISOString().slice(0, 10);
    return { day, completed: completed.has(day), today: day === today, future: day > today };
  });
}
