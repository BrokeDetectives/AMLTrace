# Architecture

How AMLTrace is put together, and why the seams fall where they do.

---

## The shape

```
             sample_ledger.csv / Kaggle export / synthetic generator
                                   |
                         dataset_adapters.to_canonical()
                                   |
                    sender, receiver, amount, day [, channel, is_fraud]
                                   |
                          aml_engine.build_graph()          -> nx.DiGraph
                                   |
                          aml_engine.run_pipeline()         -> L1..L6 flags
                                   |
        +--------------------------+---------------------------+
        |                          |                           |
  detect_rings()          suspicion_propagation()       build_timeline()
      L-ring                      L7                          L8
        |                          |                           |
        +--------------------------+---------------------------+
                                   |
                          aml_engine.analyse()   -> one JSON payload
                                   |
                    +--------------+--------------+
                    |                             |
                app.py (Flask)            generate_report.py
                    |                             |
              static/ browser UI            outputs/ artefacts
```

One rule holds the design together: **`aml_engine.py` is a pure library.** No printing, no
file I/O, no globals, no Flask import. Everything is a function of `(transactions, Config)`.
That is what lets the UI re-run the entire pipeline when an analyst drags a threshold
slider, and what lets `generate_report.py` produce byte-identical results with no server
running.

---

## Modules

| File | Responsibility | Depends on |
|---|---|---|
| `aml_engine.py` | Data generation, graph build, all nine layers, rings, timeline, dossiers, ablations, replay, economics | pandas, networkx, numpy, scikit-learn, india_calibration |
| `india_calibration.py` | The published Indian figures the generated ledger is pinned to, each with its source | none |
| `dataset_adapters.py` | Column-signature schema detection; translation to the canonical ledger | pandas |
| `app.py` | Flask routes, in-process state, CSV/JSON exports, static hosting | engine, adapters, flask |
| `generate_report.py` | Headless run; prints the analysis and writes `outputs/` | engine, adapters |
| `static/` | `index.html`, `styles.css`, `app.js`; the whole console, zero dependencies | none |
| `audit.py` | Self-audit: ledger/graph integrity, deck sync, seed stability, circularity probe, label audit | engine, deck |
| `redteam.py` | Attacks the system the way a hostile reviewer would, and names what it finds | engine, adapters |
| `samples/` | Schema-faithful files that exercise each adapter | none |
| `deck/` | The pitch deck, its charts, and the scripts that build both from the engine | engine, matplotlib, python-pptx |
| `sample_ledger.csv` | A tiny generic-schema ledger for smoke-testing upload | none |
| `run.sh` / `run.bat` | Install requirements, then start the app | none |

Nothing imports `app.py`. The dependency graph is a DAG pointing at the engine.

---

## The canonical ledger

Every input path converges on one dataframe before detection begins:

| Column | Type | Required | Meaning |
|---|---|---|---|
| `sender` | str | yes | Debited account |
| `receiver` | str | yes | Credited account |
| `amount` | float | yes | Value in ₹ (or the source unit) |
| `day` | int | derived | 1-based day index; computed from a timestamp when absent |
| `txn_id` | str | optional | Source reference, carried into STRs |
| `channel` | str | optional | NEFT / RTGS / UPI / CASH; display only |
| `is_fraud` | 0/1 | optional | Ground-truth label; drives the metrics panels |

If `is_fraud` is missing the engine still runs; it simply reports no precision/recall,
because there is nothing honest to compute them against.

---

## The pipeline in `run_pipeline()`

Seven detectors run independently over the same `(df, G, cfg)` and their outputs are merged
additively:

| Layer | Function | Weight | Can originate an alert? |
|---|---|---|---|
| L1 Structuring | `detect_structuring` | 2 | yes |
| L2 Fan-out / fan-in | `detect_fan` | 2 | yes |
| L3 Circular flow | `detect_cycles` | 2 | yes |
| L4 Pass-through | `detect_pass_through` | 2 | yes |
| L5 Chokepoint | `detect_centrality` | 1 | **no** |
| L6 Behavioural anomaly | `detect_ml_anomaly` | 1 | **no** |
| L9 Threshold evasion | `detect_adaptive` | 2 | yes |

