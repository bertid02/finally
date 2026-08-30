import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Recharts measures its container; jsdom reports 0x0, which renders nothing.
// Give every ResponsiveContainer a deterministic box so chart tests can assert.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as never);

Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 400 });

// jsdom implements no scrolling; the chat transcript pins itself to the bottom.
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
