"use client";

import { ConnectionDot } from "@/components/ConnectionDot";
import { formatMoney, formatPercent, formatSignedMoney, toneClass } from "@/lib/format";
import { useTerminal } from "@/state/TerminalProvider";

/**
 * The console's readout strip. Net liquidation is the one figure set large —
 * everything else in the interface is sized to be scanned, this one to be seen.
 */
export function Header() {
  const { totalValue, cash, positions, prices, status } = useTerminal();

  const costBasis = positions.reduce((sum, p) => sum + p.quantity * p.avg_cost, 0);
  const marketValue = positions.reduce(
    (sum, p) => sum + p.quantity * (prices[p.ticker]?.price ?? p.avg_cost),
    0,
  );
  const unrealized = marketValue - costBasis;
  const unrealizedPercent = costBasis > 0 ? (unrealized / costBasis) * 100 : 0;

  return (
    <header className="flex h-14 shrink-0 items-center gap-6 border-b border-hairline bg-panel px-4">
      <div className="flex items-baseline gap-2">
        <span className="font-display text-[15px] font-extrabold uppercase tracking-[0.18em] text-ink">
          Fin<span className="text-signal">Ally</span>
        </span>
        <span className="label hidden whitespace-nowrap text-mute/70 xl:inline">Trading Console</span>
      </div>

      <div className="h-8 w-px bg-hairline" />

      <div className="flex items-baseline gap-3">
        <span className="label">Net Liquidation</span>
        <span className="num text-readout font-medium text-ink">{formatMoney(totalValue)}</span>
      </div>

      <Readout label="Unrealized">
        <span className={`num text-data ${toneClass(unrealized)}`}>
          {formatSignedMoney(unrealized)}
          <span className="ml-2 text-mute">{formatPercent(unrealizedPercent)}</span>
        </span>
      </Readout>

      <Readout label="Cash">
        <span className="num text-data text-ink">{formatMoney(cash)}</span>
      </Readout>

      <Readout label="Positions">
        <span className="num text-data text-ink">{positions.length}</span>
      </Readout>

      <div className="ml-auto">
        <ConnectionDot status={status} />
      </div>
    </header>
  );
}

function Readout({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="hidden flex-col gap-[3px] md:flex">
      <span className="label">{label}</span>
      {children}
    </div>
  );
}
