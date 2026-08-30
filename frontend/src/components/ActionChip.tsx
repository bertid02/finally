import { formatMoney, formatPrice, formatQuantity } from "@/lib/format";
import type { TradeAction, WatchlistAction } from "@/lib/types";

/**
 * Confirmation chips for actions the assistant took during a turn. Failures are
 * shown too, carrying the backend's own user-facing message verbatim — one
 * error vocabulary across the manual and the AI path.
 */
export function TradeChip({ action }: { action: TradeAction }) {
  const failed = action.status === "failed";
  const side = action.side === "buy" ? "Bought" : "Sold";

  return (
    <div
      data-testid="trade-chip"
      className={`flex flex-col gap-0.5 border-l-2 px-2 py-1 ${
        failed ? "border-down bg-down/10" : "border-up bg-up/10"
      }`}
    >
      <span className="font-mono text-tiny text-ink">
        {failed ? `${action.side === "buy" ? "Buy" : "Sell"} rejected` : side}{" "}
        {formatQuantity(action.quantity)} {action.ticker}
        {!failed && action.price !== undefined && ` at ${formatPrice(action.price)}`}
        {!failed && action.total !== undefined && ` · ${formatMoney(action.total)}`}
      </span>
      {failed && action.error && <span className="text-tiny text-down">{action.error}</span>}
    </div>
  );
}

export function WatchlistChip({ action }: { action: WatchlistAction }) {
  const failed = action.status === "failed";
  return (
    <div
      data-testid="watchlist-chip"
      className={`flex flex-col gap-0.5 border-l-2 px-2 py-1 ${
        failed ? "border-down bg-down/10" : "border-wire bg-wire/10"
      }`}
    >
      <span className="font-mono text-tiny text-ink">
        {failed
          ? `Watchlist ${action.action} rejected: ${action.ticker}`
          : `${action.action === "add" ? "Added" : "Removed"} ${action.ticker} ${action.action === "add" ? "to" : "from"} the watchlist`}
      </span>
      {failed && action.error && <span className="text-tiny text-down">{action.error}</span>}
    </div>
  );
}
