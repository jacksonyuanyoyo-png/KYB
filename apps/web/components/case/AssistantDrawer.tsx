"use client";

import { ArrowUp, Check, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useCaseContext } from "@/components/case/CaseContext";
import { Drawer, useAction } from "@/components/ui";
import { AI_MODELS, explainGaps, parseInstruction, suggestEntityType, type PartyProposal } from "@/lib/ai/mock";
import { applyAiParties, recordAiRejection } from "@/lib/data/actions";
import { apiAskAssistant } from "@/lib/data/api";
import { isApiMode } from "@/lib/data/source";
import { formatPercent, uid } from "@/lib/format";
import { isCollected } from "@/lib/insights";
import { ENTITY_LABELS } from "@/lib/labels";

interface Message {
  id: string;
  role: "user" | "bot";
  text: string;
  proposal?: PartyProposal;
  resolved?: "applied" | "discarded";
}

const PROMPTS = ["Why can't I continue?", "Which documents are still outstanding?", "Which entity type should this be?", "Alice Chen holds 60% and is a director"];

export function AssistantDrawer({ initialPrompt, onClose }: { initialPrompt?: string; onClose: () => void }) {
  const { record, insight, user } = useCaseContext();
  const { run, pending } = useAction();
  const [messages, setMessages] = useState<Message[]>([{
    id: "hello", role: "bot",
    text: `I can see ${record.legalName}: ${record.parties.length - 1} parties, ${insight.ownershipIssues.length + insight.detailsGaps.length} open structure issues, ${insight.collected}/${insight.checklist.length} documents collected.\n\nAsk me what's missing, or describe an owner in plain language and I'll draft the fields for you to confirm.`,
  }]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [modelName, setModelName] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);
  const editable = record.status !== "APPROVED" && record.status !== "READY_FOR_COMPLIANCE" && user.role !== "COMPLIANCE";

  useEffect(() => { body.current?.scrollTo({ top: body.current.scrollHeight, behavior: "smooth" }); }, [messages, thinking]);
  useEffect(() => {
    if (initialPrompt && !sentInitial.current) { sentInitial.current = true; void send(initialPrompt); }
  }, [initialPrompt]); // eslint-disable-line react-hooks/exhaustive-deps

  async function respond(text: string): Promise<Omit<Message, "id" | "role">> {
    const proposal = parseInstruction(text, record);
    if (proposal) {
      const parent = record.parties.find((party) => party.legalName === proposal.parentName);
      const total = record.parties.filter((party) => party.parentId === parent?.id).reduce((sum, party) => sum + party.ownershipPercent, 0) + proposal.ownershipPercent;
      const warning = total > 100 ? `\n\nHeads up: interests under ${proposal.parentName} would total ${formatPercent(total)}. Adjust another holder after applying.` : "";
      return editable
        ? { text: `Here's what I would add under ${proposal.parentName.replace(/\.$/, "")}. Nothing is saved until you confirm.${warning}`, proposal }
        : { text: "This case is read-only at its current status, so I can't propose changes to the structure." };
    }
    if (/why|continue|missing|blocked|stuck|gap/i.test(text)) {
      const gaps = explainGaps(record, insight);
      if (!gaps.length) {
        const outstanding = insight.checklist.length - insight.collected;
        return { text: outstanding ? `The structure and account details are complete. ${outstanding} document${outstanding === 1 ? " is" : "s are"} still outstanding on the checklist.` : "Nothing is blocking this case. You can submit it for compliance review." };
      }
      return { text: gaps.map((gap, index) => `${index + 1}. ${gap.title}\n${gap.detail}\n→ ${gap.action}`).join("\n\n") };
    }
    if (/entity type|subtype|which type|classify/i.test(text)) {
      const suggestion = await suggestEntityType(record.legalName, "");
      if (!suggestion) return { text: "The name alone doesn't tell me enough. Check the formation document: articles of incorporation mean Corporation, a trust deed means Formal Trust, a partnership agreement means Partnership / LP." };
      const same = suggestion.type === record.entityType;
      return { text: `${same ? "The current choice looks right" : "Worth double-checking"}: I'd classify this as ${ENTITY_LABELS[suggestion.type]} (${Math.round(suggestion.confidence * 100)}% confidence).\n\n${suggestion.reasons.join("\n")}` };
    }
    if (/document|checklist|outstanding|need|collect/i.test(text)) {
      if (insight.ownershipIssues.length || insight.detailsGaps.length) return { text: "The checklist isn't generated yet — the ownership structure and account details must be complete first. Ask me \"why can't I continue?\" for specifics." };
      const missing = insight.checklist.filter((item) => !isCollected(record, item.id));
      return { text: missing.length ? `Still outstanding:\n${missing.map((item) => `• ${item.name}${item.conditional ? " (if applicable)" : ""} — ${item.reason}`).join("\n")}` : "Every checklist item is collected." };
    }
    return { text: "I can:\n• explain why the case can't move to the next step\n• list outstanding documents and why each is required\n• check the entity subtype\n• turn a sentence like \"Jane Doe owns 30% and is a director\" into a draft node\n\nI can't clear PEP status, sanctions or FATCA classification — those come from approved screening systems and Compliance." };
  }

  async function send(text: string) {
    if (!text.trim()) return;
    setMessages((current) => [...current, { id: uid("m"), role: "user", text }]);
    setInput("");
    setThinking(true);
    if (isApiMode()) {
      const botId = uid("m");
      let started = false;
      try {
        const reply = await apiAskAssistant(record.id, text, (chunk) => {
          setThinking(false);
          if (!started) {
            started = true;
            setMessages((current) => [...current, { id: botId, role: "bot", text: chunk }]);
            return;
          }
          setMessages((current) => current.map((item) => (item.id === botId ? { ...item, text: item.text + chunk } : item)));
        });
        setModelName(reply.model);
        setMessages((current) => {
          const next = { id: botId, role: "bot" as const, text: reply.text, proposal: reply.proposal ?? undefined };
          return started ? current.map((item) => (item.id === botId ? next : item)) : [...current, next];
        });
      } catch (error) {
        const note = error instanceof Error ? error.message : "The assistant is unavailable.";
        setMessages((current) => started ? current.map((item) => (item.id === botId ? { ...item, text: note } : item)) : [...current, { id: botId, role: "bot", text: note }]);
      }
      setThinking(false);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    const reply = await respond(text);
    setThinking(false);
    setMessages((current) => [...current, { id: uid("m"), role: "bot", ...reply }]);
  }

  async function apply(message: Message) {
    const proposal = message.proposal!;
    const parent = record.parties.find((party) => party.legalName === proposal.parentName) ?? record.parties.find((party) => party.parentId === null)!;
    const result = await run(() => applyAiParties(record, [...record.parties, {
      id: uid("p"), parentId: parent.id, kind: "PERSON", legalName: proposal.legalName, title: proposal.title, country: "Canada",
      ownershipPercent: proposal.ownershipPercent, isController: proposal.isController, isSigningAuthority: proposal.isSigningAuthority,
      isUsPerson: proposal.isUsPerson, isPepHio: proposal.isPepHio,
    }], `Assistant added ${proposal.legalName} under ${parent.legalName}`, AI_MODELS.assistant), `${proposal.legalName} added to the graph`);
    if (result) setMessages((current) => current.map((item) => (item.id === message.id ? { ...item, resolved: "applied" } : item)));
  }

  async function discard(message: Message) {
    await run(() => recordAiRejection(record, `Discarded assistant proposal for ${message.proposal!.legalName}`, AI_MODELS.assistant));
    setMessages((current) => current.map((item) => (item.id === message.id ? { ...item, resolved: "discarded" } : item)));
  }

  const submit = (event: FormEvent) => { event.preventDefault(); void send(input); };

  return (
    <Drawer onClose={onClose} label="Case assistant">
      <div className="assistant-head">
        <span className="assistant-mark"><Sparkles /></span>
        <div style={{ flex: 1 }}><strong>Case assistant</strong><div className="small muted">{record.reference} · reads this case only</div></div>
        <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close"><X /></button>
      </div>
      <div className="assistant-body" ref={body}>
        {messages.map((message) => (
          <div key={message.id} className={`msg ${message.role}`}>
            {message.text}
            {message.proposal && (
              <div className="proposal">
                <div className="proposal-head">Proposed change</div>
                <dl>
                  <dt>Name</dt><dd>{message.proposal.legalName}</dd>
                  <dt>Under</dt><dd>{message.proposal.parentName}</dd>
                  <dt>Ownership</dt><dd>{formatPercent(message.proposal.ownershipPercent)}</dd>
                  <dt>Role</dt><dd>{message.proposal.title}</dd>
                  <dt>Controller</dt><dd>{message.proposal.isController ? "Yes" : "No"}</dd>
                  <dt>Signing authority</dt><dd>{message.proposal.isSigningAuthority ? "Yes" : "No"}</dd>
                  {message.proposal.isUsPerson && <><dt>US person</dt><dd>Yes</dd></>}
                  {message.proposal.isPepHio && <><dt>PEP / HIO</dt><dd style={{ color: "var(--red)" }}>Flagged</dd></>}
                </dl>
                <div className="proposal-actions">
                  {message.resolved ? <span className="small" style={{ color: message.resolved === "applied" ? "var(--green)" : "var(--text-3)" }}>{message.resolved === "applied" ? <><Check size={12} /> Added to graph</> : "Discarded"}</span> : (
                    <>
                      <button className="btn btn-primary btn-sm" disabled={pending} onClick={() => void apply(message)}>Apply</button>
                      <button className="btn btn-ghost btn-sm" disabled={pending} onClick={() => void discard(message)}>Discard</button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {thinking && <div className="msg bot muted">Thinking…</div>}
      </div>
      <div className="prompts">{PROMPTS.map((prompt) => <button key={prompt} className="prompt-chip" onClick={() => void send(prompt)}>{prompt}</button>)}</div>
      <form className="assistant-input" onSubmit={submit}>
        <input className="input" placeholder="Ask about this case…" value={input} onChange={(event) => setInput(event.target.value)} />
        <button className="btn btn-primary btn-icon" disabled={!input.trim() || thinking} aria-label="Send"><ArrowUp /></button>
      </form>
      <div className="assistant-foot">{modelName ? <>Model <span className="mono">{modelName}</span>. </> : null}Suggestions are logged with your decision and rule version {record.ruleVersion}.</div>
    </Drawer>
  );
}
