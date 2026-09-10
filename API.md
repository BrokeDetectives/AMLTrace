# API reference

Base URL `http://127.0.0.1:5000`. Everything is JSON except the `/api/export/*.csv`
endpoints. There is no authentication: this is a single-analyst local console, not a
service. Uploads are capped at 16 MB (`MAX_CONTENT_LENGTH`).

The server holds exactly one case in memory. `/api/simulate` and `/api/upload` replace it;
`/api/reconfigure` re-scores it. Everything else reads it.

---

## `GET /api/state`

The entire application state, the payload every view in the UI is rendered from. Building
it on the first request takes a few seconds; afterwards it is served from memory.

Top-level keys:

| Key | Contents |
|---|---|
| `config` | Every tunable, as the current `Config` |
| `dataset` | `accounts`, `transactions`, `total_value`, `days`, `labelled`, `fraud_linked_accounts`, `decoy_accounts`, `epoch` |
| `summary` | `flagged`, `tiers`, `rings`, `modularity`, `exposure`, `illicit_flow`, `illicit_share`, `layer_counts`, `watchlist` |
| `metrics` | `precision`, `recall`, `f1`, `tp/fp/fn/tn`, and the named false positives and negatives; `labelled: false` when the ledger carries no `is_fraud` column |
| `alerts` | One object per flagged account: score, tier, action, SLA, layers, inflow/outflow/exposure, ring, typology, centrality, ML score, structured reasons |
| `rings` | Detected components with `ring_id`, `members`, `typology` |
| `propagation` | L7 watchlist: exposure score, hop distance, path |
| `timeline` | L8 events with placement / layering / integration attribution |
| `graph` | `nodes` and `edges` for the force-directed view |
| `decoys`, `discriminator_ablation`, `ablation`, `robustness` | The self-assessment blocks |
| `economics` | Costed alert population under default assumptions |
| `layer_meta`, `accounts`, `ring_labels`, `source`, `schema`, `seed` | Metadata |

---

## `POST /api/simulate`

Generate a fresh synthetic ledger and analyse it.

```json
{ "seed": 7, "n_normal": 135, "n_normal_txn": 150, "config": { "fan_min_branches": 4 } }
```

All fields optional; `config` accepts any subset of the tunables below. Returns the same
payload as `/api/state`.

```bash
curl -s -X POST localhost:5000/api/simulate -H 'Content-Type: application/json' -d '{"seed":42}'
```

---

## `POST /api/reconfigure`

Re-run detection on the **current** ledger with new thresholds; the endpoint behind the
Detection Lab sliders. Unspecified tunables keep their current values.

```json
{ "config": { "structuring_min_count": 3, "use_ml": false }, "ablation": true }
```

Set `"ablation": false` to skip the ablation and robustness sweeps when you only want the
alert set back quickly. Returns the full payload. Clears the replay cache.

### Tunables

| Group | Fields |
|---|---|
| L1 structuring | `structuring_floor_pct` 0.85 · `structuring_min_count` 4 · `structuring_window_days` 5 · `smurf_min_senders` 3 · `structuring_require_avoidance` true · `structuring_min_crossers` 2 · `structuring_crosser_share` 0.25 |
| L1(c) aggregation | `agg_min_count` 5 · `agg_window_days` 7 |
| L2 fan | `fan_min_branches` 3 · `fan_min_dedication` 0.75 · `fan_max_retention` 0.60 · `fan_max_inflation` 2.00 |
| L3 cycles | `cycle_max_len` 6 · `cycle_max_span_slack` 3 · `cycle_require_monotone` true · `cycle_max_edge_count` 2 · `cycle_min_conservation` 0.25 |
| L4 pass-through | `pt_ratio_low` 0.93 · `pt_ratio_high` 1.00 · `pt_window_days` 1 · `pt_min_amount` 250000 · `pt_max_counterparties` 4 · `pt_max_repeats` 2 · `pt_rail_span_days` 7 |
| L5 centrality | `centrality_z` 2.0 |
| L6 ML | `ml_cutoff` -0.05 · `ml_confirming_only` true |
| L7 propagation | `prop_alpha` 0.85 · `prop_max_hops` 3 · `prop_decay` 0.45 |
| L9 evasion (relay) | `adaptive_min_hops` 3 · `adaptive_hop_low` 0.60 · `adaptive_hop_high` 1.05 · `adaptive_min_conservation` 0.35 · `adaptive_rhythm_cv` 0.30 · `adaptive_amount_cv` 0.30 · `adaptive_net_dedication` 0.60 · `adaptive_max_edge_count` 2 · `adaptive_min_amount` 100000 |
| L9 evasion (inferred line) | `adaptive_agg_min_count` 5 · `adaptive_agg_amount_cv` 0.35 · `adaptive_agg_hug` 0.75 · `adaptive_agg_multiple` 2.0 |
| Layer switches | `use_structuring` · `use_fan` · `use_cycles` · `use_pass_through` · `use_adaptive` · `use_centrality` · `use_ml` |

Unknown keys and values that will not cast to the field's type are ignored rather than
rejected, so a partial or slightly wrong config never 500s.

---

## `POST /api/upload`

Analyse an uploaded CSV. The schema is auto-detected from its column signature, see
[DATASETS.md](DATASETS.md).

Multipart:

```bash
curl -s -X POST localhost:5000/api/upload -F file=@HI-Small_Trans.csv -F limit=50000
```

Or JSON with the file inline:

```json
{ "csv": "sender,receiver,amount\nA,B,900000\n" }
```

