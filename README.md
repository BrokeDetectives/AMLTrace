# AMLTrace: Tracing Financial Crime Across Patterns

An anti-money-laundering investigation console. It ingests a transaction ledger, builds a
directed money-flow graph, runs nine detection layers over it, and produces what a
compliance team actually needs: a triaged alert queue, the ring structure behind the
alerts, a reconstructed laundering timeline, a watchlist of new leads, filing-ready
FIU-IND Suspicious Transaction Reports, and an honest account of what the whole thing
costs to run.

Built for **Build $ Bank**, IGDTUW

---

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

Offline version, same engine, writes every artefact to `outputs/`:

```bash
python generate_report.py
python generate_report.py --csv HI-Small_Trans.csv --limit 50000   # a real public dataset
```

No build step, no CDN, no internet dependency. The force-directed graph, every chart and
every table is hand-written, so it runs on a demo laptop with the wifi off.

---

## The nine layers

| # | Layer | Kind | Role | What it catches |
|---|-------|------|------|-----------------|
| L1 | Structuring / smurfing | rule | **primary** | Transfers sized just under the ₹10,00,000 CTR threshold; many-to-one funnels |
| L2 | Fan-out → fan-in | graph | **primary** | Funds split across intermediaries that reconverge on one destination |
| L3 | Circular flow | graph + time | **primary** | Money that round-trips to its origin, closing quickly and in order |
| L4 | Pass-through conduit | temporal | **primary** | Receive and forward roughly the same amount within a day (the mule signature) |
| L5 | Sub-network chokepoint | graph | corroborating | Betweenness *inside the suspicious sub-network*: which freeze fragments the operation |
| L6 | Behavioural anomaly | ML | corroborating | IsolationForest over nine per-account features |
| L7 | Suspicion propagation | graph | lead generation | Unflagged accounts exposed to confirmed alerts |
| L8 | Laundering timeline | temporal | reconstruction | Placement → layering → integration attribution per transaction |
| L9 | Threshold evasion | adaptive | **primary** | Relays that sit deliberately outside every published threshold |

L5 and L6 can only raise the score of an account a primary layer already flagged. An
unsupervised anomaly score cannot be explained to a regulator, so it is not allowed to
originate an STR. L7 sits outside the alerting pipeline entirely, so guilt-by-association
can never inflate the precision figure.

Risk score = 2 per primary typology + 1 per corroborating layer.
≥4 CRITICAL · 3 HIGH · 2 MEDIUM · 1 LOW.

---

## Layer 9: catching a launderer who has read the rulebook

L1 to L4 are published rules, and every one of them has a number in it. This interface
prints all of those numbers on the Detection Lab page. Anyone who can read them can sit
just outside: space the hops of a loop wider than the span cap, size the deposits below
the reporting band, hold the money a day longer than the conduit window, and give every
mule a little unrelated trade so its dedication ratio never rises. Our own `redteam.py`
builds exactly that ledger, and layers 1 to 8 score **recall 0.00** on it.

L9 does not add another number to sit outside of. It scores three properties that follow
from moving other people's money through other people's accounts, and that therefore
survive the manoeuvre:

| | Test | Why a launderer cannot drop it |
|---|---|---|
| **D1** | **Rhythm** | Trade between two businesses is irregular. Money walked along a relay arrives on a schedule, because someone is operating it. Evading a time window means committing to a rhythm. |
| **D2** | **Conservation** | One sum keeping its size across several hops, distinct from what those accounts otherwise carry. Laundering has to move a particular amount from A to Z. |
| **D3** | **Net exposure** | Cover traffic inflates gross volume until any dedication ratio falls below threshold. Netting each counterparty pair first removes the disguise, because money that comes straight back changes nothing. |

Two of the three must agree, and every hop on the relay must be one-shot, before anything
is flagged. That last test is what keeps an acquiring bank out of the queue: a standing
settlement corridor is rhythmic and does conserve value, but it runs on the same rails
every week.

The structuring half of the layer works the same way. Rather than compiling India's
₹10,00,000 CTR line into the detector, it reads the lowest round figure a run of tranches
never crosses, so the same test works for a US $10,000 CTR or a EUR 10,000 cash ceiling
with no code change. That also closes the "spread the deposits over a fortnight" evasion,
which no time window can.

### The benchmark

Two ledgers built specifically to defeat this engine, each scored twice. The second is a
**peeling chain**: a typology no layer here was written for, included to test whether L9
generalises or has just been fitted to the first attack. Nothing in either was used to
tune a threshold.

| Adversarial ledger | Layers 1–8 | With L9 | Precision | FP |
|---|---|---|---|---|
| Threshold-evasive relay ring (6 accounts) | recall **0.00** | recall **1.00** | 1.00 | 0 |
| Peeling chain, typology not implemented (8 accounts) | recall **0.00** | recall **1.00** | 1.00 | 0 |

It costs nothing on the ordinary ledger: precision stays **1.00** across all ten seeds and
none of the 19 lawful look-alikes is flagged. On the circularity probe in `audit.py`,
which bends each typology toward the legitimate side, it lifts *structuring spread over
12 days* from 0.83 to **1.00** and *cycles closing 3× slower* from 0.74 to **0.89**.

