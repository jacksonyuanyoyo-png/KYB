"use client";

import { LayoutGrid, Plus, Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import type { Party } from "@fcc/domain";
import { AddPartyModal } from "@/components/case/AddPartyModal";
import { useCaseContext } from "@/components/case/CaseContext";
import { OwnershipGraph } from "@/components/case/OwnershipGraph";
import { ParseDocumentsModal } from "@/components/case/ParseDocumentsModal";
import { PartyEditor, StructureOverview } from "@/components/case/PartyInspector";
import { useAction } from "@/components/ui";
import { AI_MODELS } from "@/lib/ai/mock";
import { applyAiParties, recordAiRejection, updateParties, uploadDocuments } from "@/lib/data/actions";
import { describeChanges, removeSubtree } from "@/lib/parties";

export default function OwnershipPage() {
  return <Suspense><OwnershipStep /></Suspense>;
}

function OwnershipStep() {
  const { record, insight, user } = useCaseContext();
  const params = useSearchParams();
  const { run, pending } = useAction();
  const [selectedId, setSelectedId] = useState<string | null>(params.get("node"));
  const [addParentId, setAddParentId] = useState<string | null>(null);
  const [parseOpen, setParseOpen] = useState(params.get("parse") === "1");
  const [layoutKey, setLayoutKey] = useState(0);

  const editable = (record.status === "BUILDING" || record.status === "DOCS_REQUESTED" || record.status === "RETURNED") && user.role !== "COMPLIANCE";
  const root = record.parties.find((party) => party.parentId === null);
  const selected = record.parties.find((party) => party.id === selectedId) ?? null;
  const addParent = record.parties.find((party) => party.id === addParentId) ?? null;
  const identifyIds = useMemo(() => new Set(insight.identify.map((party) => party.id)), [insight.identify]);
  const onAdd = useCallback((parentId: string) => setAddParentId(parentId), []);
  const addTarget = selected?.kind === "ENTITY" ? selected : root;

  async function addParty(party: Party) {
    const parent = record.parties.find((item) => item.id === party.parentId);
    const result = await run(() => updateParties(record, [...record.parties, party], `Added ${party.legalName} under ${parent?.legalName ?? "root"}`), `${party.legalName} added`);
    if (result) { setAddParentId(null); setSelectedId(party.id); }
  }

  async function saveParty(next: Party) {
    const before = record.parties.find((item) => item.id === next.id);
    if (!before) return;
    const changes = describeChanges(before, next);
    await run(() => updateParties(record, record.parties.map((item) => (item.id === next.id ? next : item)), `Updated ${next.legalName}`, changes), "Changes saved");
  }

  async function removeParty(party: Party) {
    const result = await run(() => updateParties(record, removeSubtree(record.parties, party.id), `Removed ${party.legalName} from the structure`), `${party.legalName} removed`);
    if (result) setSelectedId(party.parentId);
  }

  async function applyExtraction(parties: Party[], files: Array<Pick<File, "name" | "size" | "type">>, summary: string) {
    const updated = await run(() => applyAiParties(record, [...record.parties, ...parties], summary, AI_MODELS.extract));
    if (!updated) return;
    await run(() => uploadDocuments(updated, "formation", files), `${parties.length} parties added from documents`);
    setParseOpen(false);
    setLayoutKey((value) => value + 1);
  }

  async function rejectExtraction(files: Array<Pick<File, "name">>) {
    await run(() => recordAiRejection(record, `Discarded parties extracted from ${files.map((file) => file.name).join(", ")}`, AI_MODELS.extract), "Suggestions discarded");
    setParseOpen(false);
  }

  return (
    <div className="workspace">
      <div className="workspace-main flush">
        <OwnershipGraph
          parties={record.parties} issues={insight.ownershipIssues} effective={insight.effective} identifyIds={identifyIds}
          selectedId={selectedId} editable={editable} layoutKey={layoutKey} onSelect={setSelectedId} onAdd={onAdd}
          toolbar={<>
            {editable && (
              <div className="graph-pill">
                <button className="btn btn-sm btn-ghost" onClick={() => addTarget && setAddParentId(addTarget.id)}><Plus />Add under {addTarget?.parentId === null ? "root" : addTarget?.legalName.split(" ")[0]}</button>
                <button className="btn btn-sm btn-ai" onClick={() => setParseOpen(true)}><Sparkles />Parse formation documents</button>
              </div>
            )}
            <div className="graph-pill" style={{ padding: "4px 10px", gap: 12, fontSize: 12 }}>
              <span><strong>{record.parties.length - 1}</strong> <span className="muted">parties</span></span>
              <span><strong style={{ color: "var(--primary)" }}>{insight.identify.length}</strong> <span className="muted">to identify</span></span>
              <span><strong style={{ color: insight.ownershipIssues.length ? "var(--amber)" : "var(--green)" }}>{insight.ownershipIssues.length}</strong> <span className="muted">issues</span></span>
            </div>
            <span className="spacer" />
            <div className="graph-pill">
              <button className="btn btn-sm btn-ghost" onClick={() => setLayoutKey((value) => value + 1)}><LayoutGrid />Auto layout</button>
            </div>
          </>}
        />
      </div>
      <aside className="inspector">
        {selected ? (
          <PartyEditor
            party={selected} editable={editable} pending={pending}
            onClose={() => setSelectedId(null)} onSave={(next) => void saveParty(next)} onRemove={() => void removeParty(selected)}
            onAdd={() => setAddParentId(selected.id)} onSelect={setSelectedId}
          />
        ) : <StructureOverview onSelect={setSelectedId} />}
      </aside>
      {addParent && (
        <AddPartyModal
          parent={addParent} pending={pending}
          siblingsTotal={record.parties.filter((party) => party.parentId === addParent.id).reduce((sum, party) => sum + party.ownershipPercent, 0)}
          onClose={() => setAddParentId(null)} onAdd={(party) => void addParty(party)}
        />
      )}
      {parseOpen && <ParseDocumentsModal record={record} pending={pending} onClose={() => setParseOpen(false)} onApply={(parties, files, summary) => void applyExtraction(parties, files, summary)} onReject={(files) => void rejectExtraction(files)} />}
    </div>
  );
}
