/**
 * Talk to the deal.
 *
 * With a model configured this is a real assistant: it calls the finance
 * engine and the comp model as tools, sees what they return, and explains.
 * Without one, the deterministic router still handles exact commands and says
 * so. Either way, the scope that comes back becomes the new state, so a change
 * made in chat moves the same slider a hand would.
 *
 * Each reply carries a badge saying what actually ran: which tools the model
 * called, or that no model was involved. A reply that cited a figure nothing
 * computed is replaced by the engine's own numbers and flagged, so the promise
 * "it never invents a figure" is something you can see being kept.
 */

import { useEffect, useRef, useState } from "react";
import {
  type ChatHistoryTurn,
  type ChatResponse,
  type DealScope,
  chat,
} from "../api/client";

interface Message {
  role: "user" | "assistant";
  text: string;
  usedLlm?: boolean;
  toolCalls?: string[];
  steps?: string[];
  provenanceOk?: boolean;
  rejectedFigures?: string[];
  error?: boolean;
}

interface Props {
  scope: DealScope;
  onScope: (scope: DealScope) => void;
  location: { latitude: number; longitude: number } | null;
  onLocation: (location: { latitude: number; longitude: number }) => void;
}

const SUGGESTIONS = [
  "explain what all these numbers mean",
  "is this a good deal?",
  "what does DSCR mean and is mine ok?",
  "what if I put down 25%",
  "what's the weakest part of this deal?",
];

const TOOL_LABELS: Record<string, string> = {
  get_deal: "read the deal",
  set_field: "changed a field",
  value_from_comps: "valued from comps",
  list_comps: "listed comps",
};

function Badge({ message }: { message: Message }) {
  if (message.role !== "assistant" || message.error) return null;

  if (!message.usedLlm) {
    return (
      <span className="badge badge-deterministic">
        no model
        {message.steps && message.steps.length > 0 && (
          <em> — {message.steps.join(", ")}</em>
        )}
      </span>
    );
  }

  const tools = (message.toolCalls ?? []).map((t) => TOOL_LABELS[t] ?? t);
  return (
    <span className={`badge ${message.provenanceOk === false ? "badge-bad" : "badge-model"}`}>
      {message.provenanceOk === false
        ? "reply replaced — cited unproduced figures"
        : "assistant"}
      {tools.length > 0 && <em> — {tools.join(", ")}</em>}
      {message.rejectedFigures && message.rejectedFigures.length > 0 && (
        <em> · corrected: {message.rejectedFigures.join(", ")}</em>
      )}
    </span>
  );
}

export function ChatPanel({ scope, onScope, location, onLocation }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [llmAvailable, setLlmAvailable] = useState<boolean | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;

    // What the assistant should remember: every prior exchange, as text.
    const history: ChatHistoryTurn[] = messages
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.text }));

    setDraft("");
    setMessages((prior) => [...prior, { role: "user", text: message }]);
    setBusy(true);

    try {
      const response: ChatResponse = await chat({
        message,
        scope,
        latitude: location?.latitude ?? null,
        longitude: location?.longitude ?? null,
        history,
      });

      onScope(response.scope);
      if (response.latitude !== null && response.longitude !== null) {
        onLocation({ latitude: response.latitude, longitude: response.longitude });
      }
      setLlmAvailable(response.llm_available);

      setMessages((prior) => [
        ...prior,
        {
          role: "assistant",
          text: response.reply,
          usedLlm: response.used_llm,
          toolCalls: response.tool_calls,
          steps: response.steps,
          provenanceOk: response.provenance_ok,
          rejectedFigures: response.rejected_figures,
        },
      ]);
    } catch (err) {
      setMessages((prior) => [
        ...prior,
        {
          role: "assistant",
          text: err instanceof Error ? err.message : "Something went wrong.",
          error: true,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel chat">
      <div className="chat-head">
        <h2>Ask the deal</h2>
        {llmAvailable === false && (
          <span className="chat-note chat-note-warn">
            No model configured. Set <code>ANTHROPIC_API_KEY</code> and restart
            the API to turn on the assistant. Direct commands still work.
          </span>
        )}
      </div>

      <div className="chat-log">
        {messages.length === 0 && (
          <p className="chat-empty">
            Ask anything about this deal. The assistant reads the numbers from
            the engine and explains them; it cannot make a figure up.
          </p>
        )}
        {messages.map((message, index) => (
          <div
            key={index}
            className={`chat-msg chat-${message.role} ${message.error ? "chat-error" : ""}`}
          >
            <div className="chat-text">{message.text}</div>
            <Badge message={message} />
          </div>
        ))}
        {busy && <div className="chat-msg chat-assistant chat-busy">thinking…</div>}
        <div ref={bottom} />
      </div>

      <div className="chat-suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            className="chip"
            onClick={() => void send(suggestion)}
            disabled={busy}
          >
            {suggestion}
          </button>
        ))}
      </div>

      <form
        className="chat-input"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about the deal…"
          disabled={busy}
          maxLength={2000}
        />
        <button type="submit" className="primary" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