Run it yourself: `GET /api/evasion`, the **Adversarial test** view, or the section in
`python generate_report.py`.

---

## Hard negatives

Any detector scores perfectly on data where only criminals form triangles. So the ledger
contains **19 accounts of entirely lawful business, engineered to reproduce each typology's
topology**. None is labelled fraud:

| Lawful activity | Looks like | Why it is not a crime |
|---|---|---|
| Customer → 3 contractors → shared distributor | Fan-out / fan-in | The contractors have their own unrelated trade |
| Merchant ↔ PSP ↔ acquirer settlement | Circular flow | A standing commercial relationship, repeated for months |
| Escrow agent | Pass-through conduit | Openly does this with many counterparties |
| Payroll bureau paid just under the CTR line | Structuring | Also sends sums *above* the line, so it is not avoiding it |

Four economic discriminators separate them: **threshold avoidance**, **branch dedication**,
**standing relationship**, and **settlement-rail recurrence**. Turning each off in isolation:

| Configuration | Precision | Recall | FP | Look-alikes wrongly flagged |
|---|---|---|---|---|
| All discriminators on | **1.00** | 1.00 | 0 | **0 / 19** |
| Without threshold avoidance (L1) | 0.95 | 1.00 | 2 | 2 / 19 |
| Without branch dedication (L2) | 0.78 | 1.00 | 10 | 10 / 19 |
| Without standing relationship (L3) | 0.85 | 1.00 | 6 | 6 / 19 |
| Without settlement-rail recurrence (L4) | 0.95 | 1.00 | 2 | 2 / 19 |
| **All off, pure shape matching** | **0.66** | 1.00 | 18 | **18 / 19** |

A topology-only AML detector flags 18 of 19 legitimate businesses. That last row is the
reason the discriminators exist.

---

## Streaming replay

The pipeline is re-run **each day on only the transactions seen so far**:

- First alert possible: **day 3**
- Detection lag: mean **4.23 days**, median 4, worst 17
- All 35 laundering accounts eventually surface; none is missed

And the uncomfortable part:

> Batch scoring reports precision **1.00**. The same detector, run as a stream, spends its
> first four days of alerting at precision **0.00**, is still at **0.68** on day 13, and
> does not reach the batch figure until **day 22**.

That gap is the cost of acting early rather than a defect. The discriminators are
evidence-hungry: a settlement rail is indistinguishable from a money mule until you have
watched it settle three times; a contractor looks like a dedicated conduit until its
unrelated invoices arrive. Early in the window that history does not exist, so the first
accounts the system names are lawful businesses, provisionally flagged and then correctly
released as their ordinary trade arrives.

A batch precision figure quoted on its own leaves this curve out.

---

## Alert economics

Detection quality is not the decision a bank makes; staffing is. Every threshold is a
budget line, so the app costs the current configuration:

- **0.32 analyst FTE**, ₹7.01 L a year at ₹1,200/hour on this book
- **₹1,646** per alert · **₹9.25 L** of illicit flow surfaced per analyst hour
- Conversion rate **100%** against a typical large-bank **~6%**, flagged in the UI as the
  synthetic-data artefact it is

The **threshold ↔ cost frontier** re-runs the pipeline across a range of settings: raising
the fan-branch threshold from 3 to 4 saves ₹2.19 L a year and costs 40% of recall.

---

## What the synthetic ledger is calibrated to

Account-level Indian transaction data is not public and cannot be: customer records are
protected by RBI regulation, by PMLA confidentiality and by the DPDP Act 2023. Every
public AML dataset in existence is synthetic for that reason. So the ledger is invented,
but the system it moves through is not. `india_calibration.py` pins each generator
parameter to a published figure and records the source next to it:

| Calibrated | Pinned to |
|---|---|
| Channel mix across NEFT / RTGS / IMPS / UPI | RBI Payment Systems Report, NPCI UPI statistics |
| Average ticket size per rail | RBI Payment Systems Report |
| CTR threshold ₹10,00,000 | PML (Maintenance of Records) Rules, 2005, Rule 3 |
| RTGS ₹2,00,000 floor and per-rail ceilings | RBI RTGS System Regulations, NPCI circulars |
| Institution codes | real IFSC prefixes, synthetic account numbers |
| Operating rhythm, and how laundering skews off-hours | FATF typologies |

Accounts, counterparties and rings are invented; no real person, account or institution is
represented. `GET /api/export/provenance.json` returns every parameter with its source,
alongside the mix the generated ledger actually exhibits, so the calibration can be checked
rather than taken on trust.

---

## Public dataset adapters

Raw exports are auto-detected from their column signature, with no reformatting:

| Key | Dataset |
|---|---|
| `ibm_aml` | IBM Transactions for Anti Money Laundering (Kaggle) |
| `paysim` | PaySim mobile money (Kaggle) |
| `amlsim` | IBM AMLSim generator output |
| `elliptic` | Elliptic Bitcoin transaction graph |
| `generic` | `sender, receiver, amount` + optional `day`/`timestamp`, `channel`, `is_fraud` |

