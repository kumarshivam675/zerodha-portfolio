// Pure text masking for privacy mode (no React), so it can be tested on its own.

export const MASK = "***";

export type Sensitive = { amounts: number[]; quantities: number[] };

// The analyst wraps the investor's own figures in {{…}} (see the agent's system prompt).
const PRIVATE = /\{\{([^{}]+)\}\}/g;

/** Text for display: tagged private figures masked when hidden, braces removed otherwise. */
export function privacyText(text: string, hidden: boolean, sensitive: Sensitive | null): string {
  if (PRIVATE.test(text)) {
    PRIVATE.lastIndex = 0;
    return text.replace(PRIVATE, (_m, inner: string) => (hidden ? maskFigure(inner) : inner));
  }
  // Older replies have no tags: fall back to matching amounts against the portfolio.
  return hidden ? maskText(text, sensitive) : text;
}

/** "+₹21,400" -> "+₹***", "40 shares" -> "*** shares", "₹1.05 L" -> "₹***". */
function maskFigure(figure: string): string {
  const sign = figure.trim().match(/^[+\-−]/)?.[0] ?? "";
  const currency = /₹|Rs\.?|INR/.exec(figure)?.[0] ?? "";
  const words = figure
    .replace(/^\s*[+\-−]?\s*(?:₹|Rs\.?|INR)?\s*[\d,]+(?:\.\d+)?\s*(?:lakhs?|crores?|L|Cr|cr|k|K)?(?![A-Za-z])/, "")
    .trim();
  return `${sign}${currency}${MASK}${words ? " " + words : ""}`;
}

const MULTIPLIER: Record<string, number> = { k: 1e3, l: 1e5, lakh: 1e5, lakhs: 1e5, cr: 1e7, crore: 1e7, crores: 1e7 };
// ₹2,635.00 · ₹4.20 L · ₹4,448 cr · -₹69 · Rs. 500 · INR 2 lakh
const RUPEE_AMOUNT = /(₹|Rs\.?\s?|INR\s?)\s?([\d,]+(?:\.\d+)?)(\s?(?:lakhs?|crores?|L|Cr|cr|k|K)\b)?/g;
const QUANTITY = /\b([\d,]+(?:\.\d+)?)(\s?(?:units|shares|qty))\b/gi;

function isSensitive(value: number, halfStep: number, amounts: number[]) {
  return amounts.some((a) => Math.abs(a - value) <= Math.max(halfStep, a * 0.001));
}

export function maskText(text: string, sensitive: Sensitive | null): string {
  if (!sensitive) return text.replace(RUPEE_AMOUNT, (_m, sym: string) => `${sym}${MASK}`); // still loading: be safe
  return text
    .replace(RUPEE_AMOUNT, (match: string, sym: string, digits: string, unit?: string) => {
      const n = parseFloat(digits.replace(/,/g, ""));
      const mult = unit ? MULTIPLIER[unit.trim().toLowerCase()] ?? 1 : 1;
      const decimals = digits.includes(".") ? digits.split(".")[1].length : 0;
      const halfStep = 0.5 * 10 ** -decimals * mult;
      return isSensitive(n * mult, halfStep, sensitive.amounts) ? `${sym}${MASK}` : match;
    })
    .replace(QUANTITY, (match: string, digits: string, unit: string) => {
      const n = parseFloat(digits.replace(/,/g, ""));
      return sensitive.quantities.some((q) => Math.abs(q - n) < 1e-6) ? `${MASK}${unit}` : match;
    });
}

