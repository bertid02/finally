"use client";

import { useMemo } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Panel } from "@/components/Panel";
import { formatClock, formatPercent, formatPrice, toneClass } from "@/lib/format";
import { useTerminal } from "@/state/TerminalProvider";

/**
 * The selected ticker, plotted against its session open. The reference line is
 * the point of the chart: everything above it is a green day, below it a red one.
 */
export function DetailChart() {
  const { selected, prices, series } = useTerminal();
  const update = selected ? prices[selected] : undefined;
  const points = useMemo(() => (selected ? (series[selected] ?? []) : []), [selected, series]);

  const sessionPercent = update?.change_percent_session;
  const positive = (sessionPercent ?? 0) >= 0;
  const stroke = positive ? "#33d69f" : "#ff5d6c";

  const domain = useMemo<[number, number]>(() => {
    if (points.length === 0) return [0, 1];
    const values = points.map((point) => point.p);
    if (update) values.push(update.session_open);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const pad = (high - low || high * 0.002) * 0.25;
    return [low - pad, high + pad];
  }, [points, update]);

  return (
    <Panel
      title={selected ?? "No selection"}
      meta={
        update && (
          <span className="flex items-baseline gap-3">
            <span className="num text-[15px] text-ink">{formatPrice(update.price)}</span>
            <span className={`num text-data ${toneClass(sessionPercent ?? 0)}`}>
              {formatPercent(sessionPercent)}
            </span>
            <span className="label">Open {formatPrice(update.session_open)}</span>
          </span>
        )
      }
      actions={<span className="label">Since page load</span>}
      className="h-full min-h-0"
      bodyClassName="p-2"
    >
      {points.length < 2 ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-tiny text-mute">
            {selected ? `Collecting ${selected} ticks…` : "Pick a symbol from the watchlist."}
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="detail-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#222b3b" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={formatClock}
              stroke="#7a8799"
              tick={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}
              tickLine={false}
              axisLine={{ stroke: "#222b3b" }}
              minTickGap={48}
            />
            <YAxis
              domain={domain}
              orientation="right"
              width={62}
              stroke="#7a8799"
              tick={{ fontSize: 10, fontFamily: "IBM Plex Mono" }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value: number) => value.toFixed(2)}
            />
            {update && (
              <ReferenceLine
                y={update.session_open}
                stroke="#ecad0a"
                strokeDasharray="3 3"
                strokeOpacity={0.7}
              />
            )}
            <Tooltip
              contentStyle={{
                background: "#111722",
                border: "1px solid #2f3a4d",
                borderRadius: 2,
                fontFamily: "IBM Plex Mono",
                fontSize: 11,
              }}
              labelFormatter={(value) => formatClock(Number(value))}
              formatter={(value) => [formatPrice(Number(value)), "Price"]}
            />
            <Area
              type="monotone"
              dataKey="p"
              stroke={stroke}
              strokeWidth={1.5}
              fill="url(#detail-fill)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}
