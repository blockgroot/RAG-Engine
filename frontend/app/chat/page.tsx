"use client";

import { useRef, useState } from "react";
import { Nav } from "@/components/Nav";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api } from "@/lib/api";

const SUGGESTED_QUESTIONS = [
  "How many days of paid leave do I get?",
  "What's the remote work policy?",
  "How do I claim a medical reimbursement?",
  "What are the maternity/paternity leave rules?",
];

export default function ChatPage() {
  const { me, loading } = useMe();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

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

  async function ask(question: string) {
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

    requestAnimationFrame(() => {
      logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
    });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    ask(input.trim());
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
      <div className="chat-page">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h1>Ask a question</h1>
            <p className="muted">Ask anything about your company&rsquo;s policies — leave, benefits, remote work, and more.</p>
            <div className="suggested-chips">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button key={q} type="button" className="suggested-chip" onClick={() => ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-log" ref={logRef}>
            {messages.map((m, i) => (
              <ChatMessageView key={i} message={m} />
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="chat-composer">
          <input
            className="chat-composer-input"
            placeholder="Ask about leave, benefits, remote work…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
            autoFocus
          />
          <button
            className="chat-composer-send"
            type="submit"
            disabled={busy || !input.trim()}
            aria-label="Send"
          >
            {busy ? "…" : "↑"}
          </button>
        </form>
      </div>
    </>
  );
}
