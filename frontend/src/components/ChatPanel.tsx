/**
 * Talk to the deal in plain English.
 *
 * Every message goes to /api/chat with the current scope, and whatever scope
 * comes back becomes the new state — so "set the rate to 7%" moves the same
 * slider a hand would. The chat and the sliders are two views of one object,
 * never two objects.
 *
 * Each reply carries a small badge saying whether a model was involved. Most
 * messages are handled by the deterministic router and show "no model"; that
 * is the point of the design, and worth being able to see.
 */

import { useEffect, useRef, useState } from "react";
import { type ChatResponse, type DealScope, chat } from "../api/client";

interface Message {
  role: "user" | "assistant";
  text: string;
  steps?: string[];
  planSource?: "router" | "planner";
  usedLlmForPlanning?: boolean;
  usedLlmForNarration?: boolean;
  error?: boolean;
}

interface Props {
  scope: DealScope;
  onScope: (scope: DealScope) => void;
  location: { latitude: number; longitude: number } | null;
  onLocation: (location: { latitude: number; longitude: number }) => void;
}

const SUGGESTIONS = [
  "what's my cash-on-cash",
  "change the rate to 6.5%",
  "what if I put down 25%",
  "does it pass the 70% rule",
  "give me a summary",
];

function Badge({ message }: { message: Message }) {
  if (message.role !== "assistant" || message.error) return null;

  const parts: string[] = [];
  if (message.planSource === "router") parts.push("routed, no model");
  else if (message.usedLlmForPlanning) parts.push("planned by model");
  if (message.usedLlmForNarration) parts.push("worded by model");

  const tone = message.usedLlmForPlanning || message.usedLlmForNarration
    ? "badge-model"
    : "badge-deterministic";

  return (
    <span className={`badge ${tone}`}>
      {parts.join(" · ") || "deterministic"}
      {message.steps && message.steps.length > 0 && (
        <em> — {message.steps.join(", ")}</em>
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

    setDraft("");
    setMessages((prior) => [...prior, { role: "user", text: message }]);
    setBusy(true);

    try {
      const response: ChatResponse = await chat({
        message,
        scope,
        latitude: location?.latitude ?? null,
        longitude: location?.longitude ?? null,
      });

      // The server's scope is the truth. Adopt it, and the sliders follow.
      onScope(response.scope);
      if (response.latitude !== null && response.longitude !== null) {
        onLocation({
          latitude: response.latitude,
          longitude: response.longitude,
        });
      }
      setLlmAvailable(response.llm_available);

      setMessages((prior) => [
        ...prior,
        {
          role: "assistant",
          text: response.reply,
          steps: response.steps,
          planSource: response.plan_source,
          usedLlmForPlanning: response.used_llm_for_planning,
          usedLlmForNarration: response.used_llm_for_narration,
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
          <span className="chat-note">
            No model configured — the deterministic router still answers
            changes, metrics, summaries and valuations.
          </span>
        )}
      </div>

      <div className="chat-log">
        {messages.length === 0 && (
          <p className="chat-empty">
            Try a change or a question. Numbers always come from the engine;
            a model, if one is configured, only chooses steps or rewords.
          </p>
        )}
        {messages.map((message, index) => (
          <div
            key={index}
            className={`chat-msg chat-${message.role} ${
              message.error ? "chat-error" : ""
            }`}
          >
            <div className="chat-text">{message.text}</div>
            <Badge message={message} />
          </div>
        ))}
        {busy && <div className="chat-msg chat-assistant chat-busy">…</div>}
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
          placeholder='e.g. "change the down payment to 25%"'
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
