import assert from "node:assert/strict";
import test from "node:test";

import {
  calendarUrlFor,
  earningsConfidencePresentation,
} from "./calendarArtifacts.mjs";

test("the earnings calendar reads its dedicated normalized artifact", () => {
  const topPicks = "https://static.example/top-picks/latest.json";

  assert.equal(
    calendarUrlFor(topPicks, "earnings"),
    "https://static.example/calendar/normalized/earnings/latest.json",
  );
});

test("calendar artifact URL construction fails closed for an unknown layout", () => {
  assert.throws(
    () => calendarUrlFor("https://static.example/custom.json", "earnings"),
    /Unsupported top-picks artifact URL/,
  );
});

test("earnings confidence labels distinguish confirmed, conflicting, and sparse dates", () => {
  assert.deepEqual(earningsConfidencePresentation("confirmed"), {
    label: "Confirmed",
    tone: "positive",
  });
  assert.deepEqual(earningsConfidencePresentation("conflicting"), {
    label: "Conflicting",
    tone: "warning",
  });
  assert.deepEqual(earningsConfidencePresentation("single_source"), {
    label: "Single source",
    tone: "neutral",
  });
  assert.deepEqual(earningsConfidencePresentation(undefined), {
    label: "Unreconciled",
    tone: "muted",
  });
});
