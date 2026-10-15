"use client";

/** Act moved into the Account page; old links and bookmarks land there. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function ActRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace(`/account${window.location.search}#act`);
  }, [router]);
  return null;
}
