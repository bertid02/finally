"use client";

import { useMemo } from "react";
import { ResponsiveContainer, Treemap } from "recharts";

import { Panel } from "@/components/Panel";
import { formatPercent } from "@/lib/format";
import { useTerminal } from "@/state/TerminalProvider";

interface Cell {
  name: string;
  value: number; // market value — drives the rectangle's area
  pnlPercent: number; // unrealized — drives its colour
  /** Recharts types tree nodes as open records. */
  [key: string]: string | number;
}

/** Saturates at ±5%: beyond that the colour stops carrying extra information. */
const FULL_SCALE = 5;

function fill(pnlPercent: number): string {
  const weight = Math.min(Math.abs(pnlPercent) / FULL_SCALE, 1);
  const alpha = 0.14 + weight * 0.62;
  if (Math.abs(pnlPercent) < 0.005) return "rgba(122,135,153,0.16)";
  return pnlPercent > 0 ? `rgba(51,214,159,${alpha})` : `rgba(255,93,108,${alpha})`;
}

interface CellProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  name?: string;
  pnlPercent?: number;
}

function TreeCell(props: CellProps) {
  const { x = 0, y = 0, width = 0, height = 0, name = "", pnlPercent = 0 } = props;
  if (width <= 0 || height <= 0) return null;

  const showLabel = width > 42 && height > 26;
  const showPercent = width > 58 && height > 42;

  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill(pnlPercent)} stroke="#0a0d13" strokeWidth={1} />
      {showLabel && (
        <text
          x={x + 6}
          y={y + 16}
          fill="#dbe3ef"
          fontFamily="Archivo, system-ui, sans-serif"
          fontSize={11}
          fontWeight={700}
        >
          {name}
        </text>
      )}
      {showPercent && (
        <text
          x={x + 6}
          y={y + 30}
          fill={pnlPercent >= 0 ? "#33d69f" : "#ff5d6c"}
          fontFamily="IBM Plex Mono, monospace"
          fontSize={10}
        >
          {formatPercent(pnlPercent)}
        </text>
      )}
    </g>
  );
}

export function Heatmap() {
  const { positions, prices } = useTerminal();

  const data = useMemo<Cell[]>(
    () =>
      positions
        .map((position) => {
          const price = prices[position.ticker]?.price ?? position.avg_cost;
          return {
            name: position.ticker,
            value: Math.max(position.quantity * price, 0.01),
            pnlPercent: position.avg_cost > 0 ? ((price - position.avg_cost) / position.avg_cost) * 100 : 0,
          };
        })
        .sort((a, b) => b.value - a.value),
    [positions, prices],
  );

  return (
    <Panel title="Allocation" meta={<span className="label">Area = weight · Colour = P&amp;L</span>} className="h-full min-h-0" bodyClassName="p-1">
      {data.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-tiny text-mute">No positions yet. Buy something to fill this out.</p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <Treemap
            data={data}
            dataKey="value"
            aspectRatio={4 / 3}
            stroke="#0a0d13"
            isAnimationActive={false}
            content={<TreeCell />}
          />
        </ResponsiveContainer>
      )}
    </Panel>
  );
}
