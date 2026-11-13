import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/shell";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "QA Intelligence", template: "%s · QA Intelligence" },
  description: "Self-hosted, evidence-based software quality engineering platform.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
