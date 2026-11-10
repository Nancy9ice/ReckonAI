// Amounts arrive as decimal strings with their own currency and are only ever
// formatted here. No arithmetic, no parseFloat, no Number() on a money string:
// Intl.NumberFormat accepts a decimal string directly and formats it exactly
// (specs/tech-stack.md section 7).

export interface Money {
  amount: string;
  currency: string;
}

export function formatMoney(money: Money | null | undefined): string {
  if (!money) return "unavailable";
  return new Intl.NumberFormat("en", {
    style: "currency",
    currency: money.currency,
  }).format(money.amount as `${number}`);
}

// Unitless figures (scores, confidences) are strings in [0, 1] from the API.
// Presented as a percentage without converting through a binary float.
export function formatFraction(value: string | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  return new Intl.NumberFormat("en", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value as `${number}`);
}