Drop a file into the Detection Lab, or `--csv <file> --limit N`. The datasets themselves
are large and separately licensed, so they are **not bundled**; `samples/` contains
schema-faithful files that verify each adapter parses. Those samples are random graphs
with random labels, and the detector correctly raises **zero alerts** on them: a negative
control showing it does not invent patterns in noise.

---

## Results on the bundled ledger

187 accounts (35 fraud-linked, 19 lawful look-alikes, 133 ordinary) · 301 transactions worth ₹10.72 Cr.

| Configuration | Precision | Recall | F1 | FP |
|---|---|---|---|---|
| L1 structuring only | 1.00 | 0.17 | 0.29 | 0 |
| + L2 fan patterns | 1.00 | 0.60 | 0.75 | 0 |
| + L3 cycles | 1.00 | 0.94 | 0.97 | 0 |
| + L4 pass-through (all rules) | 1.00 | 1.00 | 1.00 | 0 |
| + L9 evasion-adaptive | 1.00 | 1.00 | 1.00 | 0 |
| + L5/L6 corroboration (full) | **1.00** | **1.00** | **1.00** | **0** |
| *ML alone, no graph context* | *0.35* | *0.17* | *0.23* | *11* |

Robustness: mean Jaccard **0.97** across 16 independent threshold perturbations.
9 rings detected, from the **detected** alert sub-network and never from the labels.

The bundled data contains one deliberate bridge: a fan-in consolidation account that
immediately wires into a mule chain. Nothing tells the detector they are related. It
merges the fan ring and the mule chain into a single seven-account RING-01 on its own,
and the consolidation account is the highest-scoring in the case at 5, because three
independent layers converge on it: fan-in, pass-through and sub-network chokepoint.

### Reproducibility

A ring identifier is cited in the alert queue, in the exports and in every STR dossier,
so it has to mean the same thing tomorrow as it did today. Nothing in the pipeline is
ordered by set iteration or by traversal order, and the one stochastic model is seeded,
so `python generate_report.py` twice produces all 25 files in `outputs/` byte for byte
identical, the network PNG and the Excel workbook included, in separate processes and
under different values of `PYTHONHASHSEED`. The workbook needed help: an `.xlsx` is a zip,
and both its member timestamps and its `docProps` modified date come from the clock rather
than the data, so it is rewritten with the ledger's epoch on every member before it is
saved.

---

## Views

Overview · Alert queue · Network · Timeline · **Replay** · Rings · Propagation ·
**Data provenance** · STR dossier · Model evidence · **Adversarial test** ·
**Alert economics** · Detection lab · Exports

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | Entire application state |
| `POST /api/simulate` | Regenerate the ledger (seed, size) and analyse |
| `POST /api/reconfigure` | Re-run detection on the current ledger with new thresholds |
| `POST /api/upload` | Analyse an uploaded CSV; schema auto-detected |
| `GET /api/replay` | Day-by-day re-detection and latency (cached) |
| `GET /api/evasion` | Adversarial benchmark: two ledgers built to defeat the engine's own rules |
| `POST /api/economics` | Recost the alert population under new staffing assumptions |
| `GET /api/schemas` | Supported public dataset schemas |
| `GET /api/dossier/<account>` | STR dossier as JSON |
| `GET /api/subgraph/<account>?hops=n` | n-hop neighbourhood |
| `GET /api/export/provenance.json` | Calibration sources, and observed vs published channel mix |
| `GET /api/export/*.csv`, `/api/export/metrics.json` | All exports |

---

## Files

```
aml_engine.py        detection engine; pure library, no I/O, no globals
india_calibration.py published Indian figures the ledger is calibrated to, with sources
dataset_adapters.py  public AML dataset schema detection and translation
app.py               Flask API + static hosting
generate_report.py   headless run; writes outputs/ (CSVs, STRs, PNG, metrics)
audit.py             self-audit: ledger/graph integrity, deck sync, seed stability,
                     circularity probe, label audit
redteam.py           attacks the system the way a hostile reviewer would
static/              index.html · styles.css · app.js  (zero dependencies)
samples/             schema-faithful files that exercise each adapter
deck/                pitch deck, its charts, and the scripts that build both
sample_ledger.csv    tiny generic-schema ledger for smoke-testing upload
run.sh / run.bat     install requirements, then start the app
```

## Further reading

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module boundaries, the pipeline in detail, why the engine is a pure library |
| [API.md](API.md) | Every endpoint, request and response shape, and all tunables |
| [DATASETS.md](DATASETS.md) | The canonical ledger, each public-dataset adapter, bringing your own CSV |

---

## Regulatory basis

CTR threshold ₹10,00,000 under the PML (Maintenance of Records) Rules, 2005. STRs are
pattern-based with no amount floor, filed under Section 12 of the Prevention of
Money-Laundering Act, 2002 read with Rule 3, and reported to FIU-IND. Generated dossiers
follow that structure (Parts A–F).

All data in this prototype is synthetic. No real customer information is used.
