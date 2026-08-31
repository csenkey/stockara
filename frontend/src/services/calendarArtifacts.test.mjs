import assert from "node:assert/strict";
import test from "node:test";

import { calendarUrlFor } from "./calendarArtifacts.mjs";

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
