"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";

/**
 * "Remembered: works in the Bangalore office · Undo".
 * Memory is saved automatically; this shows what was kept, with one-click undo.
 */
export function RememberedNote({ facts }: { facts: { id: string; text: string }[] }) {
  const [kept, setKept] = useState(facts);
  const [error, setError] = useState(false);

  async function undo(id: string) {
    setError(false);
    try {
      await api.forgetMemory(id);
      setKept((all) => all.filter((f) => f.id !== id));
    } catch {
      setError(true);
    }
  }

  if (kept.length === 0) return null;
  return (
    <div className="chat-remembered" role="status">
      <span className="chat-remembered-label">Remembered:</span>
      {kept.map((fact) => (
        <span key={fact.id} className="chat-remembered-fact">
          {fact.text}
          <button type="button" className="chat-remembered-undo" onClick={() => undo(fact.id)}>
            Undo
          </button>
        </span>
      ))}
      <Link href="/account#memory" className="chat-remembered-manage">
        Manage
      </Link>
      {error && <span className="chat-remembered-error">Couldn’t undo. Try again.</span>}
    </div>
  );
}
