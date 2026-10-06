"use client";

import {
  Building2, CircleAlert, CircleCheck, Feather, Handshake, HeartHandshake, House, Landmark, Layers,
  PiggyBank, ScrollText, Upload, Users, X,
} from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { CaseStatus, EntityType } from "@fcc/domain";
import { initials } from "@/lib/format";
import { STATUS_LABELS, STATUS_TONES, type Tone } from "@/lib/labels";

export function Badge({ tone = "neutral", dot, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return <span className={`badge tone-${tone}${dot ? " badge-dot" : ""}`}>{children}</span>;
}

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <Badge tone={STATUS_TONES[status]} dot>{STATUS_LABELS[status]}</Badge>;
}

export function Tag({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}

const AVATAR_GRADIENTS = [
  "linear-gradient(135deg,#fdba74,#ea580c)", "linear-gradient(135deg,#34d399,#059669)", "linear-gradient(135deg,#f9a8d4,#db2777)",
  "linear-gradient(135deg,#fcd34d,#d97706)", "linear-gradient(135deg,#a78bfa,#7c3aed)", "linear-gradient(135deg,#67e8f9,#0891b2)",
];

export function Avatar({ name, size }: { name: string; size?: "sm" | "lg" }) {
  const index = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % AVATAR_GRADIENTS.length;
  return <span className={`avatar${size ? ` avatar-${size}` : ""}`} style={{ background: AVATAR_GRADIENTS[index] }} aria-hidden>{initials(name)}</span>;
}

export const ENTITY_VISUALS: Record<EntityType, { icon: typeof Building2; color: string; bg: string }> = {
  corporation: { icon: Building2, color: "#15803d", bg: "#e9f8ef" },
  charity: { icon: HeartHandshake, color: "#db2777", bg: "#fdeef6" },
  trust: { icon: ScrollText, color: "#7c3aed", bg: "#f2edff" },
  ipp_rca: { icon: PiggyBank, color: "#0891b2", bg: "#e6f7fb" },
  partnership: { icon: Handshake, color: "#b45309", bg: "#fff4e0" },
  estate: { icon: Feather, color: "#64748b", bg: "#f1f4f8" },
  condo: { icon: House, color: "#0d9488", bg: "#e5f7f5" },
  pooled_fund: { icon: Layers, color: "#4f46e5", bg: "#eef0ff" },
  association: { icon: Users, color: "#ea580c", bg: "#fff1e8" },
  first_nation: { icon: Landmark, color: "#a16207", bg: "#fdf6e3" },
};

export function EntityIcon({ type, className = "entity-icon" }: { type: EntityType; className?: string }) {
  const visual = ENTITY_VISUALS[type];
  const Icon = visual.icon;
  return <span className={className} style={{ color: visual.color, background: visual.bg }}><Icon /></span>;
}

export function Progress({ value, tone }: { value: number; tone?: "green" }) {
  return <div className={`progress${tone ? ` ${tone}` : ""}`} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>;
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" onClick={() => onChange(!checked)} />;
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
}

export function Modal({ title, description, onClose, children, footer, size }: {
  title: string; description?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "lg" | "xl";
}) {
  useEscape(onClose);
  return (
    <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={`modal${size ? ` modal-${size}` : ""}`} role="dialog" aria-modal aria-label={title}>
        <div className="modal-header">
          <div><h2>{title}</h2>{description && <p>{description}</p>}</div>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close"><X /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ onClose, children, label }: { onClose: () => void; children: ReactNode; label: string }) {
  useEscape(onClose);
  return (
    <div className="overlay drawer-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal aria-label={label}>{children}</aside>
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3>{children && <p>{children}</p>}{action && <div style={{ marginTop: 8 }}>{action}</div>}</div>;
}

export function Dropzone({ onFiles, compact, label = "Drop files here or click to browse", hint = "PDF, JPG or PNG · up to 25 MB each" }: {
  onFiles: (files: File[]) => void; compact?: boolean; label?: string; hint?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  return (
    <div
      className={`dropzone${compact ? " dropzone-sm" : ""}${dragging ? " dragging" : ""}`}
      role="button" tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") input.current?.click(); }}
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length) onFiles([...event.dataTransfer.files]); }}
    >
      <Upload />
      <strong style={{ fontSize: compact ? 12 : 13 }}>{label}</strong>
      {!compact && <small>{hint}</small>}
      <input ref={input} type="file" multiple hidden accept=".pdf,.jpg,.jpeg,.png" onChange={(event) => { if (event.target.files?.length) onFiles([...event.target.files]); event.target.value = ""; }} />
    </div>
  );
}

interface Toast { id: number; message: string; kind: "success" | "error" }
const ToastContext = createContext<(message: string, kind?: Toast["kind"]) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, kind: Toast["kind"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((items) => [...items, { id, message, kind }]);
    setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 4200);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((toast) => <div key={toast.id} className={`toast ${toast.kind}`}>{toast.kind === "error" ? <CircleAlert /> : <CircleCheck />}<span>{toast.message}</span></div>)}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/** Runs a mutation, surfacing failures (including version conflicts) as a toast. */
export function useAction() {
  const toast = useToast();
  const [pending, setPending] = useState(false);
  const run = useCallback(async <T,>(work: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setPending(true);
    try {
      const result = await work();
      if (success) toast(success);
      return result;
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : String(cause), "error");
      return undefined;
    } finally {
      setPending(false);
    }
  }, [toast]);
  return { run, pending };
}
