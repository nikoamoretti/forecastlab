"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";

const links = [
  ["Board", "/"],
  ["New question", "/new"],
  ["Lab", "/lab"],
  ["Settings", "/settings"]
];

export function Nav() {
  const path = usePathname();
  const [version, setVersion] = useState("");
  useEffect(() => {
    api<{ application_version?: string }>("/api/meta")
      .then((payload) => setVersion(payload.application_version || ""))
      .catch(() => undefined);
  }, []);
  return (
    <header className="border-b border-rule bg-paper/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-end justify-between px-6 py-5">
        <Link href="/" className="block">
          <p className="font-mono text-xs uppercase tracking-[0.28em] text-copper">Local laboratory</p>
          <h1 className="font-serif text-3xl tracking-tight">ForecastLab</h1>
          {version ? <p className="mt-1 font-mono text-xs text-ink/60">v{version}</p> : null}
        </Link>
        <nav className="flex gap-6 text-sm">
          {links.map(([label, href]) => (
            <Link
              key={href}
              href={href}
              className={`border-b-2 pb-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
                path === href ? "border-copper text-ink" : "border-transparent text-ink/70"
              }`}
            >
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