L9 runs after the four rule layers and before the corroborating pair. It exists because
L1 to L4 each contain a published number, and the console prints every one of them: a
threshold an adversary can read is a threshold they can stand just outside. Rather than
adding a further number, it scores rhythm (are the hops scheduled or traded), value
conservation along a relay, and how much of each account survives once reciprocal cover
traffic is netted out. Two of the three must agree, and every hop must be one-shot, which
is what keeps a standing settlement corridor out of the alert queue. Its structuring half
infers the reporting line from the ledger instead of compiling the CTR threshold in, so it
transfers to another jurisdiction unchanged. `evasion_benchmark()` scores two ledgers
built to defeat the engine, with the layer on and off.

L5 and L6 receive `primary_flags` as an argument and are gated on it
(`ml_confirming_only`, and betweenness taken *inside the suspicious sub-network*). An
unsupervised anomaly score cannot be explained to a regulator, so it is never allowed to
originate a report, only to raise a score that a rule already justified.

Risk score = 2 per primary layer + 1 per corroborating layer.
`risk_tier()` maps it to CRITICAL (≥4) · HIGH (3) · MEDIUM (2) · LOW (1), each carrying an
action and an SLA that the alert queue displays verbatim.

Three further layers sit outside the scoring loop:

- **L7 `suspicion_propagation`**: damped exposure from confirmed alerts, capped at
  `prop_max_hops`. Produces a watchlist, never a flag, so guilt-by-association can never
  inflate the precision figure.
- **L8 `build_timeline`**: attributes each transaction to placement / layering /
  integration from the flag state of its two endpoints.
- **`detect_rings`**: connected components of the *detected* alert sub-network, typed by
  which layers dominate them. It never reads the ground-truth labels.

---

## Evidence, not just scores

Every detector returns `(flags, reasons)`. A reason is a structured record holding the
transactions, counterparties, windows and ratios that fired the rule, and it travels all
the way to `generate_dossier()`, which renders it as FIU-IND STR Parts A–F. There is no
point in the system where a score exists without its supporting evidence.

---

## Self-assessment built into the run

`analyse()` does not only detect; it audits itself on every call:

| Function | Question it answers |
|---|---|
| `run_ablation` | What does each layer add, cumulatively? |
| `run_discriminator_ablation` | How many of the 19 lawful look-alikes survive when each economic discriminator is switched off? |
| `run_robustness` | Jaccard overlap of the flagged set under ±15% threshold perturbations |
| `analyse_decoys` | Per-family verdict on the hard negatives |
| `replay` | Re-runs the pipeline day by day on truncated data, for detection lag and streaming precision |
| `alert_economics` | FTE, annual cost, cost per alert, illicit flow surfaced per analyst hour |

`replay` is the expensive one, since it runs the pipeline once per day of the window, which is
why `app.py` caches it and invalidates the cache on any reconfigure.

---

## State in the web layer

`app.py` keeps a single in-process `_STATE` dict behind a `threading.Lock`:

```
_STATE = {df, truth, cfg, meta, payload, replay}
```

- `/api/simulate` and `/api/upload` replace the ledger → full rebuild.
- `/api/reconfigure` keeps the ledger, rebuilds `Config`, re-runs `analyse` → replay cache cleared.
- Everything else reads `payload`.

This is deliberately a single-process, single-tenant prototype: one case in memory, no
database, no session handling. It is an investigation console for one analyst, not a
multi-tenant service.

---

## Front end

`static/app.js` is hand-written vanilla JavaScript. No framework, no bundler, no CDN. The
force-directed graph, every chart and every table are drawn directly, so the console runs
on a demo laptop with the wifi off. It fetches `/api/state` once, then re-renders twelve
views from that one payload; only reconfigure, replay, economics and upload go back to the
server.

---

## Determinism

`generate_dataset(seed=7, ...)` is fully seeded, and IsolationForest is fixed-seeded, so
the bundled ledger is identical on every machine: 187 accounts (35 fraud-linked, 19 lawful
look-alikes, 133 ordinary) and 301 transactions over 30 days. Every figure quoted in
[README.md](README.md) is reproducible with `python generate_report.py`.
