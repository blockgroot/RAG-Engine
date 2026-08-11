/** @type {import('next').NextConfig} */

// Browser calls this Next origin only (`NEXT_PUBLIC_API_BASE_URL=/api`).
// These rewrites proxy `/api/:path*` to FastAPI so the session cookie is
// first-party (SameSite=Lax). A split vercel.app → onrender.com fetch cannot
// send a Lax cookie — do not "fix" that by flipping the cookie to None.
const apiProxyTarget = (
  process.env.API_PROXY_TARGET || "http://localhost:8000"
).replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
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
