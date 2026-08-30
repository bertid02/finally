"use client";

import { useState, type FormEvent } from "react";

import { Panel } from "@/components/Panel";
import { WatchlistRow } from "@/components/WatchlistRow";
import { useTerminal } from "@/state/TerminalProvider";

export function Watchlist() {
  const { watchlist, prices, series, selected, select, addTicker, removeTicker } = useTerminal();
  const [draft, setDraft] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const symbol = draft.trim().toUpperCase();
    if (!symbol) return;
    setDraft("");
    await addTicker(symbol);
  }

  return (
    <Panel
      title="Watchlist"
      meta={<span className="num text-tiny text-mute">{watchlist.length}/30</span>}
      className="h-full min-h-0"
      bodyClassName="flex flex-col"
    >
      <div className="grid grid-cols-[48px_1fr_70px_62px] gap-1.5 border-b border-hairline px-2.5 py-1.5">
        <span className="label">Sym</span>
        <span className="label justify-self-center">Trend</span>
        <span className="label justify-self-end">Last</span>
        <span className="label justify-self-end">Day</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {watchlist.length === 0 ? (
          <p className="px-3 py-6 text-tiny text-mute">
            Nothing on the watchlist. Add a symbol below to start streaming it.
          </p>
        ) : (
          watchlist.map((ticker) => (
            <WatchlistRow
              key={ticker}
              ticker={ticker}
              update={prices[ticker]}
              points={series[ticker] ?? []}
              selected={selected === ticker}
              onSelect={select}
              onRemove={removeTicker}
            />
          ))
        )}
      </div>

      <form onSubmit={submit} className="flex shrink-0 gap-1.5 border-t border-hairline p-2">
        <label htmlFor="add-ticker" className="sr-only">
          Add ticker
        </label>
        <input
          id="add-ticker"
          value={draft}
          onChange={(event) => setDraft(event.target.value.toUpperCase())}
          placeholder="ADD SYMBOL"
          maxLength={5}
          className="field flex-1 uppercase"
        />
        <button
          type="submit"
          disabled={!draft.trim()}
          className="btn border-hairline bg-raised text-mute hover:border-signal hover:text-signal"
        >
          Add
        </button>
      </form>
    </Panel>
  );
}
