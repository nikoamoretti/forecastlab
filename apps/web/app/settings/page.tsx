"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [data, setData] = useState<any>(null);
  const [modelKey, setModelKey] = useState("");
  const [searchKey, setSearchKey] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    api("/api/settings").then(setData);
  }, []);

  async function onSave(event: React.FormEvent) {
    event.preventDefault();
    const payload = {
      ...data,
      model_api_key: modelKey || undefined,
      search_api_key: searchKey || undefined
    };
    const next = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    setData(next);
    setModelKey("");
    setSearchKey("");
    setSaved("Saved. Keys are stored locally and are never returned.");
  }

  if (!data) return <p>Loading settings…</p>;

  return (
    <form onSubmit={onSave} className="mx-auto max-w-2xl space-y-5">
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Credentials</p>
      <h2 className="font-serif text-4xl">Keep keys on this machine.</h2>
      <p>Current mode: {data.mode}. API keys are write-only after save.</p>
      <label className="block">
        Model provider
        <input className="mt-1 w-full border border-rule p-2" value={data.model_provider || ""} onChange={(e) => setData({ ...data, model_provider: e.target.value })} />
      </label>
      <label className="block">
        Base URL
        <input className="mt-1 w-full border border-rule p-2" value={data.model_base_url || ""} onChange={(e) => setData({ ...data, model_base_url: e.target.value })} />
      </label>
      <label className="block">
        Model name
        <input className="mt-1 w-full border border-rule p-2" value={data.model_name || ""} onChange={(e) => setData({ ...data, model_name: e.target.value })} />
      </label>
      <label className="block">
        Model API key {data.model_api_key_set ? "(set)" : "(not set)"}
        <input className="mt-1 w-full border border-rule p-2" type="password" value={modelKey} onChange={(e) => setModelKey(e.target.value)} />
      </label>
      <label className="block">
        Search provider
        <input className="mt-1 w-full border border-rule p-2" value={data.search_provider || ""} onChange={(e) => setData({ ...data, search_provider: e.target.value })} />
      </label>
      <label className="block">
        Search API key {data.search_api_key_set ? "(set)" : "(not set)"}
        <input className="mt-1 w-full border border-rule p-2" type="password" value={searchKey} onChange={(e) => setSearchKey(e.target.value)} />
      </label>
      <label className="block">
        Cost ceiling (USD)
        <input className="mt-1 w-full border border-rule p-2" type="number" step="0.01" value={data.max_cost_usd || 5} onChange={(e) => setData({ ...data, max_cost_usd: Number(e.target.value) })} />
      </label>
      <label className="block">
        Timeout (seconds)
        <input className="mt-1 w-full border border-rule p-2" type="number" value={data.model_timeout_seconds || 60} onChange={(e) => setData({ ...data, model_timeout_seconds: Number(e.target.value) })} />
      </label>
      <button className="border border-ink bg-ink px-4 py-2 text-paper">Save</button>
      {saved ? <p>{saved}</p> : null}
    </form>
  );
}
