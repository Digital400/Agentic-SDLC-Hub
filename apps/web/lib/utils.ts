import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn/ui helper for merging conditional Tailwind classes.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
