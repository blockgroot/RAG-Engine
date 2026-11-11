/** @type {import('next').NextConfig} */

// Browser calls this Next origin only (`NEXT_PUBLIC_API_BASE_URL=/api`).
// These rewrites proxy `/api/:path*` to FastAPI so the session cookie is
// first-party (SameSite=Lax). A split vercel.app → onrender.com fetch cannot
// send a Lax cookie — do not "fix" that by flipping the cookie to None.
const apiProxyTarget = (
  process.env.API_PROXY_TARGET || "http://localhost:8000"
).replace(/\/$/, "");

// One id per build, baked into the page and served live by /build-id: an open
// tab whose id no longer matches is an old version (components/NewVersionPill).
const buildId =
  process.env.VERCEL_GIT_COMMIT_SHA || process.env.HANDBOOK_IMAGE_VERSION || String(Date.now());

const nextConfig = {
  reactStrictMode: true,
  env: { NEXT_PUBLIC_BUILD_ID: buildId },
  // The Docker image (frontend/Dockerfile) needs a self-contained server;
  // Vercel builds without it, so production there is unchanged.
  ...(process.env.NEXT_STANDALONE === "1" ? { output: "standalone" } : {}),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiProxyTarget}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
