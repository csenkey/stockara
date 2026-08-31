export function calendarUrlFor(topPicksUrl, eventType) {
  const suffix = "/top-picks/latest.json";
  if (!topPicksUrl.endsWith(suffix)) {
    throw new Error(`Unsupported top-picks artifact URL: ${topPicksUrl}`);
  }
  return `${topPicksUrl.slice(0, -suffix.length)}/calendar/normalized/${eventType}/latest.json`;
}
