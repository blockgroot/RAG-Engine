"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type McpServerAdmin, type McpTool } from "@/lib/api";

/**
 * Other tools through MCP servers (Admin › Actions).
 *
 * The admin adds a server and chooses each tool's mode. Everything starts
 * off. "Write" drafts a card the person approves, like every action; "Lookup"
 * lets Handbook read while drafting and is only offered for tools the server
 * marks read-only. Tools that delete, merge or change code are blocked. A tool
 * the server later changes turns off again until it is chosen again.
 */

const MODE_HINT: Record<McpTool["mode"], string> = {
  off: "Off",
  lookup: "Lookup (read while drafting)",
  write: "Write (approved on a card)",
};

export function McpServersAdmin({ disabled }: { disabled: boolean }) {
  const [servers, setServers] = useState<McpServerAdmin[] | null>(null);
  const [signInReady, setSignInReady] = useState(true);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.adminMcp();
      setServers(data.servers);
      setSignInReady(data.sign_in_ready);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load MCP servers.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(key: string, work: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await work();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work. Nothing changed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="studio-panel stack" aria-labelledby="actions-mcp">
      <div className="studio-section-head">
        <h2 id="actions-mcp">Other tools (MCP)</h2>
        <p className="muted">
          Add a tool&rsquo;s MCP server, then choose what each of its tools may do. Everything starts off.
          Writes still need the person&rsquo;s approval on a card, with their own sign-in. Tools that
          delete, merge or change code are blocked.
        </p>
      </div>

      {error && (
        <div className="banner banner-warn" role="alert">
          {error}
        </div>
      )}

      <form
        className="mcp-add"
        onSubmit={(event) => {
          event.preventDefault();
          void run("add", async () => {
            await api.addMcpServer(name.trim(), url.trim());
            setName("");
            setUrl("");
          });
        }}
      >
        <input
          className="input"
          aria-label="Tool name"
          placeholder="Name, e.g. Asana"
          maxLength={60}
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={disabled}
        />
        <input
          className="input"
          aria-label="MCP server address"
          placeholder="https://mcp.example.com/mcp"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={disabled}
        />
        <button className="button" type="submit" disabled={disabled || busy === "add" || !name.trim() || !url.trim()}>
          {busy === "add" ? "Adding…" : "Add server"}
        </button>
      </form>
      {!signInReady && (
        <p className="muted">Servers that need sign-in can&rsquo;t be added until FRONTEND_URL (or MCP_REDIRECT_URI) is set.</p>
      )}

      {servers?.map((server) => (
        <article key={server.id} className="actions-provider-group mcp-server">
          <header>
            <div>
              <h3>{server.name}</h3>
              <p>
                {server.url}
                {server.auth === "oauth" ? " · each person signs in" : " · no sign-in"}
              </p>
            </div>
            <div className="mcp-server-actions">
              <label className="action-toggle">
                <span>On</span>
                <input
                  type="checkbox"
                  checked={server.enabled}
                  disabled={disabled || busy === server.id}
                  onChange={(e) => void run(server.id, () => api.setMcpServerEnabled(server.id, e.target.checked))}
                  aria-label={`${server.enabled ? "Turn off" : "Turn on"} ${server.name}`}
                />
              </label>
              <button
                type="button"
                className="button button-secondary button-sm"
                disabled={busy === server.id}
                onClick={() => void run(server.id, () => api.refreshMcpServer(server.id))}
              >
                Refresh tools
              </button>
              <button
                type="button"
                className="button button-secondary button-sm"
                disabled={busy === server.id}
                onClick={() => {
                  if (window.confirm(`Remove ${server.name}? Everyone's sign-in to it is forgotten.`)) {
                    void run(server.id, () => api.deleteMcpServer(server.id));
                  }
                }}
              >
                Remove
              </button>
            </div>
          </header>
          {server.tools.length === 0 ? (
            <p className="muted mcp-empty">
              {server.auth === "oauth"
                ? "Sign in to it yourself under Account → Agent, then Refresh tools to list them."
                : "This server lists no tools."}
            </p>
          ) : (
            <ul>
              {server.tools.map((tool) => (
                <li key={tool.name} className="mcp-tool">
                  <span>
                    <strong>{tool.title}</strong>
                    {tool.description && <small>{tool.description}</small>}
                  </span>
                  {tool.blocked ? (
                    <span className="studio-chip">Blocked</span>
                  ) : (
                    <select
                      className="input mcp-mode"
                      aria-label={`What ${tool.title} may do`}
                      value={tool.mode}
                      disabled={disabled || busy === `${server.id}:${tool.name}`}
                      onChange={(e) =>
                        void run(`${server.id}:${tool.name}`, () =>
                          api.setMcpToolMode(server.id, tool.name, e.target.value as McpTool["mode"]),
                        )
                      }
                    >
                      <option value="off">{MODE_HINT.off}</option>
                      {tool.read_only && <option value="lookup">{MODE_HINT.lookup}</option>}
                      <option value="write">{MODE_HINT.write}</option>
                    </select>
                  )}
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
    </section>
  );
}
