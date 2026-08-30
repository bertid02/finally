"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { ChatBubble } from "@/components/ChatBubble";
import { Panel } from "@/components/Panel";
import { useTerminal } from "@/state/TerminalProvider";

const PROMPTS = [
  "How concentrated is my portfolio?",
  "Buy some AAPL",
  "Add PYPL to my watchlist",
];

export function ChatPanel({ onCollapse }: { onCollapse(): void }) {
  const { messages, chatPending, sendChat } = useTerminal();
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, chatPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || chatPending) return;
    setDraft("");
    await sendChat(text);
  }

  return (
    <Panel
      title="Assistant"
      actions={
        <button
          type="button"
          onClick={onCollapse}
          aria-label="Collapse assistant"
          className="label px-1 text-mute hover:text-signal"
        >
          Hide
        </button>
      }
      className="h-full min-h-0"
      bodyClassName="flex flex-col"
    >
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-[12.5px] leading-[1.55] text-mute">
              FinAlly can read your positions, analyse concentration and P&amp;L, and place trades
              for you. Trades it places fill immediately.
            </p>
            <div className="flex flex-col gap-1">
              {PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => setDraft(prompt)}
                  className="border border-hairline px-2 py-1 text-left text-tiny text-mute transition-colors hover:border-edge hover:text-ink"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <ChatBubble key={message.id} message={message} />
        ))}

        {chatPending && (
          <div className="flex items-center gap-2" role="status">
            <span aria-hidden className="h-[6px] w-[6px] animate-pulse rounded-full bg-bloom" />
            <span className="label text-bloom">Thinking</span>
          </div>
        )}

        <div ref={endRef} />
      </div>

      <form onSubmit={submit} className="flex shrink-0 gap-1.5 border-t border-hairline p-2">
        <label htmlFor="chat-input" className="sr-only">
          Message FinAlly
        </label>
        <input
          id="chat-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about your portfolio, or give an order"
          disabled={chatPending}
          className="field flex-1 font-sans text-[12.5px]"
        />
        <button
          type="submit"
          disabled={!draft.trim() || chatPending}
          className="btn border-bloom bg-bloom/25 text-ink hover:enabled:bg-bloom/45"
        >
          Send
        </button>
      </form>
    </Panel>
  );
}
