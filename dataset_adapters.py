"""
Adapters for the public AML datasets people actually benchmark on.

The engine wants one shape: sender, receiver, amount, day. Every public dataset
uses a different one. These adapters detect the schema from its column signature
and translate, so a judge can drag a raw Kaggle export straight into the app.

Supported
  ibm_aml   IBM Transactions for Anti Money Laundering (Kaggle, "HI/LI-Small" etc.)
  paysim    PaySim mobile-money simulation (Kaggle)
  amlsim    IBM AMLSim generator output
  elliptic  Elliptic Bitcoin dataset (edge list + class file)
  generic   sender/receiver/amount, with optional day, timestamp, is_fraud

None of these are bundled - they are large and separately licensed. Point the
app at your own copy:

    python generate_report.py --csv HI-Small_Trans.csv
    (or drop the file into the Detection Lab's upload panel)
"""

from __future__ import annotations

import pandas as pd

from aml_engine import LedgerError

SCHEMAS = {
    "ibm_aml": {
        "label": "IBM Transactions for Anti Money Laundering",
        "signature": {"Account", "Amount Paid", "Is Laundering"},
        "note": "Kaggle: ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
    },
    "paysim": {
        "label": "PaySim mobile money",
        "signature": {"nameOrig", "nameDest", "isFraud"},
        "note": "Kaggle: ealaxi/paysim1",
    },
    "amlsim": {
        "label": "IBM AMLSim",
        "signature": {"orig_acct", "bene_acct", "base_amt"},
        "note": "github.com/IBM/AMLSim transaction log",
    },
    "elliptic": {
        "label": "Elliptic Bitcoin transaction graph",
        "signature": {"txId1", "txId2"},
        "note": "Kaggle: ellipticco/elliptic-data-set (edgelist; no amounts)",
    },
    "generic": {
        "label": "Generic ledger",
        "signature": {"sender", "receiver", "amount"},
        "note": "sender, receiver, amount [, day | timestamp, txn_id, channel, is_fraud]",
    },
}


def detect_schema(columns) -> str:
    cols = set(map(str, columns))
    for name in ("ibm_aml", "paysim", "amlsim", "elliptic"):
        if SCHEMAS[name]["signature"] <= cols:
            return name
    if SCHEMAS["generic"]["signature"] <= cols:
        return "generic"
    lowered = {c.lower() for c in cols}
    if SCHEMAS["generic"]["signature"] <= lowered:
        return "generic"
    raise LedgerError(
        "Unrecognised ledger. Supply at least sender, receiver and amount columns, "
        "or use a raw export of one of: " + ", ".join(
            SCHEMAS[k]["label"] for k in ("ibm_aml", "paysim", "amlsim", "elliptic")))


