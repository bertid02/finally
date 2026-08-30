import type { ReactNode } from "react";

/**
 * Every panel in the console wears the same frame: a hairline box, an uppercase
 * micro-label, and a yellow registration tick in the top-left corner. The tick
 * is the console's signature — it marks a panel as an instrument rather than a
 * card, and it is the only decorative mark in the interface.
 */
export function Panel({
  title,
  meta,
  actions,
  children,
  className = "",
  bodyClassName = "",
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`relative flex min-h-0 flex-col border border-hairline bg-panel ${className}`}>
      <span aria-hidden className="absolute left-0 top-0 h-[2px] w-3 bg-signal" />
      <span aria-hidden className="absolute left-0 top-0 h-3 w-[2px] bg-signal" />

      <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-hairline pl-4 pr-2">
        <div className="flex min-w-0 items-baseline gap-3">
          <h2 className="label">{title}</h2>
          {meta}
        </div>
        {actions}
      </header>

      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
