"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, api, type McpPreset, type McpServerAdmin, type McpTool } from "@/lib/api";

/**
 * The tools Agent mode may use (Admin › Actions), each through that tool's own
 * MCP server.
 *
 * The admin adds a connector in one click (or any server by address), signs in
 * to it themselves to list its tools, and chooses what each tool may do.
 * Everything starts off. "Read" lets Handbook look things up while drafting;
 * "Write" drafts a card the person approves. Tools outside what Agent mode may
 * ever do (deleting, merging, code) cannot be turned on. A tool the server
 * later changes turns off again until it is chosen again.
 */

const MODE_LABEL: Record<McpTool["mode"], string> = {
  off: "Off",
  lookup: "Read (while drafting)",
  write: "Write (approved on a card)",
};

export function McpServersAdmin({ disabled }: { disabled: boolean }) {
  const [servers, setServers] = useState<McpServerAdmin[] | null>(null);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [signInReady, setSignInReady] = useState(true);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.adminMcp();
      setServers(data.servers);
      setPresets(data.presets);
      setSignInReady(data.sign_in_ready);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the tools.");
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
    <section className="studio-panel stack" aria-labelledby="actions-tools">
      <div className="studio-section-head">
        <h2 id="actions-tools">Tools</h2>
        <p className="muted">
          Agent mode works through each tool&rsquo;s own MCP server. Add a tool, sign in to it once
          yourself to see its tools, then choose what each may do. Everything starts off, and every
          write waits for the person&rsquo;s approval, with their own sign-in.
        </p>
      </div>

      {error && (
        <div className="banner banner-warn" role="alert">
          {error}
        </div>
      )}
      {!signInReady && (
        <p className="muted">Tools that need sign-in can&rsquo;t be added until FRONTEND_URL (or MCP_REDIRECT_URI) is set.</p>
      )}

      <ul className="mcp-presets" aria-label="Connectors">
        {presets.map((preset) => (
          <li key={preset.key} className="mcp-preset">
            <span>
              <strong>{preset.name}</strong>
              {preset.note && <small>{preset.note}</small>}
            </span>
            {preset.added ? (
              <span className="studio-chip">Added</span>
            ) : (
              <button
                type="button"
                className="button button-secondary button-sm"
                disabled={disabled || busy === preset.key}
                onClick={() => void run(preset.key, () => api.addMcpServer({ preset: preset.key }))}
              >
                {busy === preset.key ? "Adding…" : "Add"}
              </button>
            )}
          </li>
        ))}
      </ul>

      <details className="mcp-other">
        <summary>Add another tool by its MCP address</summary>
        <form
          className="mcp-add"
          onSubmit={(event) => {
            event.preventDefault();
            void run("add", async () => {
              await api.addMcpServer({ name: name.trim(), url: url.trim() });
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
            {busy === "add" ? "Adding…" : "Add"}
          </button>
        </form>
      </details>

      {servers?.map((server) => {
        const on = server.tools.filter((t) => t.mode !== "off").length;
        const allowed = server.tools.filter((t) => !t.blocked).length;
        const shown = open[server.id] ?? false;
        return (
          <article key={server.id} className="actions-provider-group mcp-server">
            <header>
              <div>
                <h3>{server.name}</h3>
                <p>
                  {server.tools.length
                    ? `${on} of ${allowed} tools on`
                    : server.auth === "oauth"
                      ? "Sign in to see its tools"
                      : "No tools listed"}
                  {server.auth === "oauth" ? " · each person signs in" : " · no sign-in"}
                </p>
              </div>
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
            </header>

            <div className="mcp-server-actions">
              {server.tools.length > 0 && (
                <>
                  <button
                    type="button"
                    className="button button-sm"
                    disabled={disabled || busy === server.id}
                    onClick={() => void run(server.id, () => api.setMcpModes(server.id, "recommended"))}
                    title="Reads run while drafting; writes are approved on a card"
                  >
                    Turn on recommended
                  </button>
                  <button
                    type="button"
                    className="button button-secondary button-sm"
                    disabled={disabled || busy === server.id || on === 0}
                    onClick={() => void run(server.id, () => api.setMcpModes(server.id, "off"))}
                  >
                    Turn all off
                  </button>
                </>
              )}
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

            {server.tools.length === 0 ? (
              server.auth === "oauth" && (
                <p className="muted mcp-empty">
                  Sign in to {server.name} yourself under <Link href="/account#act">Account → Agent</Link>, then
                  Refresh tools to list them.
                </p>
              )
            ) : (
              <>
                <button
                  type="button"
                  className="act-text-button mcp-view"
                  aria-expanded={shown}
                  onClick={() => setOpen((all) => ({ ...all, [server.id]: !shown }))}
                >
                  {shown ? "Hide tools" : `View ${server.tools.length} tools`}
                </button>
                {shown && (
                  <ul className="mcp-tools">
                    {server.tools.map((tool) => (
                      <li key={tool.name} className="mcp-tool" data-blocked={tool.blocked}>
                        <span>
                          <strong>{tool.title}</strong>
                          {tool.description && <small>{tool.description}</small>}
                        </span>
                        {tool.blocked ? (
                          <span className="studio-chip" title="Deletes, merges or changes code, or is outside what Agent mode may do">
                            Not allowed
                          </span>
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
                            <option value="off">{MODE_LABEL.off}</option>
                            {tool.read_only && <option value="lookup">{MODE_LABEL.lookup}</option>}
                            <option value="write">{MODE_LABEL.write}</option>
                          </select>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </article>
        );
      })}
    </section>
  );
}
