"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";

// Two everyday destinations; the operator pages sit behind "More".
const links = [
  ["Track record", "/"],
  ["Ask a question", "/ask"]
];
const more = [
  ["Autopilot", "/autopilot"],
  ["All runs", "/runs"],
  ["Lab", "/lab"],
  ["Settings", "/settings"]
];

export function Nav() {
  const path = usePathname();
  const [version, setVersion] = useState("");
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    api<{ application_version?: string }>("/api/meta")
      .then((payload) => setVersion(payload.application_version || ""))
      .catch(() => undefined);
  }, []);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !menu.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const active = (href: string) => (href === "/" ? path === "/" || path.startsWith("/q/") : path.startsWith(href));
  const inMore = more.some(([, href]) => active(href));
  return (
    <header className="border-b border-rule bg-paper/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-3 px-6 py-4">
        <Link href="/" className="font-serif text-2xl tracking-tight">ForecastLab</Link>
        <nav className="flex items-center gap-1 text-base" aria-label="Main">
          {links.map(([label, href]) => (
            <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}
              className={`rounded-md px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
                active(href) ? "bg-ink/5 font-medium text-ink" : "text-ink/70 hover:text-ink"}`}>{label}</Link>
          ))}
          <div className="relative" ref={menu}>
            <button type="button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}
              className={`rounded-md px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
                inMore ? "bg-ink/5 font-medium text-ink" : "text-ink/70 hover:text-ink"}`}>More ▾</button>
            {open && <div role="menu" className="absolute right-0 z-20 mt-2 w-48 border border-rule bg-paper py-1 shadow-lg">
              {more.map(([label, href]) => (
                <Link key={href} href={href} role="menuitem" className={`block px-4 py-2.5 hover:bg-ink/5 ${active(href) ? "font-medium" : ""}`}>{label}</Link>
              ))}
              {version && <p className="border-t border-rule px-4 py-2 text-xs text-ink/50">Version {version}</p>}
            </div>}
          </div>
        </nav>
      </div>
    </header>
  );
}
