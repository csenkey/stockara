export type CalendarEventType = "earnings" | "dividends";

export function calendarUrlFor(
  topPicksUrl: string,
  eventType: CalendarEventType,
): string;

export interface ConfidencePresentation {
  label: string;
  tone: "positive" | "warning" | "neutral" | "muted";
}

export function earningsConfidencePresentation(
  status?: string | null,
): ConfidencePresentation;
