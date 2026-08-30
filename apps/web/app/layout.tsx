import type { Metadata } from "next";
import "./globals.css";

import { AppShell } from "@/components/layout/app-shell";
import { api } from "@/lib/api";
import { formatRoleLabel } from "@/lib/format";

export const metadata: Metadata = {
  title: "Agentic SDLC Hub",
  description: "Company-wide SDLC workflow platform powered by AI agents.",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // No login exists yet (see docs/mvp-plan.md) — the header shows the same
  // default-first-user convention used everywhere "acting as" is needed
  // without auth, rather than a hardcoded name that drifts from reality
  // (it previously always said "Suru Sampathi / Owner" even after the
  // permission system introduced real, different roles).
  let currentUser: { name: string; roleLabel: string } | null = null;
  try {
    const users = await api.users.list();
    const user = users[0];
    if (user) currentUser = { name: user.full_name, roleLabel: formatRoleLabel(user.role) };
  } catch {
    // Backend unreachable at render time — the header falls back to a
    // generic label rather than crashing the whole app shell over it.
  }

  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <AppShell currentUser={currentUser}>{children}</AppShell>
      </body>
    </html>
  );
}
