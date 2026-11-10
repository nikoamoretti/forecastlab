import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl space-y-4 py-10 text-center">
      <p className="eyebrow">Missing page</p>
      <h2 className="font-serif text-4xl">That path is not in ForecastLab.</h2>
      <p className="text-ink/70">Use the navigation to return to the board, a new question, the lab, or settings.</p>
      <p><Link href="/" className="btn-primary">Back to the track record</Link></p>
    </div>
  );
}
