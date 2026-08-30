"use client";

import { useState } from "react";

import { ChatPanel } from "@/components/ChatPanel";
import { DetailChart } from "@/components/DetailChart";
import { Header } from "@/components/Header";
import { Heatmap } from "@/components/Heatmap";
import { PnLChart } from "@/components/PnLChart";
import { PositionsTable } from "@/components/PositionsTable";
import { TradeBar } from "@/components/TradeBar";
import { Watchlist } from "@/components/Watchlist";
import { TerminalProvider } from "@/state/TerminalProvider";

/**
 * The console is one screen. It fills the viewport exactly and never scrolls as
 * a page — each panel scrolls inside its own frame, the way a real trading
 * terminal behaves. Below 1024px the columns stack and the page scrolls, so a
 * tablet still gets every panel at a usable size.
 */
export default function Page() {
  const [chatOpen, setChatOpen] = useState(true);

  return (
    <TerminalProvider>
      <div className="flex h-screen flex-col bg-void">
        <Header />

        <main className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-1 lg:flex-row lg:overflow-hidden">
          <div className="h-[320px] shrink-0 lg:h-auto lg:w-[318px]">
            <Watchlist />
          </div>

          <div className="flex min-w-0 shrink-0 flex-col gap-1 lg:min-h-0 lg:flex-1 lg:shrink">
            <div className="h-[320px] shrink-0 lg:h-auto lg:min-h-0 lg:flex-[8] lg:shrink">
              <DetailChart />
            </div>

            <div className="flex shrink-0 flex-col gap-1 sm:h-[260px] sm:flex-row lg:h-auto lg:min-h-0 lg:flex-[5] lg:shrink">
              <div className="h-[240px] min-h-0 sm:h-auto sm:flex-1">
                <Heatmap />
              </div>
              <div className="h-[240px] min-h-0 sm:h-auto sm:flex-1">
                <PnLChart />
              </div>
            </div>

            <div className="h-[260px] shrink-0 lg:h-auto lg:min-h-0 lg:flex-[4] lg:shrink">
              <PositionsTable />
            </div>
          </div>

          {chatOpen ? (
            <div className="h-[420px] shrink-0 lg:h-auto lg:w-[350px]">
              <ChatPanel onCollapse={() => setChatOpen(false)} />
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setChatOpen(true)}
              className="shrink-0 border border-hairline bg-panel px-2 py-3 text-mute transition-colors hover:border-signal hover:text-signal lg:w-9"
            >
              <span className="label lg:[writing-mode:vertical-rl]">Assistant</span>
            </button>
          )}
        </main>

        <TradeBar />
      </div>
    </TerminalProvider>
  );
}
