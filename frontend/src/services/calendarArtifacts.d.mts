export type CalendarEventType = "earnings" | "dividends";

export function calendarUrlFor(
  topPicksUrl: string,
  eventType: CalendarEventType,
): string;
