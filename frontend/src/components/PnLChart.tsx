"use client";

import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Panel } from "@/components/Panel";
import { formatClock, formatMoney, formatSignedMoney, toneClass } from "@/lib/format";
import { useTerminal } from "@/state/TerminalProvider";

interface Row {
  t: number;
  /** Persisted snapshots — written on trades only, so this series is sparse. */
  hist?: number;
  /** Marked live from the price stream since page load. */
  live?: number;
}

export function PnLChart() {
  const { snapshots, liveEquity, totalValue } = useTerminal();

  const { rows, opening, span } = useMemo(() => {
    const history: Row[] = snapshots
      .map((snapshot) => ({ t: Date.parse(snapshot.recorded_at) / 1000, hist: snapshot.total_value }))
      .filter((row) => Number.isFinite(row.t))
      .sort((a, b) => a.t - b.t);

    const live: Row[] = liveEquity.map((point) => ({ t: point.t, live: point.p }));

    // Join the sparse persisted series to the live one so the curve is
    // continuous rather than two floating fragments.
    const last = history.at(-1);
    if (last && live.length > 0) last.live = last.hist;

    const merged = [...history, ...live].sort((a, b) => a.t - b.t);
    const values = merged.map((row) => row.hist ?? row.live ?? 0);
    return {
      rows: merged,
      opening: merged[0]?.hist ?? merged[0]?.live ?? totalValue,
      // A day that moves a few dollars would otherwise print the same rounded
      // label on every gridline.
      span: values.length ? Math.max(...values) - Math.min(...values) : 0,
    };
  }, [snapshots, liveEquity, totalValue]);

  const delta = totalValue - opening;

  return (
    <Panel
      title="Equity Curve"
      meta={
        rows.length > 0 && (
          <span className={`num text-data ${toneClass(delta)}`}>{formatSignedMoney(delta)}</span>
        )
      }
      className="h-full min-h-0"
      bodyClassName="p-2"
    >
      {rows.length < 2 ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-tiny text-mute">The curve starts building as prices arrive.</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#222b3b" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={formatClock}
              stroke="#7a8799"
              tick={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}
              tickLine={false}
              axisLine={{ stroke: "#222b3b" }}
              minTickGap={48}
            />
            <YAxis
              domain={["auto", "auto"]}
              orientation="right"
              width={62}
              stroke="#7a8799"
              tick={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value: number) => formatMoney(value, span < 50 ? 2 : 0)}
            />
            <Tooltip
              contentStyle={{
                background: "#111722",
                border: "1px solid #2f3a4d",
                borderRadius: 2,
                fontFamily: "IBM Plex Mono",
                fontSize: 11,
              }}
              labelFormatter={(value) => formatClock(Number(value))}
              formatter={(value) => [formatMoney(Number(value)), "Value"]}
            />
            <Line
              dataKey="hist"
              stroke="#7a8799"
              strokeWidth={1}
              strokeDasharray="3 3"
              dot={{ r: 2, fill: "#ecad0a", stroke: "none" }}
              connectNulls
              isAnimationActive={false}
            />
            <Line
              dataKey="live"
              stroke="#209dd7"
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}
