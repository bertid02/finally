"use client";

import { memo } from "react";
import { Line, LineChart, YAxis } from "recharts";

import type { Point } from "@/state/TerminalProvider";

const WIDTH = 76;
const HEIGHT = 20;
/** More points than this in 76px is ink, not information. */
const MAX_POINTS = 40;

/**
 * Progressive sparkline. There is no price history endpoint — the series
 * accumulates from the SSE stream since page load, so these fill in over the
 * first few seconds rather than arriving complete.
 */
function SparklineImpl({ points, tone }: { points: Point[]; tone: "up" | "down" | "flat" }) {
  if (points.length < 2) {
    return (
      <div
        className="flex items-center"
        style={{ width: WIDTH, height: HEIGHT }}
        aria-label="Collecting price history"
      >
        <span className="h-px w-full bg-hairline" />
      </div>
    );
  }

  const data = points.length > MAX_POINTS ? points.slice(-MAX_POINTS) : points;
  const stroke = tone === "up" ? "#33d69f" : tone === "down" ? "#ff5d6c" : "#7a8799";

  return (
    <LineChart width={WIDTH} height={HEIGHT} data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
      <YAxis hide domain={["dataMin", "dataMax"]} />
      <Line
        type="linear"
        dataKey="p"
        stroke={stroke}
        strokeWidth={1}
        dot={false}
        isAnimationActive={false}
      />
    </LineChart>
  );
}

export const Sparkline = memo(SparklineImpl);
