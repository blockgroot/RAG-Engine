"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { isUnreachable, wakeServer } from "@/lib/serverWake";

/** Seconds the button rests after "unreachable" or "too many tries". */
const COOLDOWN_SECONDS = 30;
/** After this long, the request is probably waiting for the server to start. */
const SLOW_MS = 4000;

/**
 * One send at a time from a public form (sign-in, sign-up), with honest waits.
 *
 * The API can be asleep on free hosting, so the page contacts it as soon as it
 * opens, a slow send says so, and an unreachable server gets a short message
 * and a 30-second rest instead of a button people press again and again.
 * Other errors are returned for the form to show as before.
 */
export function useServerSubmit() {
  const [loading, setLoading] = useState(false);
  const [slow, setSlow] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  // A ref, not state: two clicks in one frame both see `loading` as false.
  const inFlight = useRef(false);

  useEffect(() => {
    wakeServer();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast]);

  /** Runs `send`; resolves to the error message for the form, or null. */
  async function submit(send: () => Promise<void>): Promise<string | null> {
    if (inFlight.current || cooldown > 0) return null;
    inFlight.current = true;
    setToast(null);
    setLoading(true);
    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    try {
      await send();
      return null;
    } catch (err) {
      if (isUnreachable(err)) {
        wakeServer();
        setToast("We couldn't reach the server just now. Please try again in a minute.");
        setCooldown(COOLDOWN_SECONDS);
        return null;
      }
      if (err instanceof ApiError && err.status === 429) {
        setToast("Too many requests. Please wait a moment and try again.");
        setCooldown(COOLDOWN_SECONDS);
        return null;
      }
      return err instanceof Error ? err.message : "Something went wrong.";
    } finally {
      clearTimeout(slowTimer);
      setSlow(false);
      setLoading(false);
      inFlight.current = false;
    }
  }

  const toastView = toast ? (
    <div className="app-toast" role="status" aria-live="polite">
      <span>{toast}</span>
      <button type="button" aria-label="Dismiss" onClick={() => setToast(null)}>
        ×
      </button>
    </div>
  ) : null;

  const slowNote = loading && slow ? (
    <p className="muted auth-v2-wake-note">
      This is taking longer than usual. Please keep this page open; it will finish as soon as the
      connection completes.
    </p>
  ) : null;

  /** The button's words: idle, sending, slow, or resting. */
  function label(idle: string, sending: string): string {
    if (loading) return slow ? "Connecting…" : sending;
    return cooldown > 0 ? `Try again in ${cooldown}s` : idle;
  }

  return { submit, busy: loading || cooldown > 0, label, toastView, slowNote };
}
