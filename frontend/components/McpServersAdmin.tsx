"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ApiError, api, type McpPreset, type McpServerAdmin, type McpTool } from "@/lib/api";

/**
 * The tools Agent mode may use (Admin › Actions), each through that tool's own
 * MCP server. Laid out like a connector list: added servers first, each opening
 * to its tools with one switch per tool; the connectors not yet added below.
 *
 * A switch turns a tool on in the only mode it can have: a read-only tool runs
 * while drafting, anything else drafts a card the person approves. Tools Agent
 * mode may never use (deleting, merging, code) are not listed, only counted.
 */

const BRANDS: Record<string, BrandName> = {
  linear: "linear",
  notion: "notion",
  github: "github",
  slack: "slack",
  google_drive: "drive",
  google_sheets: "drive",
};

/** "notion-create-pages" → "Create pages"; a server's own title wins when it has one. */
function toolName(tool: McpTool, server: string) {
  const raw = tool.title && tool.title !== tool.name ? tool.title : tool.name;
  const words = raw
    .replace(new RegExp(`^${server}[-_ ]`, "i"), "")
    .replace(/[-_]+/g, " ")
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function McpServersAdmin({ disabled }: { disabled: boolean }) {
  const [servers, setServers] = useState<McpServerAdmin[] | null>(null);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [signInReady, setSignInReady] = useState(true);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
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

  const presetOf = (server: McpServerAdmin) =>
    presets.find((p) => p.url.replace(/\/$/, "") === server.url.replace(/\/$/, ""));
  const available = presets.filter((p) => !p.added);

  return (
    <section className="studio-panel stack mcp" aria-labelledby="actions-tools">
      <div className="mcp-head">
        <div>
          <h2 id="actions-tools">Tools</h2>
          <p className="muted">Choose which tools Agent mode can use. Every write waits for approval.</p>
        </div>
        <button
          type="button"
          className="button button-secondary button-sm"
          onClick={() => setAdding((v) => !v)}
          disabled={disabled}
        >
          {adding ? "Cancel" : "+ Custom server"}
        </button>
      </div>

      {error && (
        <div className="banner banner-warn" role="alert">
          {error}
        </div>
      )}
      {!signInReady && <p className="muted">Set FRONTEND_URL on the server before adding tools that need sign-in.</p>}

      {adding && (
        <form
          className="mcp-add"
          onSubmit={(event) => {
            event.preventDefault();
            void run("add", async () => {
              await api.addMcpServer({ name: name.trim(), url: url.trim() });
              setName("");
              setUrl("");
              setAdding(false);
            });
          }}
        >
          <input
            className="input"
            aria-label="Name"
            placeholder="Name, e.g. Asana"
            maxLength={60}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="input"
            aria-label="MCP server address"
            placeholder="https://mcp.example.com/mcp"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button className="button button-sm" type="submit" disabled={busy === "add" || !name.trim() || !url.trim()}>
            {busy === "add" ? "Adding…" : "Add"}
          </button>
        </form>
      )}

      {servers && servers.length > 0 && (
        <ul className="mcp-list">
          {servers.map((server) => {
            const preset = presetOf(server);
            const usable = server.tools.filter((t) => !t.blocked);
            const hidden = server.tools.length - usable.length;
            const on = usable.filter((t) => t.mode !== "off").length;
            const expanded = open === server.id;
            const q = filter.trim().toLowerCase();
            const shown = q
              ? usable.filter((t) => `${t.name} ${t.title} ${t.description}`.toLowerCase().includes(q))
              : usable;
            return (
              <li key={server.id} className="mcp-row" data-open={expanded}>
                <div className="mcp-row-head">
                  <button
                    type="button"
                    className="mcp-row-main"
                    aria-expanded={expanded}
                    onClick={() => {
                      setOpen(expanded ? null : server.id);
                      setFilter("");
                    }}
                  >
                    <span className="mcp-logo" aria-hidden="true">
                      <BrandGlyph name={BRANDS[preset?.key ?? ""] ?? "model"} size={22} />
                    </span>
                    <span className="mcp-row-text">
                      <strong>{server.name}</strong>
                      <small>
                        {usable.length ? `${on} of ${usable.length} tools on` : "Sign in to list its tools"}
                      </small>
                    </span>
                    <span className="mcp-chevron" aria-hidden="true">
                      {expanded ? "▴" : "▾"}
                    </span>
                  </button>
                  <label className="action-toggle mcp-switch">
                    <input
                      type="checkbox"
                      checked={server.enabled}
                      disabled={disabled || busy === server.id}
                      onChange={(e) => void run(server.id, () => api.setMcpServerEnabled(server.id, e.target.checked))}
                      aria-label={`${server.name} ${server.enabled ? "on" : "off"}`}
                    />
                  </label>
                </div>

                {expanded && (
                  <div className="mcp-body">
                    {preset?.note && <p className="mcp-note">{preset.note}</p>}

                    {usable.length === 0 ? (
                      <p className="mcp-note">
                        <Link href="/account#act">Sign in to {server.name}</Link> under Account → Agent; its tools
                        appear here.{" "}
                        <button
                          type="button"
                          className="mcp-link"
                          disabled={busy === server.id}
                          onClick={() => void run(server.id, () => api.refreshMcpServer(server.id))}
                        >
                          Refresh
                        </button>
                      </p>
                    ) : (
                      <>
                        <div className="mcp-toolbar">
                          {usable.length > 8 && (
                            <input
                              className="input mcp-search"
                              placeholder="Search tools"
                              aria-label={`Search ${server.name} tools`}
                              value={filter}
                              onChange={(e) => setFilter(e.target.value)}
                            />
                          )}
                          <button
                            type="button"
                            className="mcp-link"
                            disabled={disabled || busy === server.id}
                            onClick={() => void run(server.id, () => api.setMcpModes(server.id, "recommended"))}
                          >
                            Turn all on
                          </button>
                          <button
                            type="button"
                            className="mcp-link"
                            disabled={disabled || busy === server.id || on === 0}
                            onClick={() => void run(server.id, () => api.setMcpModes(server.id, "off"))}
                          >
                            Turn all off
                          </button>
                        </div>
                        <ul className="mcp-tools">
                          {shown.map((tool) => {
                            const key = `${server.id}:${tool.name}`;
                            const label = toolName(tool, server.name);
                            return (
                              <li key={tool.name} className="mcp-tool">
                                <span className="mcp-tool-text">
                                  <strong>
                                    {label}
                                    <em className="mcp-kind">{tool.read_only ? "Read" : "Write"}</em>
                                  </strong>
                                  {tool.description && <small title={tool.description}>{tool.description}</small>}
                                </span>
                                <label className="action-toggle mcp-switch">
                                  <input
                                    type="checkbox"
                                    checked={tool.mode !== "off"}
                                    disabled={disabled || busy === key}
                                    onChange={(e) =>
                                      void run(key, () =>
                                        api.setMcpToolMode(
                                          server.id,
                                          tool.name,
                                          e.target.checked ? (tool.read_only ? "lookup" : "write") : "off",
                                        ),
                                      )
                                    }
                                    aria-label={`${label} ${tool.mode !== "off" ? "on" : "off"}`}
                                  />
                                </label>
                              </li>
                            );
                          })}
                          {shown.length === 0 && <li className="mcp-note">No tool matches.</li>}
                        </ul>
                        {hidden > 0 && (
                          <p className="mcp-note">
                            {hidden} more {hidden === 1 ? "tool isn't" : "tools aren't"} available: Agent mode never
                            deletes, merges or changes code.
                          </p>
                        )}
                      </>
                    )}

                    <div className="mcp-footer">
                      {usable.length > 0 && (
                        <button
                          type="button"
                          className="mcp-link"
                          disabled={busy === server.id}
                          onClick={() => void run(server.id, () => api.refreshMcpServer(server.id))}
                        >
                          Refresh tools
                        </button>
                      )}
                      <button
                        type="button"
                        className="mcp-link mcp-danger"
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
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {available.length > 0 && (
        <div className="mcp-more">
          <h3 className="mcp-subhead">{servers?.length ? "Add more" : "Add a tool"}</h3>
          <ul className="mcp-presets">
            {available.map((preset) => (
              <li key={preset.key} className="mcp-preset" title={preset.note || undefined}>
                <span className="mcp-logo" aria-hidden="true">
                  <BrandGlyph name={BRANDS[preset.key] ?? "model"} size={20} />
                </span>
                <strong>{preset.name}</strong>
                <button
                  type="button"
                  className="button button-secondary button-sm"
                  disabled={disabled || busy === preset.key}
                  onClick={() => void run(preset.key, () => api.addMcpServer({ preset: preset.key }))}
                >
                  {busy === preset.key ? "Adding…" : "Add"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
