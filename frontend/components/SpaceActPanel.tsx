"use client";

/**
 * A space owner's Agent mode settings. Only narrows what the company allows:
 * the switch is shown but locked while the company has Agent mode off, and the
 * tool list holds only what the admin switched on. Every tool starts on here;
 * the owner may switch a tool, or a whole tool's server, off for this space.
 * What each person may then do in a tool is still their own account's access.
 */

import { useEffect, useState } from "react";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ApiError, api, type SpaceAct, type SpaceToolServer } from "@/lib/api";

const BRANDS: Record<string, BrandName> = {
  linear: "linear",
  notion: "notion",
  github: "github",
  slack: "slack",
  google_workspace: "google",
};

function toolLabel(name: string, title: string) {
  const words = (title && title !== name ? title : name).replace(/[-_]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function SpaceActPanel({ workspaceId }: { workspaceId: string }) {
  const [state, setState] = useState<SpaceAct | null>(null);
  const [servers, setServers] = useState<SpaceToolServer[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.spaceAct(workspaceId).then(setState).catch(() => setState(null));
    api.spaceTools(workspaceId).then((r) => setServers(r.servers)).catch(() => setServers([]));
  }, [workspaceId]);

  if (!state) return null;
  const companyOff = state.company === "off";

  async function run(key: string, work: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change that.");
    } finally {
      setBusy(null);
    }
  }

  const switchTool = (serverIds: string[], tool: string | null, enabled: boolean) =>
    run(`${serverIds.join()}:${tool ?? ""}`, async () => {
      let latest: SpaceToolServer[] = servers;
      for (const id of serverIds) latest = (await api.setSpaceTool(workspaceId, id, tool, enabled)).servers;
      setServers(latest);
    });

  // Servers of one product (Google Workspace) share a row.
  const rows: { key: string; name: string; brand: BrandName; servers: SpaceToolServer[] }[] = [];
  for (const server of servers) {
    const key = server.group ?? server.id;
    const row = rows.find((r) => r.key === key);
    if (row) row.servers.push(server);
    else
      rows.push({
        key,
        name: server.group_name ?? server.name,
        brand: BRANDS[server.brand ?? ""] ?? "model",
        servers: [server],
      });
  }

  return (
    <section className="studio-panel stack" aria-labelledby="space-act-title">
      <div className="studio-section-head">
        <h2 id="space-act-title">Agent mode in this space</h2>
        <p className="muted">
          Whether members may ask Handbook to make changes in their tools from this space. Each
          change still runs on the member&rsquo;s own account and waits for their approval.
        </p>
      </div>
      <label className="action-surface">
        <span>
          <strong>Allow Agent mode in this space</strong>
          <small>
            {companyOff
              ? "Your admin has turned Agent mode off for the company (Admin › Actions). You can switch it on here once they allow it."
              : state.enabled
              ? "Members can use the tools below, with their own accounts, after they approve each change."
              : "Off for this space. Your admin allows Agent mode; you turned it off here."}
          </small>
        </span>
        <input
          type="checkbox"
          checked={state.enabled}
          disabled={busy !== null || !state.can_change}
          onChange={(e) => {
            const enabled = e.target.checked;
            void run("act", async () => setState(await api.setSpaceAct(workspaceId, enabled)));
          }}
          aria-label={state.enabled ? "Turn off Agent mode in this space" : "Allow Agent mode in this space"}
        />
      </label>

      {!companyOff && state.enabled && (
        <div className="mcp-more">
          <h3 className="mcp-subhead">Tools in this space</h3>
          {rows.length === 0 ? (
            <p className="mcp-note">Your admin hasn&rsquo;t switched on any tools yet (Admin › Actions).</p>
          ) : (
            <>
              <p className="mcp-note">
                These are the tools your admin switched on. Switch any off for this space; the rest of the
                company keeps them.
              </p>
              <ul className="mcp-list">
                {rows.map((row) => {
                  const ids = row.servers.map((s) => s.id);
                  const rowOn = row.servers.every((s) => s.on);
                  const tools = row.servers.flatMap((s) => s.tools.map((t) => ({ server: s, tool: t })));
                  const on = tools.filter(({ server, tool }) => server.on && tool.on).length;
                  const expanded = open === row.key;
                  return (
                    <li key={row.key} className="mcp-row" data-open={expanded}>
                      <div className="mcp-row-head">
                        <button
                          type="button"
                          className="mcp-row-main"
                          aria-expanded={expanded}
                          onClick={() => setOpen(expanded ? null : row.key)}
                        >
                          <span className="mcp-logo" aria-hidden="true">
                            <BrandGlyph name={row.brand} size={22} />
                          </span>
                          <span className="mcp-row-text">
                            <strong>{row.name}</strong>
                            <small>{rowOn ? `${on} of ${tools.length} tools on here` : "Off in this space"}</small>
                          </span>
                          <span className="mcp-chevron" aria-hidden="true">
                            {expanded ? "▴" : "▾"}
                          </span>
                        </button>
                        <label className="action-toggle mcp-switch">
                          <input
                            type="checkbox"
                            checked={rowOn}
                            disabled={busy !== null}
                            onChange={(e) => void switchTool(ids, null, e.target.checked)}
                            aria-label={`${row.name} in this space ${rowOn ? "on" : "off"}`}
                          />
                        </label>
                      </div>
                      {expanded && rowOn && (
                        <div className="mcp-body">
                          <ul className="mcp-tools">
                            {tools.map(({ server, tool }) => {
                              const label = toolLabel(tool.name, tool.title);
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
                                      checked={tool.on}
                                      disabled={busy !== null}
                                      onChange={(e) => void switchTool([server.id], tool.name, e.target.checked)}
                                      aria-label={`${label} in this space ${tool.on ? "on" : "off"}`}
                                    />
                                  </label>
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      )}

      {error && (
        <p className="act-card-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