def _require(df: pd.DataFrame, schema: str, columns) -> None:
    """A schema is recognised from a signature of two or three columns, which is
    not the same as the file carrying everything the translation needs. A trimmed
    export, or one saved from a spreadsheet with columns hidden, matches the
    signature and then fails somewhere inside pandas. Naming the missing columns
    here turns that into something the person holding the file can act on."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        label = SCHEMAS[schema]["label"]
        article = "an" if label[0] in "AEIOU" else "a"
        raise LedgerError(
            f"This looks like {article} {label} export, but "
            f"{'column' if len(missing) == 1 else 'columns'} {', '.join(repr(c) for c in missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing. Supply the full export, or rename "
            f"your columns to sender, receiver and amount to load it as a generic ledger.")


def _days_from_timestamps(series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce", format="mixed")
    if ts.isna().all():
        return pd.Series([1] * len(series))
    origin = ts.min()
    return ((ts - origin).dt.total_seconds() // 86400 + 1).fillna(1).astype(int)


def to_canonical(df: pd.DataFrame, schema: str | None = None) -> tuple[pd.DataFrame, str]:
    """Translate any supported schema into sender/receiver/amount/day/... ."""
    schema = schema or detect_schema(df.columns)

    if schema == "ibm_aml":
        _require(df, schema, ("From Bank", "To Bank", "Amount Paid", "Timestamp", "Is Laundering"))
        # Account columns repeat, so pandas suffixes the second one. Qualify each
        # account with its bank so identifiers are unique across institutions.
        acct_cols = [c for c in df.columns if str(c).startswith("Account")]
        a_from, a_to = acct_cols[0], acct_cols[-1]
        out = pd.DataFrame({
            "sender": df["From Bank"].astype(str) + ":" + df[a_from].astype(str),
            "receiver": df["To Bank"].astype(str) + ":" + df[a_to].astype(str),
            "amount": pd.to_numeric(df["Amount Paid"], errors="coerce").fillna(0),
            "day": _days_from_timestamps(df["Timestamp"]),
            "channel": df.get("Payment Format", "UNKNOWN"),
            "is_fraud": pd.to_numeric(df["Is Laundering"], errors="coerce").fillna(0).astype(int),
        })

    elif schema == "paysim":
        _require(df, schema, ("nameOrig", "nameDest", "amount", "step", "isFraud"))
        out = pd.DataFrame({
            "sender": df["nameOrig"].astype(str),
            "receiver": df["nameDest"].astype(str),
            "amount": pd.to_numeric(df["amount"], errors="coerce").fillna(0),
            # PaySim "step" is one hour of simulated time
            "day": (pd.to_numeric(df["step"], errors="coerce").fillna(1) // 24 + 1).astype(int),
            "channel": df.get("type", "UNKNOWN"),
            "is_fraud": pd.to_numeric(df["isFraud"], errors="coerce").fillna(0).astype(int),
        })

    elif schema == "amlsim":
        _require(df, schema, ("orig_acct", "bene_acct", "base_amt"))
        ts = df.get("tran_timestamp")
        # SAR labels are optional in an AMLSim run, and a missing column has to
        # become a column of zeros rather than the scalar `df.get` falls back to;
        # a scalar has none of the series methods the conversion below calls, so
        # an unlabelled log used to fail here instead of loading unlabelled.
        sar = df["is_sar"] if "is_sar" in df.columns else pd.Series(0, index=df.index)
        out = pd.DataFrame({
            "sender": df["orig_acct"].astype(str),
            "receiver": df["bene_acct"].astype(str),
            "amount": pd.to_numeric(df["base_amt"], errors="coerce").fillna(0),
            "day": _days_from_timestamps(ts) if ts is not None else 1,
            "channel": df.get("tx_type", "UNKNOWN"),
            "is_fraud": pd.to_numeric(sar, errors="coerce").fillna(0).astype(int),
        })

    elif schema == "elliptic":
        _require(df, schema, ("txId1", "txId2"))
        # An edge list with no amounts and no time. We still get the graph, which
        # is the part this system reasons over; amounts become a constant so the
        # value-based layers stay silent rather than lying.
        out = pd.DataFrame({
            "sender": df["txId1"].astype(str),
            "receiver": df["txId2"].astype(str),
            "amount": 1,
            "day": 1,
            "channel": "BTC",
        })
        if "class" in df.columns:
            out["is_fraud"] = (df["class"].astype(str) == "1").astype(int)

    else:                                              # generic
        # Headers are matched case-insensitively, so "Amount" and "amount" in
        # the same file collapse onto one name and every later reference to it
        # returns a two-column frame instead of a series. Every operation after
        # that fails somewhere deep in pandas, describing an alignment problem
        # rather than the duplicated header that caused it. The first column
        # wins, which is the one a reader of the file would assume.
        out = df.rename(columns={c: str(c).lower() for c in df.columns}).copy()
        if out.columns.duplicated().any():
            dupes = sorted(set(out.columns[out.columns.duplicated()]))
            out = out.loc[:, ~out.columns.duplicated()]
        else:
            dupes = []
        _require(out, "generic", ("sender", "receiver", "amount"))
        if dupes:
            out.attrs["dropped_duplicate_columns"] = dupes

    out = out[out["sender"].astype(str) != out["receiver"].astype(str)]
    return out.reset_index(drop=True), schema


def load_any(path: str, schema: str | None = None, limit: int | None = None) -> tuple[pd.DataFrame, str]:
    """Read a CSV of any supported schema. `limit` samples the first N rows,
    which is how you make a multi-GB public dataset usable in a demo."""
    df = pd.read_csv(path, nrows=limit, encoding="utf-8-sig", low_memory=False)
    return to_canonical(df, schema)


def describe() -> list[dict]:
    return [{"key": k, "label": v["label"], "note": v["note"],
             "columns": sorted(v["signature"])} for k, v in SCHEMAS.items()]
