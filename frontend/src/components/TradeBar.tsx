"use client";

import { useEffect, useState, type FormEvent } from "react";

import { formatMoney, formatPrice } from "@/lib/format";
import type { TradeSide } from "@/lib/types";
import { useTerminal } from "@/state/TerminalProvider";

/**
 * Order entry. Market orders only, instant fill, no confirmation dialog.
 *
 * Both buttons stay disabled until a price for the symbol has arrived over the
 * stream: the fill uses the server's cached price, and a symbol with no cached
 * price is rejected outright (UNKNOWN_TICKER). Under the 15s Massive poll a
 * freshly added ticker really is untradeable for a few seconds.
 */
export function TradeBar() {
  const { selected, prices, positions, trade, notice, dismissNotice } = useTerminal();
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("10");
  const [busy, setBusy] = useState(false);

  // Follow the watchlist selection, but never overwrite what is being typed.
  useEffect(() => {
    if (selected) setTicker(selected);
  }, [selected]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(dismissNotice, 5000);
    return () => clearTimeout(timer);
  }, [notice, dismissNotice]);

  const symbol = ticker.trim().toUpperCase();
  const update = prices[symbol];
  const parsedQuantity = Number(quantity);
  const quantityValid = Number.isFinite(parsedQuantity) && parsedQuantity > 0;
  const held = positions.find((position) => position.ticker === symbol);
  const notional = update && quantityValid ? update.price * parsedQuantity : null;
  const tradable = Boolean(update) && quantityValid && !busy;

  async function submit(side: TradeSide, event?: FormEvent) {
    event?.preventDefault();
    if (!tradable) return;
    setBusy(true);
    await trade({ ticker: symbol, quantity: parsedQuantity, side });
    setBusy(false);
  }

  return (
    <form
      onSubmit={(event) => submit("buy", event)}
      className="flex h-14 shrink-0 items-center gap-4 border-t border-hairline bg-panel px-4"
    >
      <span className="label hidden xl:inline">Order</span>

      <Field label="Symbol" htmlFor="trade-ticker">
        <input
          id="trade-ticker"
          value={ticker}
          onChange={(event) => setTicker(event.target.value.toUpperCase())}
          maxLength={5}
          placeholder="AAPL"
          className="field w-24 uppercase"
        />
      </Field>

      <Field label="Quantity" htmlFor="trade-quantity">
        <input
          id="trade-quantity"
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          inputMode="decimal"
          className="field w-24 text-right"
        />
      </Field>

      <div className="flex flex-col gap-[3px]">
        <span className="label">Last</span>
        <span className="num text-data text-ink">{formatPrice(update?.price)}</span>
      </div>

      <div className="flex flex-col gap-[3px]">
        <span className="label">Est. Notional</span>
        <span className="num text-data text-signal">
          {notional === null ? "—" : formatMoney(notional)}
        </span>
      </div>

      {held && (
        <div className="hidden flex-col gap-[3px] xl:flex">
          <span className="label">Held</span>
          <span className="num text-data text-ink">{held.quantity}</span>
        </div>
      )}

      <div className="ml-auto flex items-center gap-3">
        {notice && (
          <span
            role={notice.tone === "error" ? "alert" : "status"}
            className={`max-w-[380px] truncate text-tiny ${notice.tone === "error" ? "text-down" : "text-mute"}`}
          >
            {notice.text}
          </span>
        )}

        {!update && symbol && (
          <span className="text-tiny text-mute">Waiting for a price on {symbol}…</span>
        )}

        <button
          type="submit"
          disabled={!tradable}
          className="btn w-24 border-up/50 bg-up/10 text-up hover:enabled:bg-up/20"
        >
          Buy
        </button>
        <button
          type="button"
          onClick={() => submit("sell")}
          disabled={!tradable}
          className="btn w-24 border-down/50 bg-down/10 text-down hover:enabled:bg-down/20"
        >
          Sell
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-[3px]">
      <label htmlFor={htmlFor} className="label">
        {label}
      </label>
      {children}
    </div>
  );
}
