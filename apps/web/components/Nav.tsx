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
  ["All runs", "/runs"],
  ["Lab", "/lab"],
  ["Settings", "/settings"]
];

// The mark beside the name: three calls on a rising line.
function Mark() {
  return <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6 shrink-0">
    <rect width="24" height="24" rx="7" className="fill-ink" />
    <path d="M5.5 16.5 10 12l3.5 2.5L18.5 8" fill="none" className="stroke-paper" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="18.5" cy="8" r="1.9" className="fill-copper" />
  </svg>;
}

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
  const item = (on: boolean) => `rounded-full px-3 py-1.5 text-[15px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-copper ${
    on ? "bg-ink/[0.07] font-medium text-ink" : "text-ink/70 hover:bg-ink/[0.04] hover:text-ink"}`;
  return (
    <header className="sticky top-0 z-30 border-b border-rule/70 bg-paper/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5 font-serif text-[22px] leading-none tracking-tight"><Mark />ForecastLab</Link>
        <nav className="-mx-1 flex items-center gap-0.5 sm:gap-1" aria-label="Main">
          {/* On a phone the name on the left already leads home, and "Ask a question" is shortened, so the bar stays on one line. */}
          {links.map(([label, href]) => (
            <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}
              className={`${item(active(href))} ${href === "/" ? "hidden sm:inline-block" : ""}`}>
              {href === "/ask" ? <><span className="sm:hidden">Ask</span><span className="hidden sm:inline">{label}</span></> : label}</Link>
          ))}
          <div className="relative" ref={menu}>
            <button type="button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)} className={item(inMore)}>More ▾</button>
            {open && <div role="menu" className="card absolute right-0 z-20 mt-2 w-48 overflow-hidden py-1.5">
              {more.map(([label, href]) => (
                <Link key={href} href={href} role="menuitem" className={`block px-4 py-2.5 hover:bg-ink/5 ${active(href) ? "font-medium" : ""}`}>{label}</Link>
              ))}
              {version && <p className="mt-1 border-t border-rule/70 px-4 pb-1 pt-2.5 text-xs text-ink/50">Version {version}</p>}
            </div>}
          </div>
        </nav>
      </div>
    </header>
  );
}
