"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";

const links = [
  ["Track record", "/"],
  ["Ask a question", "/new"],
  ["Autopilot", "/autopilot"]
];
const advanced = [
  ["All runs", "/runs"],
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
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-5 sm:flex-row sm:items-end sm:justify-between">
        <Link href="/" className="block">
          <h1 className="font-serif text-3xl tracking-tight">ForecastLab</h1>
          <p className="mt-1 text-sm text-ink/60">Forecasts you can check{version ? ` · v${version}` : ""}</p>
        </Link>
        <nav className="flex flex-wrap items-end gap-x-5 gap-y-3 text-sm">
          {links.map(([label, href]) => (
            <Link
              key={href}
              href={href}
              className={`border-b-2 pb-1 text-base focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
                path === href ? "border-copper text-ink" : "border-transparent text-ink/70 hover:text-ink"
              }`}
            >
              {label}
            </Link>
          ))}
          <span aria-hidden className="hidden h-5 border-l border-rule sm:block" />
          {advanced.map(([label, href]) => (
            <Link
              key={href}
              href={href}
              className={`border-b-2 pb-1 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
                path === href ? "border-copper text-ink" : "border-transparent text-ink/50 hover:text-ink"
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
