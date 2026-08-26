import { test } from 'node:test';
import assert from 'node:assert/strict';
import { expectedCompletedRunDate, workflowFreshness, failureMessage } from './workflowFreshness.ts';

test('both stale artifacts are overdue even when their dates match', () => {
  assert.deepEqual(workflowFreshness('2026-08-10', '2026-08-10', Date.parse('2026-08-26T10:00:00Z')),
    { expected: '2026-08-25', workflowOverdue: true, publicationIsStale: true });
});
test('grace window crosses midnight in UTC regardless of browser timezone', () => {
  assert.equal(expectedCompletedRunDate(Date.parse('2026-08-26T02:19:59+02:00')), '2026-08-24');
  assert.equal(expectedCompletedRunDate(Date.parse('2026-08-26T02:20:00+02:00')), '2026-08-25');
});
test('current failed workflow does not make older recommendations fresh', () => {
  assert.deepEqual(workflowFreshness('2026-08-10', '2026-08-25', Date.parse('2026-08-26T10:00:00Z')),
    { expected: '2026-08-25', workflowOverdue: false, publicationIsStale: true });
});
test('current publication with no workflow report still warns about missing status', () => {
  assert.equal(workflowFreshness('2026-08-25', undefined, Date.parse('2026-08-26T10:00:00Z')).workflowOverdue, true);
});
test('a fresh run and publication are not stale', () => {
  assert.equal(workflowFreshness('2026-08-25', '2026-08-25', Date.parse('2026-08-26T10:00:00Z')).publicationIsStale, false);
});
test('failure after analysis is not incorrectly described as analysis never reached', () => {
  assert.match(failureMessage('CollectReviewEvidence', true), /Analysis was reached/);
  assert.match(failureMessage('CollectPrices', false), /Analysis was not reached/);
  assert.match(failureMessage('Unknown'), /unavailable/);
});
