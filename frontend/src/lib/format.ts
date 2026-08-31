/** Display formatting. Every figure on screen goes through one of these. */

const money0 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});
const money2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(value: number, decimals: 0 | 2 = 2): string {
  if (!Number.isFinite(value)) return "—";
  return `$${(decimals === 0 ? money0 : money2).format(value)}`;
}

/** Prices always carry two decimals so decimal points align down a column. */
export function formatPrice(value: number | undefined): string {
  return value === undefined || !Number.isFinite(value) ? "—" : money2.format(value);
}

/** Signed percent, e.g. "+0.26%". Use ONLY with session-based figures. */
export function formatPercent(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}%`;
}

export function formatSignedMoney(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}$${money2.format(Math.abs(value))}`;
}

/** Fractional shares are supported, so trim to what the quantity actually is. */
export function formatQuantity(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, "");
}

export function formatClock(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString("en-US", { hour12: false });
}

/** Tailwind colour class for a signed figure. Flat sits at neutral, not green. */
export function toneClass(value: number): string {
  if (value > 0) return "text-up";
  if (value < 0) return "text-down";
  return "text-mute";
}
