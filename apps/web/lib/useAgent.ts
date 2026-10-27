"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatBubble, type Conversation, type Holding } from "./api";

type AgentEvent =
  | { type: "conversation"; id: string }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_start"; id: string; name: string }
  | { type: "tool_input"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; is_error: boolean }
  | { type: "done"; stop_reason: string }
  | { type: "error"; message: string };

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
  const abortRef = useRef<AbortController | null>(null);

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
        case "tool_start":
          setStatus(TOOL_STATUS[event.name] ?? event.name);
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
          onError(event.message);
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
      setStreaming(true);
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

  const analyze = useCallback(
    async (h: Holding) => {
      setConversationId(null);
      setMessages([]);
      await stream(
        "/api/agent/analyze",
        { tradingsymbol: h.tradingsymbol, exchange: h.exchange, kind: h.kind, name: h.name },
        `Analyse ${holdingLabel(h)}`,
      );
    },
    [stream],
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

  return { conversationId, messages, streaming, status, send, analyze, load, reset };
}
