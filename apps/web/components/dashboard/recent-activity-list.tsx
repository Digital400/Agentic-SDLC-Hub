import Link from "next/link";
import type { ReactNode } from "react";

export interface RecentActivityItem {
  id: string;
  title: string;
  subtitle: string;
  timestamp: string;
  href?: string;
  badge?: ReactNode;
}

export interface RecentActivityListProps {
  items: RecentActivityItem[];
  emptyMessage: string;
}

// Shared list layout for "recent X" widgets (agent runs, artifacts, ...).
// Each row is title + subtitle on the left, a status badge and relative-ish
// timestamp on the right.
export function RecentActivityList({ items, emptyMessage }: RecentActivityListProps) {
  if (items.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <ul className="divide-y divide-border">
      {items.map((item) => {
        const row = (
          <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{item.title}</p>
              <p className="truncate text-xs text-muted-foreground">{item.subtitle}</p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              {item.badge}
              <span className="text-xs text-muted-foreground">{item.timestamp}</span>
            </div>
          </div>
        );

        return (
          <li key={item.id}>
            {item.href ? (
              <Link href={item.href} className="block transition-colors hover:bg-muted/50">
                {row}
              </Link>
            ) : (
              row
            )}
          </li>
        );
      })}
    </ul>
  );
}