`limit` (multipart form field only) keeps the first N rows. The current config is carried
over. Returns the full payload, with `schema` naming the detected adapter.

`400` with `{"error": "..."}` on an empty file, an unrecognised schema, or any parse
failure. Rejections the ingest layer raises deliberately (unknown schema, a missing
column, the row and day-span caps) carry a message naming what is wrong with the file.
Anything else reports a generic parse failure, because the underlying message would be
pandas describing its own internals rather than anything the uploader can act on.

---

## `GET /api/evasion`

The adversarial benchmark. Two ledgers built specifically to defeat this engine's own
published thresholds, each scored twice: once with the adaptive layer switched off, once
with it on. Neither depends on the ledger currently loaded, so the result is computed once
per process and cached.

```json
{
  "scenarios": [
    { "scenario": "Threshold-evasive relay ring",
      "note": "Six accounts moving one sum in a loop, every parameter placed outside the published thresholds...",
      "criminal_accounts": 6,
      "recall_without_adaptive": 0.0, "recall_with_adaptive": 1.0,
      "precision_with_adaptive": 1.0,
      "caught_without": 0, "caught_with": 6, "false_positives_with": 0 }
  ]
}
```

The second scenario is a peeling chain, a typology no layer in the engine was written for.
It is there as a generalisation test: nothing in it was used to tune a threshold.

---

## `GET /api/replay`

Day-by-day re-detection: the pipeline re-run on only the transactions seen up to each day.

```json
{
  "frames":  [{ "day": 6, "date": "2026-08-06", "txns_seen": 70, "value_seen": 21909717,
                "flagged": 7, "tiers": {}, "new_alerts": [], "accounts": [],
                "precision": 0.0, "recall": 0.0 }],
  "latency": [{ "account": "SBIN909774", "typology": "CYCLE-RING-3",
                "first_active_day": 6, "detected_day": 7, "latency_days": 1 }],
  "never_detected": [],
  "max_day": 30,
  "summary": { "first_alert_day": 3, "mean_latency_days": 4.23, "median_latency_days": 4.0,
               "worst_latency_days": 17, "detected": 35, "never_detected": 0 }
}
```

Expensive, since it runs the whole pipeline once per day of the window, so it is computed on
first request and cached until the ledger or config changes.

---

## `POST /api/economics`

Recost the current alert population under different staffing assumptions. Does not re-run
detection.

```json
{ "analyst_cost_per_hour": 1200, "portfolio_multiplier": 1 }
```

`portfolio_multiplier` scales the alert volume to a larger book. Returns `assumptions`
(cost per hour, review minutes by tier, productive hours per FTE-year, observation window),
`alerts_per_day` / `alerts_per_year`, `review_hours_year`, `analyst_fte`,
`annual_review_cost`, `value_at_risk`, `illicit_flow_surfaced`, `return_per_analyst_hour`,
`cost_per_alert`, `false_positive_burden`, `conversion_rate` with its
`industry_comparison`, and `missed`.

---

## `GET /api/dossier/<account>`

The FIU-IND Suspicious Transaction Report dossier for one account, as JSON. It follows the
STR structure: `report_type`, `regulatory_basis`, `report_reference`, `reporting_entity`,
`account_id`, `risk_score` / `risk_tier` / `recommended_action` / `sla`, `narrative`,
`grounds_of_suspicion`, `transaction_summary`, `behavioural_z_scores`,
`betweenness_centrality`, `ml_anomaly_score`, `associated_ring`, `one_hop_subgraph`, the
itemised `transactions`, and `action_taken`. `404` if the account is not in the ledger.

## `GET /api/subgraph/<account>?hops=n`

The n-hop undirected neighbourhood of an account (`hops` clamped to 1–3):

```json
{ "center": "CNRB243795", "hops": 2,
  "nodes": [{ "id": "CNRB243795", "risk": 5, "tier": "CRITICAL", "hop": 0 }],
  "edges": [{ "source": "CNRB243795", "target": "UTIB119613", "amount": 900000, "count": 1 }] }
```

## `GET /api/schemas`

The supported public-dataset schemas, their column signatures and their sources: what the
Detection Lab lists in its upload panel.

---

## Exports

CSV endpoints return UTF-8-BOM with a `Content-Disposition` attachment header, so they open
cleanly in Excel.

| Endpoint | Contents |
|---|---|
| `GET /api/export/alerts.csv` | The triaged alert queue |
| `GET /api/export/timeline.csv` | L8 events with stage attribution |
| `GET /api/export/watchlist.csv` | L7 propagation leads |
| `GET /api/export/transactions.csv` | The ledger as analysed |
| `GET /api/export/rings.csv` | Detected rings and members |
| `GET /api/export/replay.csv` | Per-day streaming metrics |
| `GET /api/export/latency.csv` | Per-account detection lag |
| `GET /api/export/decoys.csv` | Hard-negative verdicts |
| `GET /api/export/provenance.json` | Every calibrated parameter with its source, plus observed against published channel mix |
| `GET /api/export/str/<account>.json` | One STR dossier |
| `GET /api/export/str_bundle.csv` | All dossiers, one row each |
| `GET /api/export/metrics.json` | Config, dataset, summary, metrics, ablations, robustness, economics |

`generate_report.py` writes the same artefacts to `outputs/` with no server running.

---

## Errors

| Code | When |
|---|---|
| `400` | Bad or empty CSV, unrecognised schema, parse failure |
| `404` | Unknown account on `/api/dossier` or `/api/subgraph` |
| `413` | Upload over 16 MB |

Errors are `{"error": "<message>"}`. Detection itself does not fail on odd data; it
reports what it found, including nothing.
