"use client";

import { useEffect, type ReactNode } from "react";
import { hydrate, useDb } from "@/lib/data/store";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { ToastProvider } from "@/components/ui";

export function AppShell({ children }: { children: ReactNode }) {
  const db = useDb();
  useEffect(() => { hydrate(); }, []);
  return (
    <ToastProvider>
      <div className="app-shell">
        <Sidebar />
        <div className="main">
          <Topbar />
          {db ? children : <PageSkeleton />}
        </div>
      </div>
    </ToastProvider>
  );
}

function PageSkeleton() {
  return (
    <div className="page">
      <div className="skeleton" style={{ height: 28, width: 260, marginBottom: 20 }} />
      <div className="grid-4">{[0, 1, 2, 3].map((key) => <div key={key} className="skeleton" style={{ height: 110 }} />)}</div>
      <div className="skeleton" style={{ height: 320, marginTop: 16 }} />
    </div>
  );
}
