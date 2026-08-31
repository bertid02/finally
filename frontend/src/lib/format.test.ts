import { describe, expect, it } from "vitest";

import {
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedMoney,
  toneClass,
} from "./format";

describe("format", () => {
  it("always signs a percentage so gains and losses read at a glance", () => {
    expect(formatPercent(0.2612)).toBe("+0.26%");
    expect(formatPercent(-1.5)).toBe("−1.50%");
    expect(formatPercent(0)).toBe("+0.00%");
  });

  it("renders a placeholder rather than NaN when a figure is unknown", () => {
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPrice(undefined)).toBe("—");
    expect(formatMoney(Number.NaN)).toBe("—");
  });

  it("pads prices to two decimals so columns align", () => {
    expect(formatPrice(190.5)).toBe("190.50");
    expect(formatPrice(1234.567)).toBe("1,234.57");
  });

  it("formats money and signed money", () => {
    expect(formatMoney(8095)).toBe("$8,095.00");
    expect(formatMoney(8095, 0)).toBe("$8,095");
    expect(formatSignedMoney(-482.194)).toBe("−$482.19");
    expect(formatSignedMoney(482.19)).toBe("+$482.19");
  });

  it("keeps fractional share quantities readable", () => {
    expect(formatQuantity(10)).toBe("10");
    expect(formatQuantity(0.5)).toBe("0.5");
  });

  it("treats a flat figure as neutral, not green", () => {
    expect(toneClass(1)).toBe("text-up");
    expect(toneClass(-1)).toBe("text-down");
    expect(toneClass(0)).toBe("text-mute");
  });
});
