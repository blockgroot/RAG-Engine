"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Me } from "@/lib/api";

const LINKS = [
  { href: "/chat", label: "Ask" },
  { href: "/workspaces", label: "My Workspaces" },
  { href: "/admin/connections", label: "Connections", adminOnly: true },
  { href: "/admin/members", label: "Members", adminOnly: true },
];

export function Nav({ me }: { me: Me | null }) {
  const pathname = usePathname();

  return (
    <nav className="nav">
      <div className="nav-links">
        {LINKS.filter((link) => !link.adminOnly || me?.role === "admin").map((link) => (
          <Link key={link.href} href={link.href} data-active={pathname.startsWith(link.href)}>
            {link.label}
          </Link>
        ))}
      </div>
      {me?.org_name && <span className="nav-org">{me.org_name}</span>}
    </nav>
  );
}
