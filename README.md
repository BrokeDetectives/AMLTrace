# AMLTrace — Trace the Money. Expose the Network.

> **AMLTrace helps financial investigators move from a suspicious transaction to the network, timeline, evidence, and investigation behind it.**

**Team:** Broke Detectives
**Project:** AMLTrace  
**Hackathon:** Build $ Bank  
**Institution:** IGDTUW

---

## Live Demo

**Live Website:** https://aml-trace.vercel.app

**Product Demo:** [\[Watch Demo\]](https://drive.google.com/file/d/18gs7fiQkV0S2ZrcRuWyLhi_7Zyw-F4RO/view?usp=sharing)

**Pitch Deck:** [\[View Pitch Deck\]](https://docs.google.com/presentation/d/1cMzpih36HAQVnD4TPXzAoxlLLcKBRO-W/edit?usp=sharing&ouid=109609182894906747138&rtpof=true&sd=true)

---

# Product Demo

Add screenshots or a short GIF of the working AMLTrace dashboard here.

Recommended screenshots:

1. Overview / Dashboard
2. Alert Queue
3. Transaction Network
4. Laundering Timeline
5. Suspicious Ring / Investigation View
6. STR Dossier
7. Adversarial Test

AMLTrace is designed as an investigation console rather than simply a transaction-scoring dashboard.

---

# The Problem

Money laundering rarely happens through one suspicious transaction.

A laundering operation can involve multiple accounts, intermediaries, transaction chains, and time-separated movements of money.

For example:

```text
Account A
   │
   ├──────► Account B
   │             │
   ├──────► Account C
   │             │
   └──────► Account D
                 │
                 ▼
              Account E
```

Viewed individually, many of these transactions may appear ordinary.

Viewed together, they can reveal:

- Fund splitting and consolidation
- Mule or pass-through accounts
- Circular movement of funds
- Structuring around reporting thresholds
- Suspicious intermediary accounts
- Coordinated transaction networks

This creates a key challenge for AML systems:

> **The suspiciousness of a transaction often depends on the network around it.**

Traditional transaction-level monitoring can therefore leave investigators with a long list of alerts without enough context to understand **what actually happened**.

---

# Our Solution

**AMLTrace** approaches AML investigation as a **network-tracing problem**.

The system ingests a transaction ledger, constructs a directed money-flow graph, applies multiple detection layers, correlates the resulting evidence, and presents the investigation through an interactive interface.

```text
Transaction Ledger
       ↓
Data Processing
       ↓
Transaction Graph
       ↓
Detection Engine
       ↓
Risk Correlation
       ↓
┌─────────────────────────────┐
│ Alert Queue                 │
│ Transaction Network         │
│ Laundering Timeline         │
│ Suspicious Rings            │
│ Evidence & Model Analysis   │
│ STR Dossier                 │
└─────────────────────────────┘
```

The goal is not simply to answer:

> **"Is this account suspicious?"**

It is to answer:

> **"Why is it suspicious, how did the money move, who is connected, and what evidence can an investigator act on?"**

---

# Why Network-Based AML?

A transaction ledger contains more information than individual transaction amounts.

The relationships between accounts can reveal patterns that are difficult to see when transactions are considered independently.

AMLTrace therefore models transactions as a directed graph:

```text
        ┌──────────┐
        │ Account A│
        └────┬─────┘
             │
       ┌─────┼─────┐
       ▼     ▼     ▼
   Account B C   Account D
       │     │     │
       └─────┼─────┘
             ▼
         Account E
```

This allows the system to investigate:

- **Who sends money to whom?**
- **Where does money split?**
- **Where does it reconverge?**
- **Which accounts act as intermediaries?**
- **Does money eventually return to its origin?**
- **Which accounts connect otherwise separate suspicious groups?**

---

# What AMLTrace Contributes

AMLTrace combines several perspectives instead of depending on a single detector.

### Transaction Rules

Detect known suspicious transaction patterns such as structuring and pass-through behaviour.

### Graph Analysis

Identify relationships, transaction rings, fan-out/fan-in structures, and network chokepoints.

### Temporal Analysis

Understand how quickly money moves and reconstruct suspicious activity chronologically.

### Behavioural Analysis

Use account-level behavioural features to identify anomalies that can corroborate rule-based evidence.

### Adaptive Detection

Test whether suspicious actors can deliberately operate outside fixed detection thresholds.

### Investigation

Turn detection results into an investigator-facing workflow rather than stopping at a risk score.

---

# How It Works

```text
User / Bank Transaction Data
            ↓
      Dataset Adapter
            ↓
    Transaction Ledger
            ↓
      Directed Graph
            ↓
   ┌────────┴────────┐
   │ Detection Engine│
   └────────┬────────┘
            ↓
   ┌─────────┼──────────┐
   ▼         ▼          ▼
 Rules     Graph        ML
   │         │          │
   └─────────┼──────────┘
            ↓
       Risk Correlation
            ↓
    Investigation Engine
            ↓
  ┌──────────┼─────────────┐
  ▼          ▼             ▼
Alerts    Network      Timeline
  │          │             │
  └──────────┼─────────────┘
            ↓
        STR Dossier
```

The same underlying analysis engine powers both the dashboard and the offline report-generation pipeline.

---

# Detection Engine

AMLTrace currently contains **nine detection layers**.

| Layer | Detection | Type | Purpose |
|---|---|---|---|
| **L1** | Structuring / Smurfing | Rule | Detect transfers positioned just below the ₹10,00,000 CTR threshold and many-to-one funnels |
| **L2** | Fan-out → Fan-in | Graph | Detect funds split across intermediaries and reconverging |
| **L3** | Circular Flow | Graph + Time | Detect money that returns to its origin through transaction cycles |
| **L4** | Pass-through Conduit | Temporal | Detect accounts receiving and forwarding approximately similar amounts within a short period |
| **L5** | Sub-network Chokepoint | Graph | Identify strategically important accounts within suspicious sub-networks |
| **L6** | Behavioural Anomaly | ML | Identify unusual account-level behaviour using Isolation Forest |
| **L7** | Suspicion Propagation | Graph | Generate leads from accounts exposed to suspicious networks |
| **L8** | Laundering Timeline | Temporal | Reconstruct placement → layering → integration |
| **L9** | Threshold Evasion | Adaptive | Detect activity deliberately designed to stay outside fixed thresholds |

---

# Risk Scoring

AMLTrace combines evidence from multiple detection layers.

```text
Risk Score
    =
2 × Primary Typology Signals
    +
1 × Corroborating Signals
```

| Score | Classification |
|---:|---|
| **≥ 4** | CRITICAL |
| **3** | HIGH |
| **2** | MEDIUM |
| **1** | LOW |

A key design decision is that **ML anomaly detection is corroborating evidence rather than an independent source of regulatory alerts**.

This keeps the investigation pipeline focused on explainable evidence.

---

# Key Features

## 1. Alert Queue

Investigators receive a prioritised queue of suspicious accounts with the signals responsible for each alert.

Each alert can be traced back to the underlying transaction activity.

---

## 2. Transaction Network

The interactive graph exposes the movement of money between accounts.

Investigators can identify:

- Sources
- Intermediaries
- Destinations
- Suspicious clusters
- Transaction paths
- Network chokepoints
- Connected suspicious accounts

---

## 3. Suspicious Rings

AMLTrace identifies suspicious sub-networks from the transaction graph.

A ring identifier is preserved across the alert queue, exports, and investigation dossiers so that the same suspicious network can be consistently referenced throughout the workflow.

---

## 4. Laundering Timeline

AMLTrace reconstructs suspicious activity chronologically.

```text
Placement
    ↓
Initial suspicious deposits
    ↓
Layering
    ↓
Intermediary accounts
    ↓
Fund movement / splitting
    ↓
Consolidation
    ↓
Integration
```

This helps investigators understand the **sequence of events**, not just the final risk score.

---

## 5. Suspicion Propagation

Suspicious activity can expose previously unflagged accounts.

AMLTrace uses the transaction graph to generate these accounts as **investigative leads**, while keeping them outside the primary alerting pipeline.

This avoids allowing guilt-by-association to artificially inflate alert precision.

---

## 6. Explainable Evidence

Every alert can be traced back to the signals that generated it.

Example:

```text
ACCOUNT
ACC-1042

RISK
CRITICAL

SIGNALS
✓ Fan-in detected
✓ Pass-through behaviour
✓ Sub-network chokepoint

CONNECTED ACCOUNTS
6

SUSPICIOUS TRANSACTIONS
14

ESTIMATED SUSPICIOUS FLOW
₹18.4L
```

The investigator can then move from the alert into the network and timeline that produced it.

---

## 7. STR Investigation Dossier

AMLTrace generates structured investigation dossiers containing relevant account, transaction, network, and detection evidence.

The dossiers follow an FIU-IND-oriented STR structure for the prototype.

---

## 8. Data Provenance

The system records the parameters used to calibrate the synthetic transaction ledger and exposes their sources.

```text
GET /api/export/provenance.json
```

This allows the calibration to be inspected rather than treated as an unexplained assumption.

---

## 9. Detection Lab

AMLTrace can analyse uploaded CSV files and automatically identify supported dataset schemas.

This allows the same investigation engine to be tested beyond the bundled synthetic ledger.

---

# Adaptive Detection

## What happens when the launderer knows the rules?

Fixed rules can become predictable.

An adversary could attempt to:

- Stay below a reporting threshold
- Spread transactions over a longer period
- Change transaction timing
- Add unrelated transactions
- Reduce the apparent dedication of mule accounts
- Alter the number of transaction hops

AMLTrace therefore includes **L9: Threshold Evasion**.

Instead of adding another fixed threshold that an adversary can simply avoid, L9 looks for behavioural properties that arise from moving a particular amount of money through other people's accounts.

---

# Adversarial Testing

AMLTrace includes an adversarial testing framework designed to attack its own detection engine.

Two ledgers are used:

1. **Threshold-evasive relay ring**
2. **Peeling chain**, a typology not explicitly implemented as a detector

| Adversarial Ledger | Layers 1–8 | With L9 | Precision | FP |
|---|---:|---:|---:|---:|
| Threshold-evasive relay ring | 0.00 recall | **1.00 recall** | 1.00 | 0 |
| Peeling chain | 0.00 recall | **1.00 recall** | 1.00 | 0 |

The purpose of the benchmark is to test whether the adaptive layer generalises rather than simply fitting itself to the first adversarial example.

---

# Hard Negatives

A detector should not flag legitimate businesses simply because their transaction topology resembles a laundering pattern.

AMLTrace therefore contains **19 legitimate accounts deliberately designed to reproduce suspicious-looking transaction structures**.

| Legitimate Activity | Resembles | Why It Is Legitimate |
|---|---|---|
| Customer → contractors → shared distributor | Fan-out / Fan-in | Contractors maintain unrelated legitimate activity |
| Merchant ↔ PSP ↔ acquirer | Circular flow | Standing commercial settlement relationship |
| Escrow agent | Pass-through | Legitimate business model with many counterparties |
| Payroll bureau near CTR threshold | Structuring | Also performs transactions above the threshold |

The system uses economic and behavioural discriminators such as:

- Threshold avoidance
- Branch dedication
- Standing relationships
- Settlement-rail recurrence

Without these discriminators, pure topology matching produces substantially more false positives.

---

# Streaming Replay

AMLTrace can simulate the behaviour of the detector as transactions arrive over time.

The system re-runs detection using only the transactions available up to each day.

On the bundled ledger:

- **First alert possible:** Day 3
- **Mean detection lag:** 4.23 days
- **Median detection lag:** 4 days
- **Worst detection lag:** 17 days
- **Laundering-linked accounts eventually surfaced:** 35 / 35

The replay also highlights an important AML operational trade-off.

A batch detector can use the complete transaction history, while an early streaming detector has less historical evidence available.

AMLTrace makes this difference visible instead of presenting only a single batch performance number.

---

# Performance Metrics

The bundled synthetic ledger contains:

**187 accounts**

- 35 fraud-linked
- 19 legitimate hard negatives
- 133 ordinary accounts

**301 transactions**

**₹10.72 Cr total transaction value**

### Detection Results

| Configuration | Precision | Recall | F1 | FP |
|---|---:|---:|---:|---:|
| L1 Structuring | 1.00 | 0.17 | 0.29 | 0 |
| + L2 Fan patterns | 1.00 | 0.60 | 0.75 | 0 |
| + L3 Circular flows | 1.00 | 0.94 | 0.97 | 0 |
| + L4 Pass-through | 1.00 | 1.00 | 1.00 | 0 |
| + L9 Evasion adaptive | 1.00 | 1.00 | 1.00 | 0 |
| **Full system** | **1.00** | **1.00** | **1.00** | **0** |
| ML alone, without graph context | 0.35 | 0.17 | 0.23 | 11 |

Additional robustness testing produced a mean **Jaccard similarity of 0.97** across 16 independent threshold perturbations.

The system detected **9 suspicious rings** from the detected alert sub-network rather than relying on labels to define those rings.

---

# Alert Economics

Detection quality is only one part of the AML problem.

Every alert also consumes analyst time.

For the bundled ledger, AMLTrace estimates:

- **0.32 analyst FTE**
- **₹7.01L annual staffing cost**
- **₹1,646 per alert**
- **₹9.25L illicit flow surfaced per analyst hour**

The dashboard also provides a **threshold ↔ cost frontier** to explore the operational trade-off between detection performance and analyst workload.

---

# Indian Data Calibration

The prototype does not use real customer-level banking data.

Instead, AMLTrace uses a **synthetic transaction ledger calibrated against publicly available Indian financial-system information**.

| Calibrated Parameter | Reference |
|---|---|
| NEFT / RTGS / IMPS / UPI channel mix | RBI Payment Systems data and NPCI statistics |
| Average ticket size | RBI Payment Systems data |
| CTR threshold of ₹10,00,000 | PML (Maintenance of Records) Rules, 2005 |
| RTGS limits | RBI / NPCI regulations and circulars |
| Institution codes | Real IFSC prefixes with synthetic account numbers |
| Operating rhythm / AML behaviour | FATF typologies |

All accounts, counterparties, and suspicious rings are synthetic.

**No real customer, account, or institution is represented.**

---

# Supported Datasets

AMLTrace supports automatic schema detection for:

| Adapter | Dataset |
|---|---|
| `ibm_aml` | IBM Transactions for Anti Money Laundering |
| `paysim` | PaySim mobile money |
| `amlsim` | IBM AMLSim generator output |
| `elliptic` | Elliptic Bitcoin transaction graph |
| `generic` | `sender, receiver, amount` + optional time/channel/fraud fields |

A supported CSV can be uploaded through the **Detection Lab** or analysed from the command line.

---

# Technology Stack

| Component | Technology |
|---|---|
| Backend | Python |
| Web Framework | Flask |
| Frontend | HTML, CSS, JavaScript |
| Graph Analysis | Graph-based transaction analysis |
| Machine Learning | Isolation Forest |
| Data Input | CSV |
| Exports | CSV, JSON, PNG, Excel |
| Runtime | Local / offline compatible |

The frontend is implemented without external CDN dependencies, allowing the prototype to run on a demo laptop without an internet connection.

---

# Architecture

```text
                         ┌──────────────────┐
                         │ Transaction Data │
                         └────────┬─────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │ Dataset Adapters    │
                       └─────────┬───────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │    AML Engine       │
                       │                     │
                       │ L1 Structuring      │
                       │ L2 Fan-out/Fan-in   │
                       │ L3 Circular Flow    │
                       │ L4 Pass-through     │
                       │ L5 Chokepoint       │
                       │ L6 ML Anomaly       │
                       │ L7 Propagation      │
                       │ L8 Timeline         │
                       │ L9 Evasion          │
                       └─────────┬───────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │ Risk Correlation    │
                       └─────────┬───────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │ Investigation API   │
                       └─────────┬───────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │  AMLTrace Dashboard │
                       └─────────────────────┘
```

---

# Reproducibility

The analysis pipeline is designed to produce deterministic results.

The stochastic ML component is seeded, graph traversal is deterministic, and the report-generation pipeline produces reproducible artefacts.

Run:

```bash
python generate_report.py
```

to execute the complete analysis pipeline.

Generated artefacts are written to:

```text
outputs/
```

---

# Getting Started

## Requirements

- Python 3.x
- pip

## Installation

```bash
pip install -r requirements.txt
```

## Start AMLTrace

```bash
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

---

# Generate an Offline Report

The complete engine can also be run without starting the web application.

```bash
python generate_report.py
```

To analyse another CSV:

```bash
python generate_report.py --csv your_dataset.csv --limit 50000
```

---

# API

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | Complete application state |
| `POST /api/simulate` | Generate and analyse a ledger |
| `POST /api/reconfigure` | Re-run detection with different thresholds |
| `POST /api/upload` | Analyse an uploaded CSV |
| `GET /api/replay` | Day-by-day detection replay |
| `GET /api/evasion` | Adversarial benchmark |
| `POST /api/economics` | Alert economics analysis |
| `GET /api/schemas` | Supported dataset schemas |
| `GET /api/dossier/<account>` | STR investigation dossier |
| `GET /api/subgraph/<account>?hops=n` | Account neighbourhood |
| `GET /api/export/provenance.json` | Calibration provenance |
| `GET /api/export/*.csv` | Data exports |
| `GET /api/export/metrics.json` | Detection metrics |

---

# Repository Structure

```text
AMLTrace/
│
├── deck/
├── outputs/
├── samples/
├── static/
│
├── .gitignore
├── API.md
├── ARCHITECTURE.md
├── DATASETS.md
├── README.md
│
├── aml_engine.py
├── app.py
├── audit.py
├── dataset_adapters.py
├── generate_report.py
├── india_calibration.py
├── redteam.py
│
├── requirements.txt
├── run.bat
├── run.sh
└── sample_ledger.csv
```

### Core Modules

| File | Purpose |
|---|---|
| `aml_engine.py` | Core AML detection engine |
| `india_calibration.py` | Indian financial-system calibration |
| `dataset_adapters.py` | Public AML dataset schema detection |
| `app.py` | Flask API and application hosting |
| `generate_report.py` | Headless analysis and report generation |
| `audit.py` | Self-audit and validation |
| `redteam.py` | Adversarial testing |

---

# Validation

AMLTrace includes validation and self-audit tooling covering:

- Ledger and graph integrity
- Detection consistency
- Seed stability
- Circularity testing
- Label auditing
- Adversarial testing
- Threshold perturbation
- Reproducibility

Run the relevant validation scripts from the repository to reproduce the reported results.

---

# Security & Data

- All bundled transaction data is synthetic.
- No real customer information is included.
- API credentials are not required for the core local prototype.
- Generated outputs contain synthetic account and transaction information.
- Dataset adapters are designed to operate on structured transaction data.

---

# Limitations

AMLTrace is a **hackathon prototype**, not a production AML compliance system.

Current limitations include:

- The bundled transaction ledger is synthetic.
- Real-world AML systems operate on substantially larger and more heterogeneous datasets.
- Production deployment would require institution-specific calibration and validation.
- Regulatory reporting requires appropriate human review and institutional controls.
- Synthetic-data performance should not be interpreted as production-level detection performance.
- Detection thresholds and behavioural assumptions would require validation against real institutional data.

---

# Future Vision

AMLTrace is designed as a foundation for a broader financial-crime investigation platform.

Potential future directions include:

- Real-time transaction-stream processing
- Institution-specific model calibration
- Larger multi-bank transaction graphs
- Cross-institution network analysis
- Continuous behavioural profiling
- Investigator feedback loops
- Case management and collaboration
- Automated evidence collection
- Human-in-the-loop alert disposition
- Production-scale deployment

The long-term objective is to move AML systems from **alert generation** toward **continuous financial-network investigation**.

---

# Team

## Team Name

**Broke Detectives**

### Team Members

| Member | Contribution |
|---|---|
| **Ujjwal Verma** | Ideation, problem analysis, solution design and product direction |
| **Varun Agarwal** | Ideation, AML workflow design, feature planning and validation |
| **Shubh Singh** | Ideation, research, testing and product refinement |

### Team Contribution

The team collectively contributed to **problem understanding, ideation, AML domain research, solution design, feature definition, investigation workflow design, testing, validation, and product refinement**.

The prototype was developed through an **AI-assisted development workflow**, with the team directing the product requirements, architecture, behaviour, testing, and final refinement.

---

# Acknowledgements

AMLTrace was developed as a prototype for **Build $ Bank at IGDTUW**.

The project uses publicly available AML, financial-system, and regulatory information to calibrate its synthetic demonstration data.

---

# Current Scope

AMLTrace currently focuses on one central problem:

> **How can financial institutions move from a suspicious transaction alert to an understandable investigation of the network behind it?**

The current prototype demonstrates this through:

**Transaction Data → Detection → Network → Timeline → Evidence → Investigation → STR Dossier**

---

# Final Thought

> **Don't just flag the transaction. Trace the money.**

**AMLTrace**

**Build $ Bank · IGDTUW**
