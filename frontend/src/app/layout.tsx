import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "FinAlly — Trading Console",
  description: "Live market data, a simulated portfolio, and an AI copilot that can trade.",
};

export const viewport: Viewport = {
  themeColor: "#0a0d13",
  width: "device-width",
  initialScale: 1,
};

/**
 * Fonts are linked rather than bundled so a build never depends on reaching
 * Google. Each stack falls back to a system face of the same class.
 *
 * Archivo   — structure: panel titles, column heads, buttons, the wordmark.
 * IBM Plex Mono — every number on screen, so decimal points align in a column.
 * IBM Plex Sans — prose, which in this interface means the chat transcript.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
