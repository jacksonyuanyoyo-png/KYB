"use client";

import { createContext, useContext } from "react";
import type { CaseInsight } from "@/lib/insights";
import type { CaseRecord, Db, User } from "@/lib/types";

export interface CaseContextValue {
  db: Db;
  user: User;
  record: CaseRecord;
  insight: CaseInsight;
  openAssistant: (prompt?: string) => void;
}

export const CaseContext = createContext<CaseContextValue | null>(null);

export function useCaseContext(): CaseContextValue {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCaseContext must be used inside a case workspace");
  return value;
}
