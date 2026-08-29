import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic SDLC Hub",
  description: "Company-wide SDLC workflow platform powered by AI agents.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
