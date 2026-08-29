import * as React from "react";

import { cn } from "@/lib/utils";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  name: string;
}

// Dependency-free initials avatar. Swap for an <img>-backed Radix Avatar
// later if real profile pictures are ever stored.
function Avatar({ name, className, ...props }: AvatarProps) {
  return (
    <div
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-medium text-secondary-foreground",
        className
      )}
      title={name}
      {...props}
    >
      {initials(name)}
    </div>
  );
}

export { Avatar };
