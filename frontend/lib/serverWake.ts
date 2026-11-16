"use client";

import { useSyncExternalStore } from "react";
import { ApiError } from "./api";

/** What an unreachable or starting API looks like through the /api proxy. */
const UNREACHABLE = new Set([502, 503, 504]);
/** How long a page keeps trying before it gives up, and the gap between tries. */
const WAKE_LIMIT_MS = 150_000;
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 8000;

/** No answer at all, or the proxy's 502/503/504: the server is starting, not refusing. */
export function isUnreachable(err: unknown): boolean {
  return !(err instanceof ApiError) || UNREACHABLE.has(err.status);
}

const WAKE_URL = process.env.NEXT_PUBLIC_API_WAKE_URL || "";

/**
 * Asks the API to start. Through the /api proxy a sleeping server is met by its
 * host's bot challenge and never starts, so the browser also calls it directly;
 * the reply is opaque and unused, no cookie is sent.
 */
export function wakeServer(): void {
  fetch("/api/health", { cache: "no-store" }).catch(() => {});
  if (WAKE_URL) {
    fetch(WAKE_URL, { mode: "no-cors", cache: "no-store", credentials: "omit" }).catch(() => {});
  }
}

let connecting = false;
const listeners = new Set<() => void>();

function setConnecting(value: boolean) {
  if (connecting === value) return;
  connecting = value;
  listeners.forEach((l) => l());
}

/** True while a page is waiting for the server to answer (the loader says so). */
export function useConnecting(): boolean {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => connecting,
    () => false,
  );
}

/**
 * Runs a READ until the server answers. The API can be asleep on free hosting,
 * and a page that treated the proxy's 502 as "signed out" sent people to the
 * sign-in page while their session was fine. Only for requests that are safe to
 * repeat; any other error is thrown at once.
 */
export async function untilReachable<T>(read: () => Promise<T>): Promise<T> {
  const deadline = Date.now() + WAKE_LIMIT_MS;
  let wait = FIRST_RETRY_MS;
  for (;;) {
    try {
      const result = await read();
      setConnecting(false);
      return result;
    } catch (err) {
      if (!isUnreachable(err) || Date.now() + wait > deadline) {
        setConnecting(false);
        throw err;
      }
      setConnecting(true);
      wakeServer();
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 1.5, MAX_RETRY_MS);
    }
  }
}
