"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ApiError, api, type McpPreset, type McpServerAdmin, type McpTool } from "@/lib/api";

/**
 * The tools Agent mode may use (Admin › Actions), each through that tool's own
 * MCP server. Laid out like a connector list: added tools first, each opening
 * to its tools with one switch per tool; the connectors not yet added below.
 * Servers of one product (Google Workspace: Drive and Sheets) share one row.
 *
 * A switch turns a tool on in the only mode it can have: a read-only tool runs
 * while drafting, anything else drafts a card the person approves. Nothing is
 * blocked; a destructive tool is marked, never turned on by "Turn all on", and
 * asks the admin to confirm.
 */

const BRANDS: Record<string, BrandName> = {
  linear: "linear",
  notion: "notion",
  github: "github",
  slack: "slack",
  google_workspace: "google",
};

/** One row: a server, or every server of one product. */
type Row = { key: string; name: string; brand: BrandName; note: string; servers: McpServerAdmin[] };

/** "notion-create-pages" → "Create pages"; a server's own title wins when it has one. */
function toolName(tool: McpTool, server: string) {
  const raw = tool.title && tool.title !== tool.name ? tool.title : tool.name;
  const prefix = (server.split(" ").pop() ?? "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const words = raw
    .replace(new RegExp(`^${prefix}[-_ ]`, "i"), "")
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

  const rows: Row[] = [];
  for (const server of servers ?? []) {
    const tile = presets.find((p) =>
      server.group ? p.key === server.group : p.url?.replace(/\/$/, "") === server.url.replace(/\/$/, ""),
    );
    const key = server.group ?? server.id;
    const row = rows.find((r) => r.key === key);
    if (row) row.servers.push(server);
    else
      rows.push({
        key,
        name: server.group && tile ? tile.name : server.name,
        brand: BRANDS[tile?.key ?? ""] ?? "model",
        note: tile?.note ?? "",
        servers: [server],
      });
  }
  const available = presets.filter((p) => !p.added);
  const each = (row: Row, work: (s: McpServerAdmin) => Promise<unknown>) =>
    run(row.key, () => Promise.all(row.servers.map(work)));

  function setTool(server: McpServerAdmin, tool: McpTool, on: boolean, label: string) {
    if (
      on &&
      tool.destructive &&
      !window.confirm(`${label} can delete or change things in ${server.name} that can't be undone. Each use still waits for the person's approval. Turn it on?`)
    ) {
      return;
    }
    void run(`${server.id}:${tool.name}`, () =>
      api.setMcpToolMode(server.id, tool.name, on ? (tool.read_only ? "lookup" : "write") : "off"),
    );
  }

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

      {rows.length > 0 && (
        <ul className="mcp-list">
          {rows.map((row) => {
            const all = row.servers.flatMap((s) => s.tools);
            const on = all.filter((t) => t.mode !== "off").length;
            const enabled = row.servers.every((s) => s.enabled);
            const expanded = open === row.key;
            const q = filter.trim().toLowerCase();
            const grouped = row.servers.length > 1;
            return (
              <li key={row.key} className="mcp-row" data-open={expanded}>
                <div className="mcp-row-head">
                  <button
                    type="button"
                    className="mcp-row-main"
                    aria-expanded={expanded}
                    onClick={() => {
                      setOpen(expanded ? null : row.key);
                      setFilter("");
                    }}
                  >
                    <span className="mcp-logo" aria-hidden="true">
                      <BrandGlyph name={row.brand} size={22} />
                    </span>
                    <span className="mcp-row-text">
                      <strong>{row.name}</strong>
                      <small>{all.length ? `${on} of ${all.length} tools on` : "Sign in to list its tools"}</small>
                    </span>
                    <span className="mcp-chevron" aria-hidden="true">
                      {expanded ? "▴" : "▾"}
                    </span>
                  </button>
                  <label className="action-toggle mcp-switch">
                    <input
                      type="checkbox"
                      checked={enabled}
                      disabled={disabled || busy === row.key}
                      onChange={(e) => {
                        const value = e.target.checked;
                        void each(row, (s) => api.setMcpServerEnabled(s.id, value));
                      }}
                      aria-label={`${row.name} ${enabled ? "on" : "off"}`}
                    />
                  </label>
                </div>

                {expanded && (
                  <div className="mcp-body">
                    {row.note && <p className="mcp-note">{row.note}</p>}

                    {all.length === 0 ? (
                      <p className="mcp-note">
                        <Link href="/account#act">Sign in to {row.name}</Link> under Account → Agent; its tools
                        appear here.{" "}
                        <button
                          type="button"
                          className="mcp-link"
                          disabled={busy === row.key}
                          onClick={() => void each(row, (s) => api.refreshMcpServer(s.id))}
                        >
                          Refresh
                        </button>
                      </p>
                    ) : (
                      <>
                        <div className="mcp-toolbar">
                          {all.length > 8 && (
                            <input
                              className="input mcp-search"
                              placeholder="Search tools"
                              aria-label={`Search ${row.name} tools`}
                              value={filter}
                              onChange={(e) => setFilter(e.target.value)}
                            />
                          )}
                          <button
                            type="button"
                            className="mcp-link"
                            disabled={disabled || busy === row.key}
                            title="Destructive tools stay as they are: turn each on by itself"
                            onClick={() => void each(row, (s) => api.setMcpModes(s.id, "recommended"))}
                          >
                            Turn all on
                          </button>
                          <button
                            type="button"
                            className="mcp-link"
                            disabled={disabled || busy === row.key || on === 0}
                            onClick={() => void each(row, (s) => api.setMcpModes(s.id, "off"))}
                          >
                            Turn all off
                          </button>
                        </div>
                        <ul className="mcp-tools">
                          {row.servers.map((server) => {
                            const shown = q
                              ? server.tools.filter((t) =>
                                  `${t.name} ${t.title} ${t.description}`.toLowerCase().includes(q),
                                )
                              : server.tools;
                            return [
                              grouped && shown.length > 0 && (
                                <li key={`${server.id}-head`} className="mcp-tool-group">
                                  {server.name.replace(/^Google /, "")}
                                </li>
                              ),
                              ...shown.map((tool) => {
                                const label = toolName(tool, server.name);
                                return (
                                  <li key={`${server.id}:${tool.name}`} className="mcp-tool">
                                    <span className="mcp-tool-text">
                                      <strong>
                                        {label}
                                        <em className="mcp-kind" data-danger={tool.destructive}>
                                          {tool.destructive ? "Destructive" : tool.read_only ? "Read" : "Write"}
                                        </em>
                                      </strong>
                                      {tool.description && <small title={tool.description}>{tool.description}</small>}
                                    </span>
                                    <label className="action-toggle mcp-switch">
                                      <input
                                        type="checkbox"
                                        checked={tool.mode !== "off"}
                                        disabled={disabled || busy === `${server.id}:${tool.name}`}
                                        onChange={(e) => setTool(server, tool, e.target.checked, label)}
                                        aria-label={`${label} ${tool.mode !== "off" ? "on" : "off"}`}
                                      />
                                    </label>
                                  </li>
                                );
                              }),
                            ];
                          })}
                        </ul>
                      </>
                    )}

                    <div className="mcp-footer">
                      {all.length > 0 && (
                        <button
                          type="button"
                          className="mcp-link"
                          disabled={busy === row.key}
                          onClick={() => void each(row, (s) => api.refreshMcpServer(s.id))}
                        >
                          Refresh tools
                        </button>
                      )}
                      <button
                        type="button"
                        className="mcp-link mcp-danger"
                        disabled={busy === row.key}
                        onClick={() => {
                          if (window.confirm(`Remove ${row.name}? Everyone's sign-in to it is forgotten.`)) {
                            void each(row, (s) => api.deleteMcpServer(s.id));
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
          <h3 className="mcp-subhead">{rows.length ? "Add more" : "Add a tool"}</h3>
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
