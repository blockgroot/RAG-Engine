/** @type {import('next').NextConfig} */

// Browser calls this Next origin only (`NEXT_PUBLIC_API_BASE_URL=/api`).
// These rewrites proxy `/api/:path*` to FastAPI so the session cookie is
// first-party (SameSite=Lax). A split vercel.app → onrender.com fetch cannot
// send a Lax cookie — do not "fix" that by flipping the cookie to None.
const apiProxyTarget = (
  process.env.API_PROXY_TARGET || "http://localhost:8000"
).replace(/\/$/, "");

// The id an open tab compares with /build-id (components/NewVersionPill): the
// newest RELEASE this build carries, never the commit. Cloud deploys main many
// times a day; "a new version is ready" means a release, and a release adds
// release-notes/<version>.md. An image built from a tag says its version.
const releaseId = (() => {
  try {
    const fs = require("fs");
    const path = require("path");
    const num = (v) => v.split(".").map(Number);
    const newer = (a, b) => { const [x, y] = [num(a), num(b)]; for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]; return false; };
    return fs
      .readdirSync(path.join(__dirname, "..", "release-notes"))
      .map((f) => f.match(/^(\d+\.\d+\.\d+)\.md$/)?.[1])
      .filter(Boolean)
      .reduce((best, v) => (!best || newer(v, best) ? v : best), null);
  } catch {
    return null; // a build without the folder (the frontend image) uses its tag
  }
})();
const buildId = process.env.HANDBOOK_IMAGE_VERSION || releaseId || "";

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
