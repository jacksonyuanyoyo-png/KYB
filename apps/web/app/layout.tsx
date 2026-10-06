import type { Metadata } from "next";
import "@xyflow/react/dist/base.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Complex Account Workbench · Fidelity Clearing Canada",
  description: "Internal WI/PI workbench for complex entity account opening",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
