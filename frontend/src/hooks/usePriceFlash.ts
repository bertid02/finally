"use client";

import { useEffect, useRef, useState } from "react";

import type { PriceUpdate } from "@/lib/types";

/**
 * Returns the animation class for a price cell, or "" when nothing is flashing.
 *
 * The direction comes from `update.direction` (tick-over-tick) — that is the
 * only thing tick-over-tick data is for. `change_percent` is never rendered.
 */
export function usePriceFlash(update: PriceUpdate | undefined, holdMs = 550): string {
  const [flash, setFlash] = useState("");
  const lastPrice = useRef<number | null>(null);

  useEffect(() => {
    if (!update) return;
    const previous = lastPrice.current;
    lastPrice.current = update.price;
    if (previous === null || previous === update.price) return;

    setFlash(update.price > previous ? "animate-flashUp" : "animate-flashDown");
    const timer = setTimeout(() => setFlash(""), holdMs);
    return () => clearTimeout(timer);
  }, [update, holdMs]);

  return flash;
}
