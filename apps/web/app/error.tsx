"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";

// Applies to every route that doesn't define its own error.tsx (none
// currently do). Next.js requires this to be a Client Component. Before
// this existed, an unhandled error (e.g. the backend being unreachable)
// fell through to Next's generic, unbranded error page with no way to
// retry short of a manual reload.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <AlertTriangle className="h-8 w-8 text-destructive" strokeWidth={1.5} />
      <div>
        <h1 className="text-sm font-semibold">Something went wrong</h1>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">
          {error.message || "An unexpected error occurred loading this page. The backend may be unreachable."}
        </p>
      </div>
      <Button size="sm" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
