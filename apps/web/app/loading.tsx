import { Loader2 } from "lucide-react";

// Applies to every route that doesn't define its own loading.tsx (none
// currently do) — Next.js shows this automatically while a server
// component's data fetch is in flight, replacing what used to be a blank
// page with no feedback at all.
export default function Loading() {
  return (
    <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-muted-foreground">
      <Loader2 className="h-6 w-6 animate-spin" />
      <p className="text-sm">Loading…</p>
    </div>
  );
}
