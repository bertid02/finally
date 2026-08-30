import type { Config } from "tailwindcss";

/**
 * Design tokens for the FinAlly console.
 *
 * The palette is deliberately narrow: three greys for depth, one hairline,
 * the three brand colours, and exactly two semantic P&L colours. Anything
 * that is not structure, brand, or P&L has no colour of its own.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#0a0d13", // page ground — deeper than any panel
        panel: "#111722", // panel surface
        raised: "#161d2a", // row hover, inputs, chat bubbles
        hairline: "#222b3b", // the 1px grid that holds the console together
        edge: "#2f3a4d", // hairline, emphasised (focus, active borders)
        signal: "#ecad0a", // brand yellow — selection and headline figures
        wire: "#209dd7", // brand blue — data lines and links
        bloom: "#753991", // brand purple — submit / the AI's voice
        up: "#33d69f",
        down: "#ff5d6c",
        ink: "#dbe3ef",
        mute: "#7a8799",
      },
      fontFamily: {
        display: ['"Archivo"', "system-ui", "sans-serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        micro: ["10px", { lineHeight: "12px", letterSpacing: "0.12em" }],
        tiny: ["11px", { lineHeight: "14px" }],
        data: ["12px", { lineHeight: "16px" }],
        readout: ["28px", { lineHeight: "30px", letterSpacing: "-0.02em" }],
      },
      borderRadius: { none: "0", sm: "2px", DEFAULT: "2px", md: "3px" },
      keyframes: {
        flashUp: {
          "0%": { backgroundColor: "rgba(51,214,159,0.22)" },
          "100%": { backgroundColor: "rgba(51,214,159,0)" },
        },
        flashDown: {
          "0%": { backgroundColor: "rgba(255,93,108,0.22)" },
          "100%": { backgroundColor: "rgba(255,93,108,0)" },
        },
        pulse: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: {
        flashUp: "flashUp 550ms ease-out",
        flashDown: "flashDown 550ms ease-out",
        pulse: "pulse 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
