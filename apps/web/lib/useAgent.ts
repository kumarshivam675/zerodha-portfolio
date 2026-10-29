"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatBubble, type Conversation, type Holding } from "./api";

/**
 * Mirrors app/services/events.py. `agent` names the pipeline agent that produced the event
 * (Analyse runs several); chat leaves it out.
 */
type AgentEvent = { agent?: string } & (
  | { type: "conversation"; id: string }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_start"; id: string; name: string }
  | { type: "tool_input"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; is_error: boolean }
  | { type: "usage"; model: string; cost_usd: number | null; duration_ms: number }
  | { type: "retry"; reason: string }
  | { type: "stage"; label: string }
  | { type: "note"; text: string }
  | { type: "done"; stop_reason: string }
  | { type: "error"; message: string }
);

export const AGENT_LABEL: Record<string, string> = {
  company: "Company research",
  technicals: "Technical research",
  macro: "Macro research",
  fund: "Fund research",
  strategy: "Strategy",
  risk: "Risk",
};

export const TOOL_STATUS: Record<string, string> = {
  get_portfolio: "Reading your portfolio",
  list_snapshots: "Listing snapshots",
  get_snapshot: "Reading a snapshot",
  get_price_history: "Loading price history",
  get_technicals: "Computing technicals",
  get_stock_info: "Fetching fundamentals",
  query_database: "Querying the database",
  web_search: "Searching the web",
  web_fetch: "Reading a web page",
  code_execution: "Analysing data",
};

/** Move the in-progress reasoning summary into the bubble's notes. */
function flushThinking(b: ChatBubble): ChatBubble {
  const t = b.thinking?.trim();
  return t ? { ...b, thinking: "", notes: [...(b.notes ?? []), t] } : b;
}

export const holdingLabel = (h: Holding) => (h.kind === "mf" ? h.name ?? h.tradingsymbol : h.tradingsymbol);

/** One agent conversation: loads history, sends messages, starts analyses, and streams replies. */
export function useAgentConversation({
  onError,
  onTurnComplete,
}: {
  onError: (message: string) => void;
  onTurnComplete?: () => void;
}) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  /** Estimated API cost of the current or last run, from its usage events. */
  const [cost, setCost] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => () => abortRef.current?.abort(), []);

  const patchAssistant = useCallback((fn: (b: ChatBubble) => ChatBubble) => {
    setMessages((ms) => {
      const last = ms[ms.length - 1];
      if (!last || last.role !== "assistant") return [...ms, fn({ role: "assistant", text: "", tools: [] })];
      return [...ms.slice(0, -1), fn(last)];
    });
  }, []);

  const handle = useCallback(
    (event: AgentEvent) => {
      switch (event.type) {
        case "conversation":
          setConversationId(event.id);
          break;
        case "text":
          setStatus(null);
          patchAssistant((b) => ({ ...flushThinking(b), text: b.text + event.text }));
          break;
        case "thinking":
          setStatus("Thinking");
          patchAssistant((b) => ({ ...b, thinking: (b.thinking ?? "") + event.text }));
          break;
        case "tool_start": {
          const what = TOOL_STATUS[event.name] ?? event.name;
          const who = event.agent && AGENT_LABEL[event.agent];
          setStatus(who ? `${who} · ${what}` : what);
          break;
        }
        case "stage":
          setStatus(event.label);
          break;
        case "usage":
          setCost((c) => c + (event.cost_usd ?? 0));
          break;
        case "retry":
          // The turn is re-issued from scratch: drop its partial output.
          setStatus("Retrying after a temporary API error");
          patchAssistant((b) => ({ ...b, text: "", thinking: "" }));
          break;
        case "note":
          patchAssistant((b) => ({ ...b, notes: [...(b.notes ?? []), event.text] }));
          break;
        case "tool_input":
          // Reasoning and text written before a tool call are progress notes, not the answer.
          patchAssistant((b) => {
            const f = flushThinking(b);
            return {
              ...f,
              notes: b.text.trim() ? [...(f.notes ?? []), b.text.trim()] : f.notes,
              text: "",
              tools: [...b.tools, { name: event.name, input: JSON.stringify(event.input) }],
            };
          });
          break;
        case "error":
          if (!cancelledRef.current) onError(event.message);
          break;
      }
    },
    [onError, patchAssistant],
  );

  const stream = useCallback(
    async (url: string, body: unknown, userText: string | null) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      cancelledRef.current = false;
      setStreaming(true);
      setCost(0);
      setStatus("Thinking");
      setMessages((ms) => [
        ...ms,
        ...(userText != null ? [{ role: "user" as const, text: userText, tools: [] }] : []),
        { role: "assistant", text: "", tools: [] },
      ]);
      try {
        const res = await fetch(
          url,
          body === undefined
            ? { signal: controller.signal }
            : {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
                signal: controller.signal,
              },
        );
        if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            if (frame.startsWith("data: ")) handle(JSON.parse(frame.slice(6)) as AgentEvent);
          }
        }
        onTurnComplete?.();
      } catch (e) {
        if ((e as Error).name !== "AbortError") onError((e as Error).message);
      } finally {
        if (abortRef.current === controller) {
          setStreaming(false);
          setStatus(null);
        }
      }
    },
    [handle, onError, onTurnComplete],
  );

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || streaming) return;
      let id = conversationId;
      if (!id) {
        try {
          id = (await api<Conversation>("/agent/conversations", { method: "POST" })).id;
          setConversationId(id);
        } catch (e) {
          onError((e as Error).message);
          return;
        }
      }
      await stream(`/api/agent/conversations/${id}/messages`, { content }, content);
    },
    [conversationId, streaming, stream, onError],
  );

  /** Stop the live run on the server (every agent in it), not just this browser's stream. */
  const cancel = useCallback(async () => {
    if (!conversationId) return;
    cancelledRef.current = true;
    setStatus("Stopping");
    try {
      await api(`/agent/conversations/${conversationId}/cancel`, { method: "POST" });
    } catch {
      // Already finished: nothing to stop.
    }
  }, [conversationId]);

  const analyze = useCallback(
    async (h: Holding) => {
      if (streaming) await cancel();
      setConversationId(null);
      setMessages([]);
      await stream(
        "/api/agent/analyze",
        { tradingsymbol: h.tradingsymbol, exchange: h.exchange, kind: h.kind, name: h.name },
        `Analyse ${holdingLabel(h)}`,
      );
    },
    [stream, streaming, cancel],
  );

  /** Show a saved conversation, re-attaching to its run if one is in progress. Resolves false if it can't be loaded. */
  const load = useCallback(
    async (id: string, { quiet = false }: { quiet?: boolean } = {}) => {
      abortRef.current?.abort();
      let c: Conversation & { messages: ChatBubble[]; running: boolean; pending_user: string | null };
      try {
        c = await api(`/agent/conversations/${id}`);
      } catch (e) {
        if (!quiet) onError((e as Error).message);
        return false;
      }
      setConversationId(id);
      setMessages(c.messages);
      // A run is still going (e.g. the panel was closed mid-analysis): re-attach and follow it.
      if (c.running) await stream(`/api/agent/conversations/${id}/stream`, undefined, c.pending_user);
      return true;
    },
    [onError, stream],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setStreaming(false);
    setStatus(null);
  }, []);

  return { conversationId, messages, streaming, status, cost, send, analyze, cancel, load, reset };
}
