import { Bell, Menu, Search } from "lucide-react";

import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

export interface HeaderProps {
  onMenuClick: () => void;
}

// The persistent top bar (not a per-page heading — see PageHeader for
// that). Search/notifications are presentational for now; wire them up
// once there's something real to search or be notified about.
export function Header({ onMenuClick }: HeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-4">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={onMenuClick} aria-label="Open navigation">
        <Menu className="h-5 w-5" />
      </Button>

      <div className="relative hidden max-w-sm flex-1 sm:block">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          placeholder="Search projects, documents, agents…"
          className="h-9 w-full rounded-md border border-input bg-background pl-8 pr-3 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      <div className="flex-1 sm:hidden" />

      <Button variant="ghost" size="icon" aria-label="Notifications">
        <Bell className="h-4 w-4" />
      </Button>

      <div className="flex items-center gap-2 pl-2">
        <Avatar name="Suru Sampathi" />
        <div className="hidden text-left leading-tight sm:block">
          <div className="text-sm font-medium">Suru Sampathi</div>
          <div className="text-xs text-muted-foreground">Owner</div>
        </div>
      </div>
    </header>
  );
}
