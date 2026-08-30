import { TradeChip, WatchlistChip } from "@/components/ActionChip";
import type { ChatMessage } from "@/lib/types";

export function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const trades = message.actions?.trades ?? [];
  const changes = message.actions?.watchlist_changes ?? [];

  return (
    <div className={`flex flex-col gap-1.5 ${isUser ? "items-end" : "items-start"}`}>
      <span className="label">{isUser ? "You" : "FinAlly"}</span>
      <div
        className={`max-w-[92%] border px-2.5 py-2 text-[12.5px] leading-[1.5] ${
          isUser
            ? "border-hairline bg-raised text-ink"
            : "border-bloom/45 bg-bloom/10 text-ink"
        }`}
      >
        {message.content}
      </div>

      {(trades.length > 0 || changes.length > 0) && (
        <div className="flex w-full flex-col gap-1">
          {trades.map((action, index) => (
            <TradeChip key={`trade-${index}`} action={action} />
          ))}
          {changes.map((action, index) => (
            <WatchlistChip key={`watch-${index}`} action={action} />
          ))}
        </div>
      )}
    </div>
  );
}
