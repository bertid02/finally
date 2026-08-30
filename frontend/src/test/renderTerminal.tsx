import { act, render, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { expect } from "vitest";

import { TerminalProvider } from "@/state/TerminalProvider";

import { fakeApi } from "./fakeApi";

/**
 * Renders a component inside a live provider and lets the initial
 * watchlist/portfolio/history load settle, so tests never race the empty
 * first frame — the ticker selection in particular is set from that load.
 */
export async function renderTerminal(ui: ReactElement) {
  const view = render(<TerminalProvider>{ui}</TerminalProvider>);
  await waitFor(() => expect(fakeApi.getPortfolio).toHaveBeenCalled());
  await act(async () => {
    for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();
  });
  return view;
}
