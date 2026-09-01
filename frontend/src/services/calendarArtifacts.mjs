export function calendarUrlFor(topPicksUrl, eventType) {
  const suffix = "/top-picks/latest.json";
  if (!topPicksUrl.endsWith(suffix)) {
    throw new Error(`Unsupported top-picks artifact URL: ${topPicksUrl}`);
  }
  return `${topPicksUrl.slice(0, -suffix.length)}/calendar/normalized/${eventType}/latest.json`;
}

export function earningsConfidencePresentation(status) {
  if (status === "confirmed" || status === "company_confirmed") {
    return { label: "Confirmed", tone: "positive" };
  }
  if (status === "conflicting") {
    return { label: "Conflicting", tone: "warning" };
  }
  if (status === "single_source") {
    return { label: "Single source", tone: "neutral" };
  }
  return { label: "Unreconciled", tone: "muted" };
}
