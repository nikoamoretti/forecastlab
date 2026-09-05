# Manual evidence URL intake

ForecastLab keeps its original question-first workflow. A user may add a source URL to an existing question from the forecast report or with:

```http
POST /api/questions/{question_id}/evidence-urls
```

The request contains a URL, an optional note, an intended use (`general_question_evidence` or `forecast_node`), a `live` or `backtest` mode, and an `as_of` timestamp for backtests. A node target is accepted only when that node belongs to a graph for the same question.

## Intake boundary

Intake is an explicit user action. ForecastLab sends the URL through the existing safe document-fetch boundary, including public-address checks, bounded redirects, response-size and content-type limits, timeouts, content extraction, and temporal validation. It stores:

- submitted, canonical, and final URLs;
- accepted or rejected status and the normalized rejection reason;
- title, publisher, publication metadata, retrieval time, source-availability time, and temporal basis;
- raw-content and extracted-text hashes, content type, and byte length;
- requested and final archive identities for historical evidence;
- the question and optional Forecast Node target.

The intake key makes a repeated canonical URL idempotent for the same question, target, mode, and cutoff. Accepted duplicate content for that same scope resolves to the existing attachment. A document cannot be attached across questions or to a node owned by another question.

Adding a URL does **not** call a model, create an Evidence Claim, create a Forecast Run, or alter an existing Forecast Version. The attachment is a document-level audit record only.

## Temporal rules

In live mode, the existing temporal policy applies. A page with no reliable publication date may be accepted when its content is retrieved and hashed during the live intake. Its publication date remains unavailable; retrieval time is recorded as the live availability basis and is never presented as publication time.

In backtest mode, the supplied `as_of` is required. ForecastLab must discover and verify a qualifying archive snapshot at or before that cutoff, or use an already supported immutable historical source. A current page, retrieval time, a post-cutoff snapshot, an archive mismatch, or a search snippet cannot establish historical eligibility.

## Entering a forecast

Accepted attachments enter only a new, explicit run with the same question, mode, and cutoff. ForecastLab creates a run-scoped `EvidenceItem` linked to the attachment. For graph execution, a general attachment is available to selected nodes and a node-specific attachment only to its named node. The existing Evidence Extractor remains authoritative: it validates provenance and grounding and is the only boundary that can create factual Evidence Claims.

If the question already has a completed Forecast Version, accepting a new URL marks the question stale. The report clearly states that a fresh explicit rerun is required. Once the attachment has entered such a run, its audit lists that run and no longer claims that it is waiting for its first rerun.

## API response

`GET /api/questions/{question_id}/evidence-urls` returns every accepted and rejected attachment. The forecast JSON and Markdown reports expose the same audit. Raw fetched bodies are not returned by these endpoints.

This feature adds user-supplied evidence to the existing evidence path. It does not change graph generation, research planning, evidence eligibility, node forecasting, scenario synthesis, aggregation, scoring, or calibration.
