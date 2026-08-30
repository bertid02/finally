"use client";

import type { StreamStatus } from "@/lib/api";

const COPY: Record<StreamStatus, { label: string; dot: string; text: string }> = {
  connected: { label: "Live", dot: "bg-up", text: "text-up" },
  reconnecting: { label: "Reconnecting", dot: "bg-signal animate-pulse", text: "text-signal" },
  disconnected: { label: "Offline", dot: "bg-down", text: "text-down" },
};

/**
 * Reflects the EventSource readyState only. The stream is silent whenever no
 * price moves, so silence must never be read as a lost connection.
 */
export function ConnectionDot({ status }: { status: StreamStatus }) {
  const { label, dot, text } = COPY[status];
  return (
    <div className="flex items-center gap-2" role="status" aria-live="polite">
      <span aria-hidden className={`h-[6px] w-[6px] rounded-full ${dot}`} />
      <span className={`label ${text}`}>{label}</span>
    </div>
  );
}
