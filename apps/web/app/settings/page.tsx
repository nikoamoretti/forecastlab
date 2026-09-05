"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Probe = {
  provider?: string;
  model?: string | null;
  success: boolean;
  latency_ms: number;
  error_category?: string | null;
  error?: string;
};

export default function SettingsPage() {
  const [data, setData] = useState<any>(null);
  const [ready, setReady] = useState<any>(null);
  const [modelKey, setModelKey] = useState("");
  const [searchKey, setSearchKey] = useState("");
  const [saved, setSaved] = useState("");
  const [modelTest, setModelTest] = useState<Probe | null>(null);
  const [searchTest, setSearchTest] = useState<Probe | null>(null);

  async function load() {
    const settings = await api("/api/settings");
    const providers = await api("/health/providers");
    setData(settings);
    setReady(providers);
  }

  useEffect(() => {
    load().catch(() => setData({ error: "unavailable" }));
  }, []);

  async function onSave(event: React.FormEvent) {
    event.preventDefault();
    const payload = {
      model_provider: data.model_provider, model_base_url: data.model_base_url, model_name: data.model_name,
      search_provider: data.search_provider, max_cost_usd: data.max_cost_usd, model_timeout_seconds: data.model_timeout_seconds,
      model_api_key: modelKey || undefined,
      search_api_key: searchKey || undefined
    };
    const next = await api<{ secrets_managed_externally?: boolean }>("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    setData(next);
    setModelKey("");
    setSearchKey("");
    setSaved(next.secrets_managed_externally ? "Saved. Production secrets are managed in Vercel. Model cost may be estimated." : "Saved. Keys are stored locally and are never returned. Model cost may be estimated.");
    const providers = await api("/health/providers");
    setReady(providers);
  }

  async function clearModelKey() {
    const next = await api("/api/settings", { method: "PUT", body: JSON.stringify({ model_api_key: "" }) });
    setData(next);
    setSaved("Model key cleared.");
  }

  async function clearSearchKey() {
    const next = await api("/api/settings", { method: "PUT", body: JSON.stringify({ search_api_key: "" }) });
    setData(next);
    setSaved("Search key cleared.");
  }

  if (!data) return <p>Loading settings…</p>;

  return (
    <form onSubmit={onSave} className="mx-auto max-w-2xl space-y-5">
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Credentials</p>
      <h2 className="font-serif text-4xl">{data.secrets_managed_externally ? "Configure your forecasting method." : "Keep keys on this machine."}</h2>
      <p>
        Default provider posture: {data.mode || "unknown"}. Mode is chosen per question. API keys are write-only after
        save. Model cost may be estimated.
      </p>
      {data.secrets_managed_externally ? <p>Production keys are managed in Vercel environment settings. Changing the method requires a new Autopilot policy approval and qualification.</p> : null}
      <section className="border border-rule p-4 text-sm">
        <p>Live ready: {ready?.live?.ready ? "yes" : "no"}</p>
        {ready?.live?.reasons?.length ? <p>Live missing: {ready.live.reasons.join(", ")}</p> : null}
        <p className="mt-2">Backtest ready: {ready?.backtest?.ready ? "yes, after as_of" : "no"}</p>
        {ready?.backtest?.reasons?.length ? <p>Backtest notes: {ready.backtest.reasons.join(", ")}</p> : null}
      </section>
      <label className="block">
        Model provider
        <select
          className="mt-1 w-full border border-rule p-2"
          value={data.model_provider || "mock"}
          onChange={(e) => setData({ ...data, model_provider: e.target.value })}
        >
          <option value="mock">mock</option>
          <option value="openai_compatible">openai_compatible</option>
          <option value="openai">openai</option>
          <option value="xai">xai</option>
        </select>
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
        <input className="mt-1 w-full border border-rule p-2" type="password" disabled={data.secrets_managed_externally} value={modelKey} onChange={(e) => setModelKey(e.target.value)} />
      </label>
      <label className="block">
        Search provider
        <select
          className="mt-1 w-full border border-rule p-2"
          value={data.search_provider || "mock"}
          onChange={(e) => setData({ ...data, search_provider: e.target.value })}
        >
          <option value="mock">mock</option>
          <option value="tavily">tavily</option>
        </select>
      </label>
      <label className="block">
        Search API key {data.search_api_key_set ? "(set)" : "(not set)"}
        <input className="mt-1 w-full border border-rule p-2" type="password" disabled={data.secrets_managed_externally} value={searchKey} onChange={(e) => setSearchKey(e.target.value)} />
      </label>
      <label className="block">
        Cost ceiling (USD)
        <input className="mt-1 w-full border border-rule p-2" type="number" step="0.01" value={data.max_cost_usd || 5} onChange={(e) => setData({ ...data, max_cost_usd: Number(e.target.value) })} />
      </label>
      <label className="block">
        Timeout (seconds)
        <input className="mt-1 w-full border border-rule p-2" type="number" value={data.model_timeout_seconds || 60} onChange={(e) => setData({ ...data, model_timeout_seconds: Number(e.target.value) })} />
      </label>
      <div className="flex flex-wrap gap-3">
        <button className="border border-ink bg-ink px-4 py-2 text-paper">Save</button>
        <button type="button" className="border border-rule px-4 py-2" disabled={data.secrets_managed_externally} onClick={clearModelKey}>
          Clear model key
        </button>
        <button type="button" className="border border-rule px-4 py-2" disabled={data.secrets_managed_externally} onClick={clearSearchKey}>
          Clear search key
        </button>
        <button
          type="button"
          className="border border-rule px-4 py-2"
          onClick={async () => setModelTest(await api("/api/settings/test-model", { method: "POST" }))}
        >
          Test model connection
        </button>
        <button
          type="button"
          className="border border-rule px-4 py-2"
          onClick={async () => setSearchTest(await api("/api/settings/test-search", { method: "POST" }))}
        >
          Test search connection
        </button>
      </div>
      {modelTest ? (
        <p>
          Model test: {modelTest.success ? "ok" : "failed"} · {modelTest.provider} {modelTest.model} · {modelTest.latency_ms} ms
          {modelTest.error_category ? ` · ${modelTest.error_category}` : ""}
        </p>
      ) : null}
      {searchTest ? (
        <p>
          Search test: {searchTest.success ? "ok" : "failed"} · {searchTest.provider} · {searchTest.latency_ms} ms
          {searchTest.error_category ? ` · ${searchTest.error_category}` : ""}
        </p>
      ) : null}
      {saved ? <p>{saved}</p> : null}
    </form>
  );
}
