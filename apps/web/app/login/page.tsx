export default function LoginPage() {
  return <section className="mx-auto max-w-lg py-16">
    <p className="font-mono text-xs uppercase tracking-widest text-copper">Your forecasting laboratory</p>
    <h2 className="mt-4 font-serif text-5xl">Welcome back.</h2>
    <p className="my-6 text-ink/70">Sign in with your GitHub account to view forecasts, manage Autopilot, and confirm outcomes.</p>
    <form action="/api/auth/github" method="get"><button className="inline-block bg-ink px-5 py-3 text-paper">Continue with GitHub</button></form>
    <p className="mt-5 text-sm text-ink/60">ForecastLab is private and restricted to its configured owner.</p>
  </section>;
}
