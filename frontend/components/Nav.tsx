import Link from "next/link";
import { Me } from "@/lib/api";

export function Nav({ me }: { me: Me | null }) {
  return (
    <nav className="nav">
      <div>
        <Link href="/chat">Ask</Link>
        {me?.role === "admin" && (
          <>
            <Link href="/admin/connections">Connections</Link>
            <Link href="/admin/domains">Domains</Link>
            <Link href="/admin/jobs">Jobs</Link>
          </>
        )}
      </div>
      <span className="muted">{me?.org_name}</span>
    </nav>
  );
}
