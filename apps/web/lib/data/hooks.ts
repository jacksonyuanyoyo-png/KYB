"use client";

import { useMemo } from "react";
import { useDb, readInsight } from "@/lib/data/store";
import { analyze, type CaseInsight } from "@/lib/insights";
import { isApiMode } from "@/lib/data/source";
import type { CaseRecord, Db, RuleLibraryState, User } from "@/lib/types";

export function useSession(): { db: Db; user: User } | null {
  const db = useDb();
  return useMemo(() => {
    if (!db) return null;
    return { db, user: db.users.find((item) => item.id === db.sessionUserId) ?? db.users[0]! };
  }, [db]);
}

export function caseInsight(record: CaseRecord, library: RuleLibraryState): CaseInsight {
  if (isApiMode()) {
    const stored = readInsight(record.id);
    if (stored) return stored;
  }
  return analyze(record, library);
}

export function useCase(caseId: string) {
  const db = useDb();
  const record = db?.cases.find((item) => item.id === caseId) ?? null;
  const insight = useMemo(() => (record && db ? caseInsight(record, db.ruleLibrary) : null), [record, db]);
  return { db, record, insight };
}

export function userName(db: Db, userId: string): string {
  return db.users.find((user) => user.id === userId)?.name ?? "Unknown user";
}
