"use client";

import { Panel } from "@/components/Panel";
import {
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedMoney,
  toneClass,
} from "@/lib/format";
import { useTerminal } from "@/state/TerminalProvider";

/**
 * Unrealized only. Realized P&L is derived from the trade log and is not
 * surfaced here (PLAN.md §7).
 *
 * Two percentages sit side by side and mean different things: "P&L %" is the
 * return on cost basis, "Day %" is `change_percent_session` — the move since
 * the session open, the same figure the watchlist shows.
 */
export function PositionsTable() {
  const { positions, prices, select, selected } = useTerminal();

  const rows = [...positions].sort((a, b) => a.ticker.localeCompare(b.ticker));

  return (
    <Panel
      title="Positions"
      meta={<span className="num text-tiny text-mute">{rows.length}</span>}
      className="h-full min-h-0"
      bodyClassName="overflow-auto"
    >
      {rows.length === 0 ? (
        <p className="px-3 py-6 text-tiny text-mute">
          No open positions. Use the order bar below to take one.
        </p>
      ) : (
        <table className="w-full border-collapse">
          <thead className="sticky top-0 bg-panel">
            <tr className="border-b border-hairline">
              <Th align="left">Symbol</Th>
              <Th>Qty</Th>
              <Th>Avg Cost</Th>
              <Th>Last</Th>
              <Th>Mkt Value</Th>
              <Th>Unrealized</Th>
              <Th>P&amp;L %</Th>
              <Th>Day %</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((position) => {
              const update = prices[position.ticker];
              const price = update?.price ?? position.avg_cost;
              const marketValue = position.quantity * price;
              const unrealized = marketValue - position.quantity * position.avg_cost;
              const pnlPercent =
                position.avg_cost > 0 ? ((price - position.avg_cost) / position.avg_cost) * 100 : 0;

              return (
                <tr
                  key={position.ticker}
                  onClick={() => select(position.ticker)}
                  className={`cursor-pointer border-b border-hairline/50 transition-colors ${
                    selected === position.ticker ? "bg-raised" : "hover:bg-raised/60"
                  }`}
                >
                  <td className="px-3 py-[6px] text-left font-display text-[12px] font-bold text-ink">
                    {position.ticker}
                  </td>
                  <Td>{formatQuantity(position.quantity)}</Td>
                  <Td>{formatPrice(position.avg_cost)}</Td>
                  <Td>{formatPrice(update?.price)}</Td>
                  <Td>{formatMoney(marketValue)}</Td>
                  <Td className={toneClass(unrealized)}>{formatSignedMoney(unrealized)}</Td>
                  <Td className={toneClass(pnlPercent)}>{formatPercent(pnlPercent)}</Td>
                  <Td className={update ? toneClass(update.change_percent_session) : "text-mute"}>
                    {formatPercent(update?.change_percent_session)}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function Th({ children, align = "right" }: { children: React.ReactNode; align?: "left" | "right" }) {
  // Tailwind cannot see an interpolated class name, so branch on whole classes.
  const alignment = align === "left" ? "text-left" : "text-right";
  return <th className={`label px-3 py-1.5 font-semibold ${alignment}`}>{children}</th>;
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`num px-3 py-[6px] text-right text-data ${className || "text-ink"}`}>{children}</td>;
}
