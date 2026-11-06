"use client";

import { useState } from "react";

const NEEDS: Array<[string, string]> = [
  ["A Linux server with Docker", "2 CPUs, 4 GB memory and 20 GB disk is enough to start"],
  ["A web address", "Such as handbook.yourcompany.com, pointed at that server"],
  ["Keys for your AI model and search", "From your company’s own accounts"],
  ["An email account Handbook can send from", "For sign-in links and reports"],
  ["Someone who can add apps to your tools", "In Slack, Notion and the others you connect"],
];

/** Self-hosted requirements as a checklist the reader can tick off before installing. */
export function DeployChecklist() {
  const [done, setDone] = useState<boolean[]>(() => NEEDS.map(() => false));
  const count = done.filter(Boolean).length;

  return (
    <div className="dck">
      <div className="dck-head">
        <span>Before you install</span>
        <b>{count === NEEDS.length ? "Ready to install" : `${count} of ${NEEDS.length} ready`}</b>
        <i style={{ ["--p" as string]: count / NEEDS.length }} aria-hidden />
      </div>
      <ul>
        {NEEDS.map(([title, text], i) => (
          <li key={title}>
            <label>
              <input
                type="checkbox"
                checked={done[i]}
                onChange={() => setDone((d) => d.map((v, j) => (j === i ? !v : v)))}
              />
              <span>
                <b>{title}</b>
                <small>{text}</small>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
