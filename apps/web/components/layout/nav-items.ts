import {
  LayoutDashboard,
  FolderKanban,
  ClipboardCheck,
  FileText,
  Bot,
  BookOpen,
  Activity,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

// The sidebar's fixed set of top-level sections. Order matches the
// product spec: Dashboard, Projects, Reviews, Documents, Agents,
// Knowledge Base, Ops, Settings.
export const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Projects", href: "/projects", icon: FolderKanban },
  { label: "Reviews", href: "/reviews", icon: ClipboardCheck },
  { label: "Documents", href: "/documents", icon: FileText },
  { label: "Agents", href: "/agents", icon: Bot },
  { label: "Knowledge Base", href: "/knowledge-base", icon: BookOpen },
  { label: "Ops", href: "/ops", icon: Activity },
  { label: "Settings", href: "/settings", icon: Settings },
];
