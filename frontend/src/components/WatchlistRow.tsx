"use client";

import { Sparkline } from "@/components/Sparkline";
import { usePriceFlash } from "@/hooks/usePriceFlash";
import { formatPercent, formatPrice, toneClass } from "@/lib/format";
import type { PriceUpdate } from "@/lib/types";
import type { Point } from "@/state/TerminalProvider";

export function WatchlistRow({
  ticker,
  update,
  points,
  selected,
  onSelect,
  onRemove,
}: {
  ticker: string;
  update: PriceUpdate | undefined;
  points: Point[];
  selected: boolean;
  onSelect(ticker: string): void;
  onRemove(ticker: string): void;
}) {
  const flash = usePriceFlash(update);
  // The DISPLAYED change is versus session open. change_percent is tick-over-
  // tick noise and drives the flash class above — it is never shown as a number.
  const sessionPercent = update?.change_percent_session;
  const tone = sessionPercent === undefined ? "flat" : sessionPercent > 0 ? "up" : sessionPercent < 0 ? "down" : "flat";

  return (
    <div
      className={`group relative grid grid-cols-[48px_1fr_70px_62px] items-center gap-1.5 border-b border-hairline/60 px-2.5 py-[7px] transition-colors ${
        selected ? "bg-raised" : "hover:bg-raised/60"
      }`}
    >
      {selected && <span aria-hidden className="absolute inset-y-0 left-0 w-[2px] bg-signal" />}

      <button
        type="button"
        onClick={() => onSelect(ticker)}
        aria-pressed={selected}
        className="justify-self-start font-display text-[12px] font-bold tracking-wide text-ink hover:text-signal"
      >
        {ticker}
      </button>

      <div className="justify-self-center">
        <Sparkline points={points} tone={tone} />
      </div>

      <span className={`num justify-self-end text-data text-ink ${flash} px-1`} data-testid={`price-${ticker}`}>
        {formatPrice(update?.price)}
      </span>

      <span
        className={`num justify-self-end text-data ${sessionPercent === undefined ? "text-mute" : toneClass(sessionPercent)}`}
        data-testid={`session-${ticker}`}
      >
        {formatPercent(sessionPercent)}
      </span>

      <button
        type="button"
        onClick={() => onRemove(ticker)}
        aria-label={`Remove ${ticker} from watchlist`}
        className="absolute right-0 top-1/2 hidden h-full -translate-y-1/2 items-center bg-gradient-to-l from-raised via-raised px-2 text-mute hover:text-down group-hover:flex"
      >
        <span aria-hidden className="text-[13px] leading-none">×</span>
      </button>
    </div>
  );
}
