# Datasets

AMLTrace analyses any transaction ledger it can reduce to four columns:
**sender, receiver, amount, day**. Everything else (schema detection, timestamp
conversion, label mapping) happens in [dataset_adapters.py](dataset_adapters.py) before
the engine sees a row.

---

## The canonical shape

| Column | Type | Required | Notes |
|---|---|---|---|
| `sender` | str | yes | Debited account |
| `receiver` | str | yes | Credited account |
| `amount` | number | yes | Non-numeric values become 0 rather than failing the load |
| `day` | int | derived | 1-based day index; derived from a timestamp when one exists, else 1 |
| `txn_id` | str | no | Source reference; carried into the STR dossiers |
| `channel` | str | no | NEFT / RTGS / UPI / CASH / card; display only, no rule reads it |
| `is_fraud` | 0/1 | no | Ground truth. Present → precision/recall panels. Absent → the app reports alerts and says nothing about accuracy |

Self-transfers (`sender == receiver`) are dropped during translation.

---

## Supported schemas

Detection is by **column signature**, the presence of a set of columns rather than the filename,
so a raw Kaggle export works unmodified. The first signature that matches wins, in the
order below, with `generic` as the fallback.

| Key | Dataset | Signature columns |
|---|---|---|
| `ibm_aml` | IBM Transactions for Anti Money Laundering | `Account`, `Amount Paid`, `Is Laundering` |
| `paysim` | PaySim mobile money | `nameOrig`, `nameDest`, `isFraud` |
| `amlsim` | IBM AMLSim generator output | `orig_acct`, `bene_acct`, `base_amt` |
| `elliptic` | Elliptic Bitcoin transaction graph | `txId1`, `txId2` |
| `generic` | Any ledger | `sender`, `receiver`, `amount` (case-insensitive) |

`GET /api/schemas` returns this table at runtime; the Detection Lab renders it in the
upload panel.

### What each adapter does

**`ibm_aml`**: the file has two columns both named `Account`, so pandas suffixes the
second. Each account is qualified with its bank (`From Bank:Account`) so identifiers stay
unique across institutions. `Amount Paid` is the value; `Payment Format` becomes the
channel; `Timestamp` becomes the day index; `Is Laundering` becomes the label.

**`paysim`**: `step` is one hour of simulated time, so days are `step // 24 + 1`. `type`
(CASH_OUT, TRANSFER, PAYMENT…) becomes the channel; `isFraud` becomes the label.
`isFlaggedFraud` is the simulator's own rule output and is deliberately ignored, since comparing
against it would be scoring one detector on another's opinion.

**`amlsim`**: `orig_acct` / `bene_acct` / `base_amt`, with `tran_timestamp` for the day
index, `tx_type` as the channel and `is_sar` as the label.

**`elliptic`**: an edge list with no amounts and no time. Amount is set to a constant 1
and every edge to day 1, which means the value-based and temporal layers (L1, L4, parts of
L3) **stay silent rather than invent numbers**. What survives is the graph structure, which
is the part this system reasons over anyway. When a `class` column is present, class 1
(illicit) becomes the label.

**`generic`**: column names are lowercased and passed straight through. This is the path
for your own data.

---

## Bringing your own ledger

The minimum viable file:

```csv
sender,receiver,amount
ACC_A,ACC_B,950000
ACC_B,ACC_C,940000
```

A more useful one:

```csv
txn_id,sender,receiver,amount,day,channel,is_fraud
T001,ACC_RETAIL_A,ACC_MULE_1,950000,1,RTGS,1
T002,ACC_RETAIL_A,ACC_MULE_1,940000,2,RTGS,1
```

That is exactly [sample_ledger.csv](sample_ledger.csv), a small generic-schema file for
smoke-testing the upload path.

Either supply `day` as a 1-based integer, or supply a `timestamp` column and let the
adapter derive it from the earliest row. Without either, every transaction lands on day 1
and the temporal layers have nothing to work with.

---

## Loading

In the app: Detection Lab, upload panel. Uploads are capped at 16 MB, and a **limit**
field keeps the first N rows, which is how a multi-GB public dataset becomes usable in a
demo.

Headless:

```bash
python generate_report.py --csv HI-Small_Trans.csv --limit 50000
```

Via the API:

```bash
curl -s -X POST localhost:5000/api/upload -F file=@paysim.csv -F limit=20000
```

From Python:

```python
import dataset_adapters as A, aml_engine as E
df, schema = A.load_any("HI-Small_Trans.csv", limit=50000)
ledger, truth, meta = E.load_dataset_from_records(df.to_dict("records"))
state = E.analyse(ledger, truth, E.Config(), meta)
```

An unrecognised file raises a `ValueError` naming the columns it needed; the API returns
that message verbatim as a `400`.

---

## Why the public datasets are not bundled

They are large and separately licensed. `samples/` instead contains four small
**schema-faithful** files that exercise each adapter end to end:

```
samples/ibm_aml_style.csv    samples/paysim_style.csv
samples/amlsim_style.csv     samples/elliptic_style.csv
```

They prove the parsers work. They are **not** benchmarks: the graphs are random and the
labels are random, so no real typology exists in them.

That makes them a useful negative control. Run the detector on them and it raises
**zero alerts**: it does not invent patterns in noise. A detector that scored well on
random graphs with random labels would be telling you something about itself, not about
the data.

---

## Scale

The bundled synthetic ledger is 187 accounts and 301 transactions, deliberately small
enough to read by hand. Cycle enumeration (L3) is the limiting factor on large graphs;
`cycle_max_len` bounds it, and `--limit` bounds the input. For a first pass on a public
dataset, start at 50,000 rows and raise it once you have seen how long the run takes on
your machine.
