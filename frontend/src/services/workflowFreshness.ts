/** The 21:05 UTC daily run has a three-hour timeout and 15-minute grace. */
export function expectedCompletedRunDate(now: number): string {
  const deadline = new Date(now);
  deadline.setUTCHours(0, 20, 0, 0);
  const daysBack = now < deadline.getTime() ? 2 : 1;
  deadline.setUTCDate(deadline.getUTCDate() - daysBack);
  return deadline.toISOString().slice(0, 10);
}

export function workflowFreshness(
  publicationDate: string,
  workflowDate: string | undefined,
  now: number,
) {
  const expected = expectedCompletedRunDate(now);
  return {
    expected,
    workflowOverdue: !workflowDate || workflowDate < expected,
    publicationIsStale:
      publicationDate < expected || Boolean(workflowDate && workflowDate > publicationDate),
  };
}

export function failureMessage(step: string, analysisReached?: boolean): string {
  if (analysisReached === true) return `Analysis was reached, but ${step} blocked completion of the run.`;
  if (analysisReached === false) return `Analysis was not reached; ${step} blocked the current run.`;
  return `${step} blocked the current run; analysis progress is unavailable.`;
}
