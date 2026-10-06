import type { Role } from "@/lib/types";

const IDENTITY_KEY = "fcc-api-identity";

export function isApiMode(): boolean {
  return process.env.NEXT_PUBLIC_DATA_SOURCE === "api";
}

export function identity(): { id: string; role: Role } {
  if (typeof window === "undefined") return { id: "u-advisor", role: "ADVISOR" };
  try {
    const raw = window.sessionStorage.getItem(IDENTITY_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { id?: string; role?: Role };
      if (parsed.id && parsed.role) return { id: parsed.id, role: parsed.role };
    }
  } catch {
    window.sessionStorage.removeItem(IDENTITY_KEY);
  }
  return { id: "u-advisor", role: "ADVISOR" };
}

export function rememberIdentity(id: string, role: Role) {
  window.sessionStorage.setItem(IDENTITY_KEY, JSON.stringify({ id, role }));
}
