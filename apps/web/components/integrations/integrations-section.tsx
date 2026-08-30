"use client";

import { useState } from "react";
import { Info } from "lucide-react";

import { IntegrationCard } from "@/components/integrations/integration-card";
import { api, ApiError } from "@/lib/api";
import type { IntegrationItem } from "@/lib/types";

// Settings > Integrations. Every card's "Connect" button calls the real
// backend endpoint — it isn't wired to a no-op — but that endpoint is
// itself a documented placeholder (501 Not Implemented) until a real MCP
// tool exists, so the banner below is accurate, not a fake error.
export function IntegrationsSection({ integrations: initial }: { integrations: IntegrationItem[] }) {
  const [integrations] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function handleConnect(id: string) {
    setBusyId(id);
    setBanner(null);
    try {
      await api.integrations.connect(id);
    } catch (err) {
      setBanner(
        err instanceof ApiError
          ? err.message
          : "Connecting a real integration isn't implemented yet."
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {integrations.map((integration) => (
          <IntegrationCard
            key={integration.id}
            integration={integration}
            onConnect={handleConnect}
            busy={busyId === integration.id}
          />
        ))}
      </div>

      {banner ? (
        <p className="mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          {banner}
        </p>
      ) : null}
    </div>
  );
}
