"use client";

import { useSyncExternalStore } from "react";
import { ApiError } from "./api";

/** What an unreachable or starting API looks like through the /api proxy. */
const UNREACHABLE = new Set([502, 503, 504]);
/** How long a call keeps trying before it gives up, and the gap between tries. */
const WAKE_LIMIT_MS = 240_000; // a cold start on the free plan measured ~160 s
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 8000;

/** No answer at all, or the proxy's 502/503/504: the server is starting, not refusing. */
export function isUnreachable(err: unknown): boolean {
  return !(err instanceof ApiError) || UNREACHABLE.has(err.status);
}

/**
 * Asks the API to start. Through the /api proxy (and from the browser) a
 * sleeping server is met by its host's bot challenge and never starts, so our
 * own server calls it directly (app/wake/route.ts).
 */
export function wakeServer(): void {
  fetch("/wake", { cache: "no-store" }).catch(() => {});
}

let waiting = 0;
const listeners = new Set<() => void>();

function setWaiting(delta: number) {
  const was = waiting > 0;
  waiting += delta;
  if (was !== waiting > 0) listeners.forEach((l) => l());
}

/** True while any call is waiting for the server to start (loaders say so). */
export function useConnecting(): boolean {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => waiting > 0,
    () => false,
  );
}

/**
 * Every API call goes through here, so no page needs its own waiting logic.
 *
 * While the API starts (free hosting sleeps when idle) a call is retried, and
 * the browser wakes the server meanwhile:
 * - the host's bot challenge (`cf-mitigated`) never reached our server, so ANY
 *   call is safe to send again, a question or a save included;
 * - a 502/503/504 or no answer may have reached it, so only a read is repeated.
 * After the wait the last answer (or error) is returned as it was.
 */
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const read = method === "GET" || method === "HEAD";
  const deadline = Date.now() + WAKE_LIMIT_MS;
  let wait = FIRST_RETRY_MS;
  let counted = false;
  try {
    for (;;) {
      let response: Response | null = null;
      let failure: unknown = null;
      try {
        response = await fetch(url, init);
      } catch (err) {
        failure = err;
      }
      const challenged = response !== null && !!response.headers.get("cf-mitigated");
      const starting = response === null || UNREACHABLE.has(response.status);
      if (!(challenged || (read && starting)) || Date.now() + wait > deadline) {
        if (response) return response;
        throw failure;
      }
      if (!counted) {
        counted = true;
        setWaiting(1);
      }
      wakeServer();
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 1.5, MAX_RETRY_MS);
    }
  } finally {
    if (counted) setWaiting(-1);
  }
}
