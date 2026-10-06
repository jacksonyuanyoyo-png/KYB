"use client";

import { useSyncExternalStore } from "react";
import { seedDb } from "@/lib/data/seed";
import { isApiMode } from "@/lib/data/source";
import { initialLibrary } from "@/lib/rules/library";
import type { Db } from "@/lib/types";

const STORAGE_KEY = "fcc-kyb-workbench:v1";

let state: Db = seedDb();
let ready = false;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function hydrate() {
  if (ready || loading) return;
  if (isApiMode()) {
    loading = import("@/lib/data/api").then(({ loadRemoteDb }) => loadRemoteDb()).then((loaded) => {
      state = loaded;
      ready = true;
      emit();
    }).catch((error: unknown) => {
      console.error(error);
      ready = true;
      emit();
    }).finally(() => {
      loading = null;
    });
    return;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const loaded = JSON.parse(raw) as Db;
      const library = loaded.ruleLibrary;
      state = library
        ? { ...loaded, ruleLibrary: { ...library, publishedOverrides: library.publishedOverrides ?? {}, draft: library.draft ? { ...library.draft, overrides: library.draft.overrides ?? {} } : null } }
        : { ...loaded, ruleLibrary: initialLibrary() };
    }
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
  }
  ready = true;
  emit();
}

let insights = new Map<string, import("@/lib/insights").CaseInsight>();

export function rememberInsight(id: string, insight: import("@/lib/insights").CaseInsight) {
  insights.set(id, insight);
}

export function readInsight(id: string) {
  return insights.get(id) ?? null;
}

export function getDb(): Db {
  return state;
}

export function replaceDb(next: Db) {
  state = next;
  emit();
}

export function setDb(next: Db) {
  state = next;
  if (!isApiMode()) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  emit();
}

export function resetDb() {
  setDb(seedDb());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useDb(): Db | null {
  return useSyncExternalStore(subscribe, () => (ready ? state : null), () => null);
}
