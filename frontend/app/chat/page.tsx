"use client";

import { useRef, useState } from "react";
import { Nav } from "@/components/Nav";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api } from "@/lib/api";

export default function ChatPage() {
  const { me, loading } = useMe();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);

  async function ensureConversation() {
    if (conversationId.current) return conversationId.current;
    try {
      const { conversation_id } = await api.createConversation();
      conversationId.current = conversation_id;
    } catch {
      // Memory may be disabled server-side; each question just goes standalone.
    }
    return conversationId.current;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);

    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setMessages((prev) => [...prev, { role: "assistant", text: "", streaming: true }]);

    const convId = await ensureConversation();

    await streamChat(question, convId, {
      onToken: (chunk) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: last.text + chunk };
          return next;
        });
      },
      onDone: (payload) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, streaming: false, done: payload };
          return next;
        });
        setBusy(false);
      },
      onError: (message) => {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", text: `Error: ${message}` };
          return next;
        });
        setBusy(false);
      },
    });
  }

  if (loading) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <>
      <Nav me={me} />
      <main className="page stack">
        <h1>Ask a question</h1>
        <div className="stack">
          {messages.length === 0 && (
            <p className="muted">Ask anything about your company&rsquo;s policies — leave, benefits, remote work, and more.</p>
          )}
          {messages.map((m, i) => (
            <ChatMessageView key={i} message={m} />
          ))}
        </div>
        <form onSubmit={handleSubmit} className="stack">
          <input
            className="input"
            placeholder="How many days of paid leave do I get?"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          <button className="button" type="submit" disabled={busy || !input.trim()}>
            {busy ? "Thinking…" : "Ask"}
          </button>
        </form>
      </main>
    </>
  );
}
