"use client";

// Catches a crash in the root layout itself (rare — layout.tsx already
// wraps its own data fetch in try/catch — but global-error.tsx is the only
// boundary that can catch that tier at all, and must render its own
// <html>/<body> since the root layout is what failed). Kept deliberately
// plain (no shared UI components) since those are exactly what might have
// failed to render in the first place.
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: "12px", fontFamily: "system-ui, sans-serif" }}>
        <h1 style={{ fontSize: "14px", fontWeight: 600 }}>Agentic SDLC Hub failed to load</h1>
        <p style={{ fontSize: "12px", color: "#666" }}>The backend may be unreachable. Try reloading.</p>
        <button
          onClick={reset}
          style={{ fontSize: "12px", padding: "6px 12px", borderRadius: "6px", border: "1px solid #ccc", cursor: "pointer" }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
