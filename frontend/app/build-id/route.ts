/** The build this server is running. An open tab compares it with its own. */
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(
    { id: process.env.NEXT_PUBLIC_BUILD_ID ?? null },
    { headers: { "Cache-Control": "no-store" } },
  );
}
