import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectionDot } from "./ConnectionDot";

describe("ConnectionDot", () => {
  it.each([
    ["connected", "Live"],
    ["reconnecting", "Reconnecting"],
    ["disconnected", "Offline"],
  ] as const)("labels the %s state as %s", (status, label) => {
    render(<ConnectionDot status={status} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });
});
