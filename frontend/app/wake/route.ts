/**
 * Wakes the API from this server, not the browser. While the free API sleeps,
 * its host's edge answers proxied (/api) and browser fetches with a bot
 * challenge that never starts it; a plain server-to-server call does. Takes no
 * input and returns nothing but whether the API answered, so it is safe public.
 */
export const dynamic = "force-dynamic";

const TARGET = (process.env.API_PROXY_TARGET || "http://localhost:8000").replace(/\/$/, "");

export async function GET() {
  let up = false;
  try {
    // The request reaching the host is what starts it; no need to wait for the boot.
    const r = await fetch(`${TARGET}/health`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    up = r.ok;
  } catch {
    // Timed out while it starts, or unreachable: the next page call retries.
  }
  return Response.json({ up }, { headers: { "Cache-Control": "no-store" } });
}
