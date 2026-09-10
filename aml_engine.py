"""
AMLTrace - Tracing Financial Crime Across Patterns
==================================================
Detection engine. Pure library: no printing, no blocking I/O, no global state.
Everything is a function of (transactions, config) so the web layer can re-run
the whole pipeline live when an analyst moves a threshold slider.

Regulatory frame: PMLA 2002 / PML (Maintenance of Records) Rules 2005, FIU-IND.
  - CTR  : cash transaction reporting threshold, Rs 10,00,000
  - STR  : suspicious transaction report, pattern-based, NO amount floor
  - CBWTR / NTR are out of scope for this prototype.

Detection layers
  L1 Structuring / smurfing      (rule, primary)
  L2 Fan-out -> fan-in           (graph, primary)
  L3 Circular flows              (graph, primary)
  L4 Pass-through mule conduits  (temporal, primary)
  L5 Betweenness centrality      (graph, corroborating)
  L6 IsolationForest anomaly     (ML,    corroborating)
  L7 Suspicion propagation       (graph, lead generation - does NOT flag)
  L8 Laundering timeline         (temporal reconstruction + stage attribution)
  L9 Threshold evasion           (adaptive, primary)

L1 to L4 are published rules with numbers in them, and a number a launderer can
read is a number they can sit just outside. L9 exists because of that: it scores
scheduling rhythm, value conservation along a relay and net exposure once cover
traffic is netted out, none of which is a threshold anyone can step around, and
it infers whichever reporting line a run of deposits is hugging rather than
compiling India's CTR in. Our own red-team ledger, built to defeat L1 to L4
deliberately, goes from recall 0.00 to 1.00 with L9 switched on.

Corroborating layers can only raise the risk score of an account that a primary
layer already flagged. That is a deliberate design choice: an unsupervised
anomaly score is not, on its own, a defensible basis for an STR.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, timedelta

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

import india_calibration as IN

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

CTR_THRESHOLD = 1_000_000          # Rs 10,00,000
EPOCH = date(2026, 8, 1)           # day 1 of the observation window


class LedgerError(ValueError):
    """A rejection whose text is meant to be read by whoever supplied the file.

    Anything else that goes wrong during ingest is an internal fault, and its
    message describes library internals rather than the analyst's file. Keeping
    the two apart is what lets the web layer forward one and swallow the other
    instead of guessing from the exception type.
    """


_FORMULA_LEAD = "=+-@"
_CONTROL_LEAD = "\t\r\n"


def csv_safe(value) -> str:
    """Neutralise a spreadsheet formula hiding in a cell.

    Account identifiers, channel names and the free text quoted back in the
    grounds of suspicion all originate in the ledger, and a ledger is often a
    third-party export. Excel and LibreOffice read a cell beginning with =, +,
    - or @ as a formula and evaluate it on open, so an identifier of
    ``=cmd|' /C calc'!A0`` in a file the analyst was told is evidence runs on
    the analyst's machine. Every export this system writes is opened in a
    spreadsheet by the person least able to afford that.

    A leading apostrophe is the spreadsheet's own escape and disappears on
    display. Negative numbers begin with a minus and are still numbers, so they
    are left alone rather than turned into text in an amount column.
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return text
    if text[0] in _CONTROL_LEAD:
        return "'" + text
    if text[0] in _FORMULA_LEAD:
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text


def csv_safe_frame(df: pd.DataFrame) -> pd.DataFrame:
    """``csv_safe`` over the text columns of a frame, leaving numerics alone.

    Text is selected by asking pandas, not by testing for ``object`` dtype.
    Pandas 3 gives a column of strings a dedicated string dtype, so the older
    test matched nothing and the guard silently protected an empty set of
    columns - which is the worst way for a guard to fail.
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(csv_safe)
    return out


def _coerce_bool(v) -> bool:
    """Parse a boolean the way an HTTP client actually sends one."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


# Inclusive (min, max) for every numeric tunable. These are not style limits:
# each one bounds a loop or a threshold that untrusted input reaches directly.
CONFIG_BOUNDS: dict[str, tuple] = {
    "structuring_floor_pct": (0.10, 0.999),
    "structuring_min_count": (2, 100),
    "structuring_window_days": (1, 365),
    "smurf_min_senders": (2, 100),
    "structuring_min_crossers": (1, 100),
    "structuring_crosser_share": (0.0, 1.0),
    "agg_min_count": (2, 200),
    "agg_window_days": (1, 365),
    "fan_min_branches": (2, 50),
    "fan_min_dedication": (0.0, 1.0),
    "fan_max_retention": (0.0, 1.0),
    "fan_max_inflation": (1.0, 100.0),
    "cycle_max_len": (3, 9),
    "cycle_max_span_slack": (0, 60),
    "cycle_max_edge_count": (1, 1000),
    "cycle_min_conservation": (0.0, 1.0),
    "pt_ratio_low": (0.0, 1.0),
    "pt_ratio_high": (0.0, 2.0),
    "pt_window_days": (0, 60),
    "pt_min_amount": (0, 10_000_000_000),
    "pt_max_counterparties": (1, 200),
    "pt_max_repeats": (1, 1000),
    "pt_rail_span_days": (1, 365),
    "adaptive_min_hops": (3, 8),
    "adaptive_hop_low": (0.0, 1.0),
    "adaptive_hop_high": (1.0, 5.0),
    "adaptive_min_conservation": (0.0, 1.0),
    "adaptive_rhythm_cv": (0.0, 2.0),
    "adaptive_amount_cv": (0.0, 2.0),
    "adaptive_net_dedication": (0.0, 1.0),
    "adaptive_max_edge_count": (1, 1000),
    "adaptive_min_amount": (0, 10_000_000_000),
    "adaptive_agg_min_count": (2, 200),
    "adaptive_agg_amount_cv": (0.0, 2.0),
    "adaptive_agg_hug": (0.0, 1.0),
    "adaptive_agg_multiple": (1.0, 100.0),
    "centrality_z": (0.0, 10.0),
    "ml_cutoff": (-1.0, 1.0),
    "prop_alpha": (0.05, 0.99),
    "prop_max_hops": (1, 6),
    "prop_decay": (0.01, 1.0),
}


@dataclass
class Config:
    """Every tunable the pipeline exposes. The UI edits this object."""
    # L1 structuring
    structuring_floor_pct: float = 0.85   # lower edge of the "just under CTR" band
    structuring_min_count: int = 4
    structuring_window_days: int = 5
    smurf_min_senders: int = 3
    structuring_require_avoidance: bool = True   # ignore pairs that DO cross the CTR line
    structuring_min_crossers: int = 2      # one over-CTR txn must not buy an exemption
    structuring_crosser_share: float = 0.25  # ...nor may a token minority of them
    agg_min_count: int = 5                 # L1(c) aggregation: sub-threshold legs summing over CTR
    agg_window_days: int = 7
    # L2 fan
    fan_min_branches: int = 3
    fan_min_dedication: float = 0.75   # branches must exist mainly to serve this fan
    fan_max_retention: float = 0.60    # branches that keep most of the money are not conduits
    fan_max_inflation: float = 2.00    # ...nor are branches paying out of unrelated funds
    # L3 cycles
    cycle_max_len: int = 6
    cycle_max_span_slack: int = 3      # loop must close within (len + slack) days
    cycle_require_monotone: bool = True
    cycle_max_edge_count: int = 2      # a standing trading relationship is not layering
    cycle_min_conservation: float = 0.25  # value must actually survive the loop
    # L4 pass-through
    pt_ratio_low: float = 0.93
    pt_ratio_high: float = 1.00
    pt_window_days: int = 1
    pt_min_amount: int = 250_000
    pt_max_counterparties: int = 4
    pt_max_repeats: int = 2            # a rail that settles every fortnight is not a mule
    pt_rail_span_days: int = 7
    # L9 evasion-adaptive
    adaptive_min_hops: int = 3
    adaptive_hop_low: float = 0.60        # a hop must carry most of what arrived
    adaptive_hop_high: float = 1.05       # ...and not be topped up from elsewhere
    adaptive_min_conservation: float = 0.35
    adaptive_rhythm_cv: float = 0.30      # spacing this regular is scheduled, not traded
    adaptive_amount_cv: float = 0.30      # one parcel keeps its size
    adaptive_net_dedication: float = 0.60
    adaptive_max_edge_count: int = 2      # a standing corridor is not a relay
    adaptive_min_amount: int = 100_000
    adaptive_agg_min_count: int = 5       # tranches needed before a pair is a pattern
    adaptive_agg_amount_cv: float = 0.35  # hand-sized tranches barely vary
    adaptive_agg_hug: float = 0.75        # how close to the inferred line they sit
    adaptive_agg_multiple: float = 2.0    # total must clear the line several times over
    # L5 centrality
    centrality_z: float = 2.0
    # L6 ML
    ml_cutoff: float = -0.05
    ml_confirming_only: bool = True
    # L7 propagation
    prop_alpha: float = 0.85
    prop_max_hops: int = 3
    prop_decay: float = 0.45
    # layer switches (ablation)
    use_structuring: bool = True
    use_fan: bool = True
    use_cycles: bool = True
    use_pass_through: bool = True
    use_adaptive: bool = True
    use_centrality: bool = True
    use_ml: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Config":
        """Build a Config from untrusted input (the UI POSTs this straight through).

        Two things have to happen here that plain ``type(cur)(v)`` does not do.
        First, booleans: ``bool("false")`` is True, so a checkbox serialised as a
        string would silently invert a layer switch. Second, bounds: every
        numeric tunable feeds a loop bound or a threshold, so an out-of-range
        value is more than a bad setting: ``cycle_max_len=500`` or
        ``fan_min_branches=0`` turns the pipeline into an unbounded enumeration,
        and ``structuring_min_count=0`` makes every pair a match. Values are
        coerced then clamped into the range the detector is defined over.
        """
        cfg = cls()
        for k, v in (d or {}).items():
            if not hasattr(cfg, k) or v is None:
                continue
            cur = getattr(cfg, k)
            try:
                if isinstance(cur, bool):
                    val = _coerce_bool(v)
                elif isinstance(cur, int):
                    val = int(round(float(v)))
                elif isinstance(cur, float):
                    val = float(v)
                else:
                    val = type(cur)(v)
            except (TypeError, ValueError):
                continue
            if isinstance(val, float) and not math.isfinite(val):
                continue
            lo, hi = CONFIG_BOUNDS.get(k, (None, None))
            if lo is not None:
                val = max(lo, min(hi, val))
            setattr(cfg, k, val)
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


LAYER_META = {
    "STRUCTURING":   {"label": "Structuring / smurfing", "role": "primary",       "weight": 2},
    "FAN":           {"label": "Fan-out / fan-in",       "role": "primary",       "weight": 2},
    "CYCLE":         {"label": "Circular flow",          "role": "primary",       "weight": 2},
    "PASS-THROUGH":  {"label": "Pass-through conduit",   "role": "primary",       "weight": 2},
    "ADAPTIVE":      {"label": "Threshold evasion",      "role": "primary",       "weight": 2},
    "CENTRALITY":    {"label": "Network chokepoint",     "role": "corroborating", "weight": 1},
    "ML-ANOMALY":    {"label": "Behavioural anomaly",    "role": "corroborating", "weight": 1},
}

FEATURE_NAMES = [
    "out_count", "in_count", "total_sent", "total_received",
    "sent_std", "unique_out_counterparties", "unique_in_counterparties",
    "amount_below_ctr_ratio", "median_hold_days",
]


# --------------------------------------------------------------------------
# Synthetic data generation
# --------------------------------------------------------------------------

def _bank_accounts(rng, n, prefix_pool=None):
    """Bank-coded account identifiers: real IFSC prefixes, synthetic numbers.

    A ledger of accounts called N1..N135 tells a reviewer nothing about whether
    the model understands the system it claims to police. Real prefixes, weighted
    by retail footprint, cost nothing and make the output legible to anyone who
    works in payments.
    """
    codes = [b[0] for b in IN.BANKS]
    weights = [b[2] for b in IN.BANKS]
    out, seen = [], set()
    while len(out) < n:
        code = rng.choices(codes, weights=weights, k=1)[0]
        acc = f"{code}{rng.randint(100000, 999999)}"
        if acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def _pick_channel(rng, illicit=False):
    """Sample a rail from the real RBI volume shares."""
    chans = list(IN.CHANNEL_MIX)
    weights = [IN.CHANNEL_MIX[c][0] for c in chans]
    if illicit:
        # laundering avoids RTGS: it settles gross, in real time, under the
        # bank's nose, and it is the most heavily scrutinised rail there is
        weights = [w * (0.15 if c == "RTGS" else 1.0) for c, w in zip(chans, weights)]
    return rng.choices(chans, weights=weights, k=1)[0]


def _amount_for(rng, channel, illicit=False, target=None):
    """An amount that is plausible for this rail.

    Sizes are drawn log-normally around the rail's real average ticket, then
    clamped to the rail's regulatory floor and ceiling. `target` overrides the
    draw but is still clamped, so a typology can ask for a specific sum without
    producing something the rail could not carry.
    """
    if target is None:
        mean = IN.AVERAGE_TICKET[channel]
        if illicit:
            mean *= IN.ILLICIT_TICKET_MULTIPLIER.get(channel, 1)
        amount = rng.lognormvariate(math.log(max(mean, 1)), 0.75)
    else:
        amount = float(target)
    cap = IN.CHANNEL_CAP.get(channel)
    if cap:
        amount = min(amount, cap)
    if channel == "RTGS":
        amount = max(amount, IN.RTGS_MINIMUM)
    return max(1000, int(round(amount, -2)))


def _channel_for_amount(rng, amount, illicit=False):
    """Choose a rail that can actually carry this amount."""
    ok = [c for c in IN.CHANNEL_MIX
          if (IN.CHANNEL_CAP.get(c) is None or amount <= IN.CHANNEL_CAP[c])
          and not (c == "RTGS" and amount < IN.RTGS_MINIMUM)]
    if not ok:
        return "RTGS"
    weights = [IN.CHANNEL_MIX[c][0] * (0.15 if (illicit and c == "RTGS") else 1.0) for c in ok]
    return rng.choices(ok, weights=weights, k=1)[0]

def generate_dataset(seed: int = 7, n_normal: int = 135, n_normal_txn: int = 150) -> tuple[pd.DataFrame, set[str], dict]:
    """Build a labelled synthetic ledger.

    Returns (transactions_df, ground_truth_accounts, meta) where meta carries
    "rings" (account -> typology name) and "decoys" (account -> the family of
    lawful activity it belongs to, for the hard-negative analysis).
    """
    rng = random.Random(seed)
    txns: list[dict] = []
    truth: set[str] = set()
    ring_labels: dict[str, str] = {}
    tid = [0]

    def add(sender, receiver, amount, day, channel="NEFT", illicit=False):
        tid[0] += 1
        # laundering skews off-hours; ordinary payments cluster in business hours
        night = rng.random() < (IN.NIGHT_SHARE_ILLICIT if illicit else IN.NIGHT_SHARE_NORMAL)
        if night:
            hour = rng.choice([0, 1, 2, 3, 4, 5, 6, 22, 23])
        else:
            hour = rng.randint(*IN.BUSINESS_HOURS)
        minute = rng.randint(0, 59)
        txns.append({
            "txn_id": f"TXN{tid[0]:05d}",
            "sender": sender,
            "receiver": receiver,
            "amount": int(amount),
            "day": int(day),
            "timestamp": f"{EPOCH + timedelta(days=int(day) - 1)}T{hour:02d}:{minute:02d}:00",
            "channel": channel,
        })

    # ---- ordinary population -------------------------------------------
    # Real IFSC prefixes, synthetic numbers. `roles` keeps a machine-readable map
    # from account to its part in the story, so tests and probes never have to
    # pattern-match on identifiers.
    all_ids = _bank_accounts(rng, n_normal + 60)
    normal_pool = all_ids[:n_normal - 2]
    reserve = all_ids[n_normal - 2:]
    roles: dict[str, str] = {a: "ordinary" for a in normal_pool}

    # round-robin the sender so every ordinary account genuinely appears in the ledger
    for i in range(n_normal_txn):
        s = normal_pool[i % len(normal_pool)]
        r = rng.choice([x for x in normal_pool if x != s])
        ch = _pick_channel(rng)
        add(s, r, _amount_for(rng, ch), rng.randint(1, 30), ch)

    # ---- typology 1: structuring (3 rings x 2 accounts) ------------------
    struct_accounts = []
    for ring in range(1, 4):
        s, r = reserve.pop(), reserve.pop()
        struct_accounts.append((s, r))
        name = f"STRUCTURING-RING-{ring}"
        truth.update([s, r]); ring_labels[s] = name; ring_labels[r] = name
        roles[s] = "structuring_source"; roles[r] = "structuring_sink"
        base = rng.randint(1, 20)
        for k in range(5):
            amt = rng.randint(int(CTR_THRESHOLD * 0.90), int(CTR_THRESHOLD * 0.99))
            add(s, r, amt, base + k % 5, _channel_for_amount(rng, amt, illicit=True), illicit=True)

    # ---- typology 2: fan-out -> fan-in (3 rings x 5 accounts) ------------
    fan_meta: dict[int, dict] = {}
    for ring in range(1, 4):
        origin = reserve.pop()
        branches = [reserve.pop() for _ in range(3)]
        dest = reserve.pop()
        name = f"FAN-RING-{ring}"
        truth.update([origin, dest, *branches])
        for a in [origin, dest, *branches]:
            ring_labels[a] = name
        roles[origin] = "fan_origin"; roles[dest] = "fan_destination"
        for b in branches:
            roles[b] = "fan_branch"
        base = rng.randint(1, 15)
        for b in branches:
            amt = rng.randint(300_000, 900_000)
            add(origin, b, amt, base, _channel_for_amount(rng, amt, illicit=True), illicit=True)
        legs = []
        for b in branches:
            amt, day = rng.randint(280_000, 880_000), base + rng.randint(2, 5)
            add(b, dest, amt, day, _channel_for_amount(rng, amt, illicit=True), illicit=True)
            legs.append((amt, day))
        fan_meta[ring] = {"dest": dest, "legs": legs}

    # ---- typology 3: circular flows (lengths 3, 4, 5) --------------------
    cycle_nodes_all = []
    for length in (3, 4, 5):
        nodes = [reserve.pop() for _ in range(length)]
        cycle_nodes_all.extend(nodes)
        name = f"CYCLE-RING-{length}"
        truth.update(nodes)
        for a in nodes:
            ring_labels[a] = name
            roles[a] = "cycle_member"
        base = rng.randint(1, 20)
        for i in range(length):
            amt = rng.randint(400_000, 950_000)
            add(nodes[i], nodes[(i + 1) % length], amt, base + i,
                _channel_for_amount(rng, amt, illicit=True), illicit=True)

    # ---- typology 4: pass-through mule chain -----------------------------
    src, sink = rng.sample(normal_pool, 2)
    m1, m2 = reserve.pop(), reserve.pop()
    truth.update([m1, m2])
    ring_labels[m1] = ring_labels[m2] = "MULE-CHAIN-1"
    roles[m1] = roles[m2] = "mule"
    add(src, m1, 500_000, 18, _channel_for_amount(rng, 500_000, illicit=True), illicit=True)
    add(m1, m2, 490_000, 19, _channel_for_amount(rng, 490_000, illicit=True), illicit=True)
    add(m2, sink, 480_000, 20, _channel_for_amount(rng, 480_000, illicit=True), illicit=True)

    # ---- the bridge: fan ring 1 cashes out THROUGH the mule chain ---------
    # A single edge that makes two apparently separate typologies one
    # operation. Nothing tells the detector this; it has to rediscover it.
    if 1 in fan_meta:
        biggest_amt, biggest_day = max(fan_meta[1]["legs"], key=lambda t: t[0])
        amt = int(biggest_amt * 0.96)
        add(fan_meta[1]["dest"], m1, amt, biggest_day + 1,
            _channel_for_amount(rng, amt, illicit=True), illicit=True)

    # ======================================================================
    # HARD NEGATIVES - legitimate activity shaped like financial crime.
    # ======================================================================
    # Any detector can score perfectly on data where only criminals form
    # triangles. The real test is whether it can tell a laundering ring from
    # an ordinary business that happens to have the same topology. Everything
    # below is lawful and is deliberately NOT in the ground-truth set: each
    # family is engineered to trip one specific detector.
    decoys: dict[str, str] = {}

    def mark(acc, family):
        decoys[acc] = family
        roles[acc] = "decoy:" + family

    # (a) supply chain -> looks exactly like fan-out / fan-in.
    #     A customer pays three contractors who all buy from one distributor.
    #     Discriminator: the contractors have their own unrelated trade, so the
    #     fan accounts for only part of their throughput (low dedication).
    for k in (1, 2):
        cust, dist = reserve.pop(), reserve.pop()
        cons = [reserve.pop() for _ in (1, 2, 3)]
        for a in [cust, dist, *cons]:
            mark(a, "Supply chain (fan shape)")
        base = rng.randint(3, 18)
        for c in cons:
            add(cust, c, rng.randint(400_000, 800_000), base, "NEFT")
            add(c, dist, rng.randint(350_000, 700_000), base + rng.randint(1, 4), "NEFT")
            # genuine independent trade - this is what a real business looks like
            for _ in range(3):
                add(rng.choice(normal_pool), c, rng.randint(200_000, 700_000), rng.randint(1, 30), "NEFT")
                add(c, rng.choice(normal_pool), rng.randint(150_000, 600_000), rng.randint(1, 30), "NEFT")

    # (b) card refund loop -> looks exactly like a circular flow.
    #     Merchant, payment processor and acquiring bank settle back and forth
    #     daily. Discriminator: these are standing relationships with many
    #     transactions per edge; a laundering loop is one-shot.
    for k in (1, 2):
        m, psp, acq = reserve.pop(), reserve.pop(), reserve.pop()
        for a in (m, psp, acq):
            mark(a, "Card settlement (cycle shape)")
        for rep in range(4):
            d = 4 + rep * 6
            add(m, psp, rng.randint(300_000, 600_000), d, "NEFT")
            add(psp, acq, rng.randint(290_000, 590_000), d + 1, "NEFT")
            add(acq, m, rng.randint(280_000, 580_000), d + 2, "NEFT")

    # (c) escrow agent -> looks exactly like a pass-through conduit.
    #     Receives and forwards near-identical sums within a day, every time.
    #     Discriminator: it does this openly with many counterparties, whereas
    #     a mule has a tiny, fixed counterparty set.
    esc = reserve.pop()
    mark(esc, "Escrow agent (pass-through shape)")
    for j in range(7):
        amt, d = rng.randint(300_000, 900_000), rng.randint(2, 26)
        add(rng.choice(normal_pool), esc, amt, d, "RTGS")
        add(esc, rng.choice(normal_pool), int(amt * 0.985), d + 1, "RTGS")

    # (d) payroll bureau -> looks exactly like structuring.
    #     Repeated transfers just under the CTR threshold, days apart.
    #     Discriminator: it is not AVOIDING the threshold - it also sends sums
    #     well above it, which a structurer never does.
    emp, bureau = reserve.pop(), reserve.pop()
    mark(emp, "Payroll bureau (structuring shape)")
    mark(bureau, "Payroll bureau (structuring shape)")
    pbase = rng.randint(2, 15)
    for k in range(5):
        add(emp, bureau, rng.randint(900_000, 985_000), pbase + k, "RTGS")
    add(emp, bureau, rng.randint(1_400_000, 2_200_000), pbase + 7, "RTGS")
    add(emp, bureau, rng.randint(1_100_000, 1_800_000), pbase + 14, "RTGS")

    # ---- interface with the legitimate economy --------------------------
    # Real rings are not islands: dirty money is funded from, and cashed out
    # into, ordinary accounts. These edges are what makes suspicion
    # propagation (L7) a genuine lead-generation tool rather than a toy - the
    # ordinary accounts touched here are NOT labelled fraud, so the pipeline
    # has to decide for itself whether to escalate them.
    funders = rng.sample(normal_pool, 3)
    for (sa, _sb), funder in zip(struct_accounts, funders):
        add(funder, sa, rng.randint(2_000_000, 3_500_000),
            max(1, rng.randint(1, 6)), "RTGS")          # placement: bulk in, then structured out
    for ring in (2, 3):
        for beneficiary in rng.sample(normal_pool, 2):  # integration: cash-out legs
            amt = rng.randint(150_000, 600_000)
            add(fan_meta[ring]["dest"], beneficiary, amt, rng.randint(16, 29),
                _channel_for_amount(rng, amt, illicit=True), illicit=True)
    for node in (cycle_nodes_all[0], cycle_nodes_all[7]):   # cycles touch the wider economy
        amt = rng.randint(100_000, 400_000)
        add(node, rng.choice(normal_pool), amt, rng.randint(22, 30),
            _channel_for_amount(rng, amt, illicit=True), illicit=True)

    df = pd.DataFrame(txns).sort_values("day", kind="stable").reset_index(drop=True)
    return df, truth, {"rings": ring_labels, "decoys": decoys, "roles": roles,
                       "calibration": IN.provenance()}


MAX_INGEST_ROWS = 200_000        # a ledger this size already takes minutes to score
MAX_AMOUNT = 10 ** 15            # Rs 1 quadrillion; anything beyond is a parse artefact
MAX_DAY_SPAN = 3_650             # 10 years of daily buckets is the most we will allocate


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fill in the optional columns so the engine can be handed any frame that
    has the three that actually matter: sender, receiver, amount."""
    df = df.copy()
    if "day" not in df.columns:
        df["day"] = 1
    if "txn_id" not in df.columns:
        df["txn_id"] = [f"TXN{i + 1:05d}" for i in range(len(df))]
    if "channel" not in df.columns:
        df["channel"] = "UNKNOWN"
    if "timestamp" not in df.columns:
        df["timestamp"] = [f"{EPOCH + timedelta(days=int(d) - 1)}T12:00:00" for d in df["day"]]
    return df


def load_dataset_from_records(records: list[dict]) -> tuple[pd.DataFrame, set[str], dict]:
    """Ingest an analyst-supplied ledger (CSV upload). Ground truth optional.

    Everything here is untrusted. The columns downstream code indexes on
    (``day`` as a 1-based bucket, ``timestamp`` as a sliceable string, a
    non-negative ``amount`` that ratio arithmetic is defined over) are
    established here rather than defended for at every use site.
    """
    df = pd.DataFrame(records)
    required = {"sender", "receiver", "amount"}
    missing = required - set(df.columns)
    if missing:
        raise LedgerError(f"missing required column(s): {', '.join(sorted(missing))}")
    if len(df) > MAX_INGEST_ROWS:
        raise LedgerError(f"ledger has {len(df):,} rows; the limit is {MAX_INGEST_ROWS:,}")
    df["sender"] = df["sender"].astype(str).str.strip()
    df["receiver"] = df["receiver"].astype(str).str.strip()

    # Amounts: a debit is expressed by direction, never by sign, so a negative
    # amount is a schema error. Left in, it inverts every ratio the detectors
    # compute (a -Rs 5L "credit" forwarded as -Rs 4.9L scores as a 98% conduit).
    amt = pd.to_numeric(df["amount"], errors="coerce")
    amt = amt.replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0, upper=MAX_AMOUNT)
    df["amount"] = amt.round().astype("int64")

    if "day" in df.columns:
        day = pd.to_numeric(df["day"], errors="coerce").fillna(1)
    elif "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce")
        origin = ts.min()
        day = ((ts - origin).dt.days + 1) if pd.notna(origin) else pd.Series(1, index=df.index)
        day = day.fillna(1)
    else:
        day = pd.Series(1, index=df.index)
    # Day is a 1-based index into per-day buckets downstream, so it has to be
    # normalised here: a zero or negative day is a KeyError in the timeline, and
    # a decade-wide span is a per-day replay that never finishes.
    day = day.replace([np.inf, -np.inf], np.nan).fillna(1).round().astype("int64")
    day = day - min(int(day.min()), 1) + 1 if len(day) else day
    if len(day) and int(day.max()) > MAX_DAY_SPAN:
        raise LedgerError(f"ledger spans {int(day.max()):,} days; the limit is {MAX_DAY_SPAN:,}")
    df["day"] = day.astype(int)

    if "timestamp" not in df.columns:
        df["timestamp"] = [str(EPOCH + timedelta(days=int(d) - 1)) + "T12:00:00" for d in df["day"]]
    else:
        # Report rendering slices this as a string; a numeric or NaT timestamp
        # would raise there rather than here.
        df["timestamp"] = df["timestamp"].astype(str)
    if "txn_id" not in df.columns:
        df["txn_id"] = [f"TXN{i + 1:05d}" for i in range(len(df))]
    else:
        df["txn_id"] = df["txn_id"].astype(str)
    if "channel" not in df.columns:
        df["channel"] = "UNKNOWN"
    else:
        df["channel"] = df["channel"].astype(str)
    truth: set[str] = set()
    if "is_fraud" in df.columns:
        mask = df["is_fraud"].astype(str).str.lower().isin({"1", "true", "yes", "y"})
        truth = set(df.loc[mask, "sender"]) | set(df.loc[mask, "receiver"])
    df = df[(df["sender"] != df["receiver"]) & (df["sender"] != "") & (df["receiver"] != "")]
    df = df.reset_index(drop=True)
    if df.empty:
        raise LedgerError("no usable transactions after validation")
    # Evidence ids are matched by identity downstream, so they must be unique.
    if df["txn_id"].duplicated().any():
        df["txn_id"] = [f"TXN{i + 1:05d}" for i in range(len(df))]
    return df, truth, {"rings": {}, "decoys": {}}


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------

def build_graph(df: pd.DataFrame) -> nx.DiGraph:
    G = nx.DiGraph()
    # Sorted, not just de-duplicated. Python randomises string hashing per
    # process, so inserting nodes straight from a set gives the graph a
    # different node order on every run. Node order reaches the output: it
    # drives traversal order in every networkx call below it, and from there
    # the numbering of the rings and the order of tied rows in the watchlist.
    # A case file that renumbers its own rings between two runs of the same
    # ledger cannot be cited as evidence, so the order is fixed here at the source.
    G.add_nodes_from(sorted(set(df["sender"]) | set(df["receiver"])))
    for row in df.itertuples(index=False):
        if G.has_edge(row.sender, row.receiver):
            e = G[row.sender][row.receiver]
            e["amount"] += int(row.amount)
            e["count"] += 1
            e["first_day"] = min(e["first_day"], int(row.day))
            e["last_day"] = max(e["last_day"], int(row.day))
        else:
            G.add_edge(row.sender, row.receiver, amount=int(row.amount), count=1,
                       first_day=int(row.day), last_day=int(row.day))
    return G


# --------------------------------------------------------------------------
# L1 - Structuring / smurfing
# --------------------------------------------------------------------------

def detect_structuring(df: pd.DataFrame, cfg: Config) -> tuple[dict, dict]:
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    lo, hi = CTR_THRESHOLD * cfg.structuring_floor_pct, CTR_THRESHOLD
    near = df[(df["amount"] >= lo) & (df["amount"] < hi)]

    def bump(acc, w):
        flags[acc] = flags.get(acc, 0) + w

    # (a) repeated near-threshold transfers on one directed pair
    above = df[df["amount"] >= hi]
    # Threshold AVOIDANCE is the discriminator: a payer who also sends over the
    # CTR line is not hiding from it, just a big biller. But "has ever crossed"
    # is a one-transaction exemption - a structurer plants a single over-CTR
    # transfer and the pair is immune for the rest of the window. So the
    # crossings have to be a real part of the relationship, not a token. Buying
    # the exemption then costs the launderer several genuinely CTR-reported
    # transfers, which is what the reporting rule is there to force.
    cross_counts: dict[tuple, int] = defaultdict(int)
    for row in above[["sender", "receiver"]].itertuples(index=False):
        cross_counts[(row.sender, row.receiver)] += 1

    def is_open_biller(s, r, n_near) -> bool:
        n_cross = cross_counts.get((s, r), 0)
        if n_cross < cfg.structuring_min_crossers:
            return False
        return (n_cross / (n_cross + n_near)) >= cfg.structuring_crosser_share

    flagged_pairs: set[tuple] = set()
    for (s, r), grp in near.groupby(["sender", "receiver"], sort=False):
        if len(grp) < cfg.structuring_min_count:
            continue
        span = int(grp["day"].max() - grp["day"].min())
        if span > cfg.structuring_window_days:
            continue
        if cfg.structuring_require_avoidance and is_open_biller(s, r, len(grp)):
            continue
        flagged_pairs.add((s, r))
        total = int(grp["amount"].sum())
        detail = (f"{len(grp)} transfers {s} -> {r} totalling Rs {total:,}, every one sized "
                  f"between {cfg.structuring_floor_pct:.0%} and 100% of the Rs {CTR_THRESHOLD:,} "
                  f"CTR threshold, all inside {span} day(s). Amount selection is the tell: "
                  f"a legitimate payer has no reason to keep landing just under the reporting line.")
        for acc in (s, r):
            bump(acc, LAYER_META["STRUCTURING"]["weight"])
            reasons[acc].append({"layer": "STRUCTURING", "detail": detail,
                                 "evidence": grp["txn_id"].tolist()})

    # (b) classic smurfing: many senders funnelling near-threshold into one receiver
    for r, grp in near.groupby("receiver", sort=False):
        senders = grp["sender"].nunique()
        if len(grp) < cfg.structuring_min_count or senders < cfg.smurf_min_senders:
            continue
        span = int(grp["day"].max() - grp["day"].min())
        if span > cfg.structuring_window_days:
            continue
        detail = (f"{len(grp)} near-threshold credits from {senders} distinct senders converged on "
                  f"{r} within {span} day(s) - smurfing pattern (deposit splitting across mules).")
        bump(r, LAYER_META["STRUCTURING"]["weight"])
        reasons[r].append({"layer": "STRUCTURING", "detail": detail,
                           "evidence": grp["txn_id"].tolist()})
        for s in grp["sender"].unique():
            bump(s, LAYER_META["STRUCTURING"]["weight"])
            reasons[s].append({"layer": "STRUCTURING",
                               "detail": f"Acted as a depositing mule into the smurfing funnel at {r}.",
                               "evidence": grp[grp["sender"] == s]["txn_id"].tolist()})

    # (c) aggregation below the near-threshold band.
    #     (a) and (b) only ever look at transfers sized 85-100% of the CTR line,
    #     which is the narrowest possible reading of structuring: splitting the
    #     same Rs 30,00,000 into ten transfers of Rs 3,00,000 sits entirely under
    #     that band and trips nothing. Rule 3 of the PML (Maintenance of Records)
    #     Rules is written on the aggregate - integrally connected transactions
    #     that together exceed the threshold - so the test is the total, not the
    #     size of any one leg. Every leg must stay below the line: a pair that
    #     crosses it openly is reported anyway and is not avoiding anything.
    sub = df[df["amount"] < hi]
    for (s, r), grp in sub.groupby(["sender", "receiver"], sort=False):
        if (s, r) in flagged_pairs or len(grp) < cfg.agg_min_count:
            continue
        total = int(grp["amount"].sum())
        if total < CTR_THRESHOLD:
            continue
        span = int(grp["day"].max() - grp["day"].min())
        if span > cfg.agg_window_days:
            continue
        if cfg.structuring_require_avoidance and is_open_biller(s, r, len(grp)):
            continue
        detail = (f"{len(grp)} transfers {s} -> {r} totalling Rs {total:,} inside {span} day(s), "
                  f"every leg individually below the Rs {CTR_THRESHOLD:,} CTR threshold "
                  f"(largest Rs {int(grp['amount'].max()):,}) and the pair never crosses it. "
                  f"Integrally connected transfers are aggregated for reporting purposes, so "
                  f"splitting a reportable sum into sub-threshold legs is the avoidance itself - "
                  f"the unremarkable size of each individual leg is what the structure buys.")
        for acc in (s, r):
            bump(acc, LAYER_META["STRUCTURING"]["weight"])
            reasons[acc].append({"layer": "STRUCTURING", "detail": detail,
                                 "evidence": grp["txn_id"].tolist()})
    return flags, dict(reasons)


# --------------------------------------------------------------------------
# L2 - Fan-out / fan-in
# --------------------------------------------------------------------------

def detect_fan(G: nx.DiGraph, cfg: Config) -> tuple[dict, dict]:
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    w = LAYER_META["FAN"]["weight"]

    def bump(acc):
        flags[acc] = flags.get(acc, 0) + w

    for origin in G.nodes():
        branches = list(G.successors(origin))
        if len(branches) < cfg.fan_min_branches:
            continue
        downstream: dict[str, set] = defaultdict(set)
        for b in branches:
            for tgt in G.successors(b):
                if tgt != origin:
                    downstream[tgt].add(b)
        for dest, contributors in downstream.items():
            if len(contributors) < cfg.fan_min_branches:
                continue
            out_amt = sum(G[origin][b]["amount"] for b in contributors)
            in_amt = sum(G[b][dest]["amount"] for b in contributors)
            if out_amt <= 0:
                continue
            retention = 1 - (in_amt / out_amt)
            # The two legs of a split-and-rejoin have to be commensurate: the
            # branches are carrying this flow, not banking it and not funding
            # the destination out of unrelated money. Branches do top up from a
            # second source before forwarding, so the band is two-sided rather
            # than requiring a non-negative commission - what it excludes is
            # branches that keep most of the money, and branches whose onward
            # payment dwarfs what this fan gave them.
            forwarded = in_amt / out_amt
            if not ((1 - cfg.fan_max_retention) <= forwarded <= cfg.fan_max_inflation):
                continue
            # A laundering branch exists only to carry this flow. A real contractor
            # paid by this customer also has other customers and other suppliers, so
            # the fan accounts for only part of its throughput.
            deds = []
            for b in contributors:
                thru = (sum(d["amount"] for _, _, d in G.in_edges(b, data=True)) +
                        sum(d["amount"] for _, _, d in G.out_edges(b, data=True)))
                served = G[origin][b]["amount"] + G[b][dest]["amount"]
                deds.append(served / thru if thru else 0)
            # Median, not mean: dedication is a claim about the branches as a
            # set, and averaging let one wholly dedicated shell account carry
            # several diversified real businesses over the threshold.
            dedication = float(np.median(deds)) if deds else 0.0
            if dedication < cfg.fan_min_dedication:
                continue
            bump(origin); bump(dest)
            reasons[origin].append({
                "layer": "FAN",
                "detail": (f"Split Rs {out_amt:,} across {len(contributors)} intermediaries "
                           f"({', '.join(sorted(contributors))}) which then reconverged on {dest} "
                           f"as Rs {in_amt:,} ({retention:.1%} retained as commission). "
                           f"The intermediaries devote {dedication:.0%} of all their activity to this "
                           f"one flow, so they are conduits rather than businesses with their own trade. "
                           f"Splitting then rejoining serves no commercial purpose - it exists to "
                           f"break the audit trail between origin and destination."),
                "evidence": [f"{origin}->{b}" for b in sorted(contributors)]})
            reasons[dest].append({
                "layer": "FAN",
                "detail": (f"Consolidation point: received Rs {in_amt:,} from {len(contributors)} "
                           f"accounts that were all funded by the single upstream source {origin} "
                           f"one to five days earlier."),
                "evidence": [f"{b}->{dest}" for b in sorted(contributors)]})
            for b in contributors:
                bump(b)
                reasons[b].append({
                    "layer": "FAN",
                    "detail": (f"Intermediary leg of the {origin} -> ... -> {dest} split-and-rejoin "
                               f"structure; received Rs {G[origin][b]['amount']:,} and forwarded "
                               f"Rs {G[b][dest]['amount']:,}."),
                    "evidence": [f"{origin}->{b}", f"{b}->{dest}"]})
    return flags, dict(reasons)


# --------------------------------------------------------------------------
# L3 - Circular flows
# --------------------------------------------------------------------------

# Enumeration is exponential in the worst case, so this is a work budget rather
# than a cap on results. The size is set by what a laundering graph actually
# looks like: the bundled ledger offers 15 candidate loops of length six or
# less, and a ledger offering thousands is one where nearly everything sits in
# some loop, which is exactly the case where the layer has nothing to say. The
# old budget was an order of magnitude past any of that and only ever bit on
# dense books of ordinary traffic, where it cost most of a minute to conclude
# nothing.
MAX_CYCLES = 20_000


def _simple_cycles(G: nx.DiGraph, bound: int, limit: int = MAX_CYCLES, report: dict | None = None):
    """networkx >= 3.1 supports length_bound; fall back gracefully if not.

    Cycle enumeration is exponential in the worst case and the graph comes from
    an uploaded ledger, so a dense book of ordinary interbank traffic can pin
    the process indefinitely. Enumeration stops after ``limit`` candidates;
    coherent laundering loops are short and rare, so the budget is only ever
    reached on graphs where the layer was not going to say anything useful.
    """
    try:
        it = nx.simple_cycles(G, length_bound=bound)
    except TypeError:                                    # older networkx
        it = (c for c in nx.simple_cycles(G) if len(c) <= bound)
    n = 0
    for cyc in it:
        n += 1
        if n > limit:
            # Stopping is the right call on a graph like this, but stopping
            # quietly is not: the cycle layer's recall becomes a lower bound and
            # the analyst has to be told so rather than reading a partial count
            # as a complete one. The budget is a candidate count and nothing
            # else, so the same ledger is always truncated at the same place;
            # a wall-clock budget would make the alert set depend on how busy
            # the machine was.
            if report is not None:
                report["truncated"] = True
                report["examined"] = limit
                report["reason"] = f"candidate limit of {limit:,} loops reached"
            return
        yield cyc
    if report is not None:
        report["truncated"] = False
        report["examined"] = n


def _cycle_is_coherent(G, cycle, cfg) -> tuple[bool, str]:
    """A laundering loop round-trips value quickly and in order.

    A cycle that merely exists in a month of ordinary payments is a coincidence,
    not a typology. We therefore require the loop to close inside
    (length + slack) days and, optionally, that some rotation of it is
    chronologically ordered - i.e. the money actually travelled the loop.
    """
    n = len(cycle)
    days = [G[cycle[i]][cycle[(i + 1) % n]]["first_day"] for i in range(n)]
    span = max(days) - min(days)
    if span > n + cfg.cycle_max_span_slack:
        return False, ""
    # A standing trading relationship is bilateral on every leg - a merchant,
    # its processor and its acquirer all settle with each other repeatedly. So
    # the exemption requires EVERY leg to be repeated. Testing max() instead
    # meant padding one edge with a couple of extra transfers exempted the whole
    # loop, which is a one-edge escape hatch from the layer.
    counts = [G[cycle[i]][cycle[(i + 1) % n]]["count"] for i in range(n)]
    if min(counts) > cfg.cycle_max_edge_count:
        return False, ""      # money going round a standing trading relationship is trade
    if cfg.cycle_require_monotone:
        ok = False
        for r in range(n):
            rot = days[r:] + days[:r]
            if all(rot[i] <= rot[i + 1] for i in range(n - 1)):
                ok = True
                break
        if not ok:
            return False, ""
    return True, f"closed in {span} day(s)"


def _canonical(cycle: list) -> list:
    """Rotate a directed cycle to start at its smallest member.

    Enumeration returns one arbitrary rotation of each loop, and which one it
    is depends on traversal order. Rotating to a fixed starting point means the
    same loop is described the same way every run, so the narrative attached to
    an alert does not move around under the analyst.
    """
    i = min(range(len(cycle)), key=lambda k: cycle[k])
    return cycle[i:] + cycle[:i]


def detect_cycles(G: nx.DiGraph, cfg: Config, report: dict | None = None) -> tuple[dict, dict, dict]:
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    w = LAYER_META["CYCLE"]["weight"]

    # One finding per set of accounts, and enumeration may offer several loops
    # over the same set. Collecting the coherent ones first and then taking the
    # lowest canonical rotation picks the same representative every time,
    # rather than whichever the traversal happened to reach first.
    report = {} if report is None else report
    accepted: dict[frozenset, list] = {}
    for cycle in _simple_cycles(G, cfg.cycle_max_len, report=report):
        if len(cycle) < 3:
            continue
        coherent, _ = _cycle_is_coherent(G, cycle, cfg)
        if not coherent:
            continue
        rot = _canonical(cycle)
        key = frozenset(rot)
        if key not in accepted or rot < accepted[key]:
            accepted[key] = rot

    for cycle in sorted(accepted.values()):
        coherent, timing = _cycle_is_coherent(G, cycle, cfg)
        amounts = [G[cycle[i]][cycle[(i + 1) % len(cycle)]]["amount"] for i in range(len(cycle))]
        value = sum(amounts)
        conservation = min(amounts) / max(amounts) if max(amounts) else 0
        # The narrative asserts the money round-tripped. If one leg carries a
        # fraction of another, value did not survive the loop and the cycle is
        # a coincidence of ordinary payments rather than a round trip.
        if conservation < cfg.cycle_min_conservation:
            continue
        path = " -> ".join(cycle + [cycle[0]])
        for acc in cycle:
            flags[acc] = flags.get(acc, 0) + w
            reasons[acc].append({
                "layer": "CYCLE",
                "detail": (f"Member of a closed {len(cycle)}-hop loop carrying Rs {value:,}, {timing}: "
                           f"{path}. Hops are chronologically ordered and {conservation:.0%} of value "
                           f"is conserved around the loop, so the money genuinely round-tripped. Funds "
                           f"return to their origin, so no net economic value is exchanged - the only "
                           f"product of the loop is transaction history."),
                "evidence": [f"{cycle[i]}->{cycle[(i + 1) % len(cycle)]}" for i in range(len(cycle))]})
    report["reported"] = len(accepted)
    return flags, dict(reasons), report


# --------------------------------------------------------------------------

def detect_pass_through(df: pd.DataFrame, G: nx.DiGraph, cfg: Config) -> tuple[dict, dict]:
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    w = LAYER_META["PASS-THROUGH"]["weight"]
    ins = defaultdict(list)
    outs = defaultdict(list)
    for row in df.itertuples(index=False):
        ins[row.receiver].append(row)
        outs[row.sender].append(row)

    for acc in G.nodes():
        i_list, o_list = ins.get(acc, []), outs.get(acc, [])
        if not i_list or not o_list:
            continue
        counterparties = len({r.sender for r in i_list} | {r.receiver for r in o_list})
        if counterparties > cfg.pt_max_counterparties:
            continue
        # Only debits inside the forwarding window can pair with a credit, so the
        # debits are bucketed by day once and the search visits that window
        # rather than the account's whole debit history. Candidates are ordered
        # by their position in the ledger, so the leg chosen for a credit is the
        # same one a full scan would have found first.
        by_day: dict = defaultdict(list)
        for pos, o_tx in enumerate(o_list):
            by_day[int(o_tx.day)].append((pos, o_tx))
        offsets = range(cfg.pt_window_days + 1)

        matches = []
        for i_tx in i_list:
            if i_tx.amount < cfg.pt_min_amount:
                continue
            day = int(i_tx.day)
            for _, o_tx in sorted(c for k in offsets for c in by_day.get(day + k, ())):
                ratio = o_tx.amount / i_tx.amount if i_tx.amount else 0
                if not (cfg.pt_ratio_low <= ratio <= cfg.pt_ratio_high):
                    continue
                matches.append((i_tx, o_tx, ratio, int(o_tx.day) - day))
                break   # one confirmed hop is enough for this incoming leg
        if not matches:
            continue
        # A conduit that repeats the same receive-and-forward on a schedule, over
        # weeks, with a fixed counterparty set, is a settlement rail - an escrow
        # agent, an acquirer, a clearing member. A mule does this once or twice
        # and goes quiet, because the account is burned as soon as it is used.
        # Recurrence has to be measured structurally, not on the amounts that
        # happened to match: count every receive-then-forward this account makes
        # between the SAME pair of counterparties. A rail repeats that pair on a
        # schedule; a mule chain touches each pair once.
        # The test on a pair is how many receive-then-forward hops it makes and
        # over what span, which is a count and a range, not the hops themselves.
        # Sorted day vectors answer both without materialising the cross product
        # of the account's credits and debits.
        credit_days: dict = defaultdict(list)
        debit_days: dict = defaultdict(list)
        for i_tx in i_list:
            credit_days[i_tx.sender].append(int(i_tx.day))
        for o_tx in o_list:
            debit_days[o_tx.receiver].append(int(o_tx.day))
        rail = {}
        for src, cd in credit_days.items():
            credits = np.sort(np.array(cd, dtype=np.int64))
            for dst, dd in debit_days.items():
                debits = np.sort(np.array(dd, dtype=np.int64))
                hops = (np.searchsorted(debits, credits + cfg.pt_window_days, side="right")
                        - np.searchsorted(debits, credits, side="left"))
                paired = credits[hops > 0]
                if paired.size:
                    rail[(src, dst)] = (int(hops.sum()), int(paired.min()), int(paired.max()))
        # The exemption belongs to the counterparty PAIR that behaves like a
        # settlement rail, not to the account. Dropping the whole account meant
        # one scheduled, innocuous-looking pair immunised every other hop it
        # made - a mule need only run a recurring side rail to switch the layer
        # off for itself entirely.
        rail_pairs = {pair for pair, (hops, first, last) in rail.items()
                      if hops > cfg.pt_max_repeats and (last - first) >= cfg.pt_rail_span_days}
        matches = [m for m in matches if (m[0].sender, m[1].receiver) not in rail_pairs]
        if not matches:
            continue
        # One hop is the finding; the layer contributes its weight once, as every
        # other layer does. Scoring per matched hop let a single account reach an
        # arbitrary risk score, so a busy conduit outranked an account confirmed
        # by four independent layers and the tiers stopped meaning anything.
        flags[acc] = flags.get(acc, 0) + w
        for i_tx, o_tx, ratio, hold in matches:
            reasons[acc].append({
                "layer": "PASS-THROUGH",
                "detail": (f"Received Rs {i_tx.amount:,} from {i_tx.sender} on day {i_tx.day} and "
                           f"forwarded Rs {o_tx.amount:,} to {o_tx.receiver} on day {o_tx.day} - "
                           f"{ratio:.1%} passed on, {1 - ratio:.1%} skimmed, held {hold} day(s). "
                           f"The account stores no value; it is a conduit, which is the defining "
                           f"signature of a money mule. It is not a settlement rail: the behaviour "
                           f"occurs {len(matches)} time(s), not on a recurring schedule."),
                "evidence": [i_tx.txn_id, o_tx.txn_id]})
    return flags, dict(reasons)


# --------------------------------------------------------------------------
# L9 - Evasion-adaptive layer (primary)
# --------------------------------------------------------------------------
# Layers 1 to 4 are published rules, and every one of them has a number in it.
# A launderer who can read the rule can sit just outside the number: space the
# hops of a loop wider than the span cap, size the deposits below the band,
# hold the money a day longer than the conduit window, and give every mule a
# little unrelated trade so its dedication ratio never rises. Our own red-team
# script builds exactly that ledger and the first eight layers score 0 on it.
#
# This layer does not add another number to sit outside of. It scores three
# properties that are consequences of moving other people's money through
# other people's accounts, and which therefore survive the manoeuvre:
#
#   D1  rhythm         hops arriving on a schedule rather than on trade
#   D2  conservation   one sum surviving several hops, distinct in size from
#                      the traffic the accounts otherwise carry
#   D3  net exposure   the chain still dominates each account after reciprocal
#                      round-trips (that is, cover traffic) are cancelled out
#
# None of the three is sufficient alone: a standing settlement rail is rhythmic,
# an escrow agent conserves value, and a small business is dedicated to its one
# customer. Two of the three must agree, and every edge on the chain must be
# one-shot, before anything is flagged.

CHAIN_MAX_HOPS = 8            # longest parcel we will follow
CHAIN_BUDGET = 60_000         # expansion ceiling, so a dense upload cannot stall


def _net_throughput(df: pd.DataFrame) -> dict:
    """Throughput per account after cancelling reciprocal flow per counterparty.

    Cover traffic is cheap: a mule can be handed any number of round-trips with
    friendly accounts, and every one of them inflates the gross volume that the
    fan layer's dedication ratio divides by. What it cannot do is change the
    account's net position, because the money comes straight back. Netting each
    counterparty pair before measuring dedication removes the disguise without
    removing anything a real trading account depends on: a business that buys
    from a supplier and sells to a customer nets to its full turnover.
    """
    if not len(df):
        return {}
    pair = df.groupby(["sender", "receiver"])["amount"].sum()
    directed: dict[tuple, float] = {}
    for (s, r), amt in pair.items():
        directed[(s, r)] = float(amt)
    net: dict[str, float] = defaultdict(float)
    seen: set[frozenset] = set()
    for (s, r), amt in directed.items():
        key = frozenset((s, r))
        if key in seen:
            continue
        seen.add(key)
        back = directed.get((r, s), 0.0)
        residual = abs(amt - back)
        net[s] += residual
        net[r] += residual
    return dict(net)


def _chain_signature(chain: list[dict]) -> tuple:
    return tuple(h.txn_id for h in chain)


def _extend_chains(df: pd.DataFrame, edge_count: dict, cfg: Config) -> list[list[dict]]:
    """Follow every sum that keeps its size from one account to the next.

    Deliberately blind to elapsed time. The span caps elsewhere in the engine
    exist to stop a standing trade relationship being read as a loop, and this
    walk uses the one-shot edge test for that instead, which a launderer cannot
    satisfy and still keep re-using the same corridor.
    """
    out_rows: dict[str, list] = defaultdict(list)
    for row in df.itertuples(index=False):
        if edge_count.get((row.sender, row.receiver), 0) > cfg.adaptive_max_edge_count:
            continue                       # a standing corridor, not a one-shot hop
        out_rows[row.sender].append(row)
    for k in out_rows:
        out_rows[k].sort(key=lambda r: r.day)

    floor = cfg.adaptive_min_amount
    chains: list[list[dict]] = []
    budget = [CHAIN_BUDGET]

    def walk(path: list, visited: set):
        if budget[0] <= 0 or len(path) >= CHAIN_MAX_HOPS:
            return
        last = path[-1]
        grew = False
        for nxt in out_rows.get(last.receiver, ()):
            budget[0] -= 1
            if budget[0] <= 0:
                return
            if nxt.day < last.day:
                continue
            ratio = nxt.amount / last.amount if last.amount else 0
            if not (cfg.adaptive_hop_low <= ratio <= cfg.adaptive_hop_high):
                continue
            closes = nxt.receiver == path[0].sender
            if closes:
                # The parcel is back where it started. Record the loop and stop:
                # anything the origin does afterwards is separate activity, not
                # a continuation of this relay.
                chains.append(path + [nxt])
                grew = True
                continue
            if nxt.receiver in visited:
                continue          # a chain that revisits an account is not one parcel
            grew = True
            walk(path + [nxt], visited | {nxt.receiver})
        if not grew and len(path) >= 3:
            chains.append(list(path))

    for start_rows in list(out_rows.values()):
        for row in start_rows:
            if row.amount < floor or budget[0] <= 0:
                continue
            walk([row], {row.sender, row.receiver})
    return chains


# The lines a payer might be sizing against. Reporting thresholds are always
# round numbers because regulators write them for humans, so the candidate set
# is small and jurisdiction-independent: India's CTR sits at 10,00,000, the US
# CTR at $10,000, the EU cash-payment ceiling at EUR 10,000. Inferring which
# line a pair is hugging, rather than compiling ours in, means the layer keeps
# working when the number changes or the ledger comes from somewhere else.
REPORTING_LINES = (100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000)


def _inferred_line(amounts: list[float]) -> float | None:
    """The round reporting line a run of tranches appears to be sized against.

    Taken from the middle of the run rather than its top. A payroll bureau pays
    mostly just under Rs 10,00,000 and occasionally well over it; reading the
    line off the largest payment would put the line above everything it does and
    make it look like a structurer with room to spare. Read off the median, the
    line lands where the tranches actually sit and the payments that cross it
    disqualify the pair, which is the behaviour we want.
    """
    ordered = sorted(amounts)
    mid = ordered[len(ordered) // 2]
    for line in REPORTING_LINES:
        if mid < line:
            return float(line)
    return None


def _detect_slow_aggregation(df: pd.DataFrame, cfg: Config) -> tuple[dict, dict]:
    """Structuring stretched out until it falls off the end of the time window.

    L1 asks whether a run of sub-threshold transfers lands inside a few days.
    Spreading the same deposits over a fortnight defeats it, and our own
    circularity probe shows the recall cost. Nothing about the deposits changes,
    though: they are still uniform tranches, still parked just under a round
    line, and still summing to several times that line. Dropping the window and
    keeping the amount evidence is what catches the patient version.
    """
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    if not len(df):
        return flags, dict(reasons)

    for (sender, receiver), grp in df.groupby(["sender", "receiver"], sort=False):
        amounts = [float(a) for a in grp["amount"] if a > 0]
        if len(amounts) < cfg.adaptive_agg_min_count:
            continue
        line = _inferred_line(amounts)
        if line is None:
            continue
        if max(amounts) >= line:
            continue                       # this payer is not avoiding the line
        if (sum(amounts) / len(amounts)) / line < cfg.adaptive_agg_hug:
            continue                       # not sized against this line at all
        total = sum(amounts)
        if total < line * cfg.adaptive_agg_multiple:
            continue
        mean = total / len(amounts)
        cv = math.sqrt(sum((a - mean) ** 2 for a in amounts) / len(amounts)) / mean if mean else 1.0
        if cv > cfg.adaptive_agg_amount_cv:
            continue
        span = int(grp["day"].max() - grp["day"].min())
        detail = (
            f"{len(amounts)} transfers {sender} -> {receiver} totalling Rs {int(total):,}, "
            f"every one under Rs {int(line):,} and averaging {mean / line:.0%} of it, spread "
            f"over {span} day(s). The reporting line was not read from a rulebook: it is the "
            f"lowest round figure these tranches never cross, so the same test works in any "
            f"jurisdiction. Tranche sizes vary by only {cv:.0%}, which is a payer sizing to a "
            f"limit rather than invoicing for goods, and the sender never once pays this "
            f"receiver above the line. Spreading deposits out defeats the {cfg.structuring_window_days}-day "
            f"structuring window; it does not change any of that.")
        ev = [str(t) for t in grp["txn_id"].tolist()[:12]]
        for acc in (sender, receiver):
            flags[acc] = LAYER_META["ADAPTIVE"]["weight"]
            reasons[acc].append({"layer": "ADAPTIVE", "detail": detail, "evidence": ev})
    return flags, dict(reasons)


def detect_adaptive(df: pd.DataFrame, G: nx.DiGraph, cfg: Config) -> tuple[dict, dict]:
    """L9. Score the act of evading the rules rather than the typology."""
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    if not len(df) or df["amount"].max() <= 0:
        return flags, dict(reasons)

    agg_flags, agg_reasons = _detect_slow_aggregation(df, cfg)
    flags.update(agg_flags)
    for acc, items in agg_reasons.items():
        reasons[acc].extend(items)

    edge_count = {(u, v): int(d["count"]) for u, v, d in G.edges(data=True)}
    chains = _extend_chains(df, edge_count, cfg)
    if not chains:
        return flags, dict(reasons)      # aggregation findings, if any, still stand

    net = _net_throughput(df)
    # Each account's own baseline: what a transaction of theirs usually weighs.
    per_account_amounts: dict[str, list] = defaultdict(list)
    for row in df.itertuples(index=False):
        per_account_amounts[row.sender].append(float(row.amount))
        per_account_amounts[row.receiver].append(float(row.amount))

    best: dict[str, tuple] = {}
    seen_sig: set = set()
    for chain in chains:
        sig = _chain_signature(chain)
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        hops = len(chain)
        if hops < cfg.adaptive_min_hops:
            continue
        members = [chain[0].sender] + [h.receiver for h in chain]
        closed = members[-1] == members[0]
        uniq = set(members)
        if len(uniq) < cfg.adaptive_min_hops:
            continue

        amounts = [float(h.amount) for h in chain]
        days = [int(h.day) for h in chain]
        conserved = amounts[-1] / amounts[0] if amounts[0] else 0.0
        if conserved < cfg.adaptive_min_conservation:
            continue

        # ---- D1 rhythm -------------------------------------------------
        gaps = [days[i + 1] - days[i] for i in range(len(days) - 1)]
        rhythm = False
        gap_cv = None
        # Three gaps is the fewest that can distinguish a schedule from a
        # coincidence; two evenly spaced hops happen by chance all the time.
        if len(gaps) >= 3 and all(g >= 1 for g in gaps):
            mean_gap = sum(gaps) / len(gaps)
            var = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
            gap_cv = math.sqrt(var) / mean_gap if mean_gap else 0.0
            rhythm = gap_cv <= cfg.adaptive_rhythm_cv and mean_gap >= 2

        # ---- D2 conservation, and a parcel that keeps its size ----------
        mean_amt = sum(amounts) / len(amounts)
        amt_var = sum((a - mean_amt) ** 2 for a in amounts) / len(amounts)
        amt_cv = math.sqrt(amt_var) / mean_amt if mean_amt else 1.0
        conservation = amt_cv <= cfg.adaptive_amount_cv and conserved >= cfg.adaptive_min_conservation

        # ---- D3 net exposure once cover traffic is cancelled -------------
        shares = []
        for acc in uniq:
            moved = sum(a for h, a in zip(chain, amounts)
                        if h.sender == acc or h.receiver == acc)
            denom = net.get(acc, 0.0)
            shares.append(min(1.0, moved / denom) if denom > 0 else 0.0)
        net_share = sum(shares) / len(shares) if shares else 0.0
        exposure = net_share >= cfg.adaptive_net_dedication

        agree = sum((rhythm, conservation, exposure))
        if agree < 2:
            continue

        # A closed loop of one-shot hops is the stronger finding: the money
        # demonstrably came back. An open chain proves less, so it has to clear
        # all three tests and run longer before it is worth an analyst's time.
        if not closed and (agree < 3 or hops < cfg.adaptive_min_hops + 1):
            continue

        detail = _adaptive_detail(members, amounts, days, gaps, gap_cv, conserved,
                                  net_share, closed, rhythm, conservation, exposure)
        for acc in uniq:
            prev = best.get(acc)
            if prev is None or agree > prev[0]:
                best[acc] = (agree, detail, [h.txn_id for h in chain])

    for acc, (_agree, detail, evidence) in best.items():
        # One weight per layer per account, however many ways the layer saw it.
        flags[acc] = LAYER_META["ADAPTIVE"]["weight"]
        reasons[acc].append({"layer": "ADAPTIVE", "detail": detail, "evidence": evidence})
    return flags, dict(reasons)


def _adaptive_detail(members, amounts, days, gaps, gap_cv, conserved, net_share,
                     closed, rhythm, conservation, exposure) -> str:
    shape = ("closed loop" if closed else "chain")
    path = " -> ".join(members if len(members) <= 7 else members[:6] + ["..."])
    parts = [
        f"Threshold-evasive {shape} of {len(amounts)} one-shot hops carrying "
        f"Rs {int(amounts[0]):,} from day {days[0]} to day {days[-1]} "
        f"({days[-1] - days[0]} days, too slow for the circular-flow window): {path}."
    ]
    if rhythm and gap_cv is not None:
        parts.append(
            f"Hops land every {sum(gaps) / len(gaps):.1f} days with only {gap_cv:.0%} variation, "
            f"a payment schedule rather than trade; genuine commercial flow between "
            f"the same parties is irregular.")
    if conservation:
        parts.append(
            f"{conserved:.0%} of the original sum survives to the far end and the hop "
            f"amounts barely move, so this is one parcel being relayed, not "
            f"{len(amounts)} unrelated payments that happen to line up.")
    if exposure:
        parts.append(
            f"After cancelling the reciprocal round-trips these accounts use as cover, "
            f"the relay accounts for {net_share:.0%} of their remaining activity. The "
            f"cover traffic returns to the counterparty it came from and nets to nothing.")
    parts.append(
        "Detected without reference to the CTR band, the circular-flow span cap or the "
        "conduit window, so sitting outside those numbers does not evade it.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# L5 - Betweenness centrality (corroborating)
# --------------------------------------------------------------------------

def detect_centrality(G: nx.DiGraph, primary_flags: dict, cfg: Config) -> tuple[dict, dict, dict]:
    """Chokepoint detection *inside the suspicious sub-network*.

    Betweenness over the whole ledger is dominated by ordinary high-traffic
    accounts, which tells an investigator nothing. The question that matters
    operationally is narrower: within the criminal sub-network we have already
    identified, which account carries the most payment paths - i.e. which
    single freeze does the most damage to the operation. So the metric is
    computed on the sub-network induced by the primary alerts plus their
    immediate counterparties.
    """
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    if not primary_flags:
        return flags, dict(reasons), {n: 0.0 for n in G.nodes()}

    scope = set(primary_flags)
    for n in list(primary_flags):
        scope |= set(G.predecessors(n)) | set(G.successors(n))
    sub = G.subgraph(scope)
    cent = nx.betweenness_centrality(sub)
    vals = np.array([cent[n] for n in primary_flags if n in cent], dtype=float)
    if vals.size == 0:
        return flags, dict(reasons), {n: 0.0 for n in G.nodes()}
    mu, sd = float(vals.mean()), float(vals.std())
    thresh = mu + cfg.centrality_z * sd if sd > 0 else float(vals.max())

    for acc in primary_flags:
        score = cent.get(acc, 0.0)
        if score <= 0 or score < thresh:
            continue
        flags[acc] = LAYER_META["CENTRALITY"]["weight"]
        reasons[acc].append({
            "layer": "CENTRALITY",
            "detail": (f"Chokepoint of the suspicious sub-network: betweenness {score:.4f}, "
                       f"{(score - mu) / (sd or 1):+.1f} standard deviations above the other alerted "
                       f"accounts. A disproportionate share of criminal payment paths runs through "
                       f"this single account, so an account freeze here fragments the network rather "
                       f"than displacing it. Corroborates the primary flag; does not create it."),
            "evidence": []})

    full = {n: 0.0 for n in G.nodes()}
    full.update({n: float(v) for n, v in cent.items()})
    return flags, dict(reasons), full


# --------------------------------------------------------------------------
# L6 - IsolationForest (corroborating)
# --------------------------------------------------------------------------

def build_features(df: pd.DataFrame, accounts: list[str]) -> np.ndarray:
    sent = defaultdict(list)
    recv = defaultdict(list)
    for row in df.itertuples(index=False):
        sent[row.sender].append(row)
        recv[row.receiver].append(row)
    rows = []
    for acc in accounts:
        s, r = sent.get(acc, []), recv.get(acc, [])
        s_amt = np.array([x.amount for x in s], dtype=float)
        r_amt = np.array([x.amount for x in r], dtype=float)
        below = np.concatenate([s_amt, r_amt]) if (len(s) + len(r)) else np.array([])
        below_ratio = float(((below >= 0.85 * CTR_THRESHOLD) & (below < CTR_THRESHOLD)).mean()) if below.size else 0.0
        # Hold time is the gap from each credit to the first debit within a week.
        # Comparing every debit against every credit is quadratic in an account's
        # own traffic, and one hub in a real export - a merchant with tens of
        # thousands of legs on each side - is then the entire run. Sorting the
        # debit days once and searching them gives the same answer in n log n.
        holds = []
        if s and r:
            send_days = np.sort(np.fromiter((x.day for x in s), dtype=np.int64, count=len(s)))
            recv_days = np.fromiter((x.day for x in r), dtype=np.int64, count=len(r))
            nxt = np.searchsorted(send_days, recv_days, side="left")
            has_next = nxt < send_days.size
            gaps = send_days[np.clip(nxt, 0, send_days.size - 1)] - recv_days
            holds = gaps[has_next & (gaps <= 7)].tolist()
        rows.append([
            len(s), len(r),
            float(s_amt.sum()) if s_amt.size else 0.0,
            float(r_amt.sum()) if r_amt.size else 0.0,
            float(s_amt.std(ddof=1)) if s_amt.size > 1 else 0.0,
            len({x.receiver for x in s}),
            len({x.sender for x in r}),
            below_ratio,
            float(np.median(holds)) if holds else 7.0,
        ])
    return np.nan_to_num(np.array(rows, dtype=float))


def isolation_scores(df: pd.DataFrame, accounts: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Fit the forest and score every account. Depends on the ledger alone, not
    on any threshold, so an ablation sweep fits it once instead of once per
    configuration."""
    X = build_features(df, accounts)
    iso = IsolationForest(n_estimators=300, random_state=42, contamination="auto")
    iso.fit(X)
    return X, iso.decision_function(X)


def detect_ml_anomaly(df: pd.DataFrame, accounts: list[str], primary_flags: dict, cfg: Config,
                      scored: tuple | None = None):
    X, scores = scored if scored is not None else isolation_scores(df, accounts)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    for acc, score, row in zip(accounts, scores, X):
        eligible = (not cfg.ml_confirming_only) or (acc in primary_flags)
        if score < cfg.ml_cutoff and eligible:
            flags[acc] = LAYER_META["ML-ANOMALY"]["weight"]
            z = (row - mu) / sd
            top = sorted(zip(FEATURE_NAMES, z), key=lambda t: -abs(t[1]))[:3]
            reasons[acc].append({
                "layer": "ML-ANOMALY",
                "detail": ("IsolationForest isolates this account at score {:.3f}. Largest deviations: "
                           .format(score) + "; ".join(f"{n} (z={v:+.1f})" for n, v in top) +
                           ". Unsupervised, so it corroborates rather than originates the alert."),
                "evidence": []})
    return flags, dict(reasons), dict(zip(accounts, map(float, scores))), X


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def risk_tier(score: int) -> tuple[str, str, str]:
    if score >= 4:
        return "CRITICAL", "Freeze review by senior compliance officer; file STR within 7 days", "T+0"
    if score >= 3:
        return "HIGH", "Escalate to compliance officer the same business day", "T+0"
    if score >= 2:
        return "MEDIUM", "Analyst review queue", "T+1"
    return "LOW", "Auto-logged; monitor for recurrence", "T+7"


# The tunables each detector actually reads. The ablation, discriminator and
# robustness sweeps re-run the pipeline two dozen times over one ledger while
# varying a handful of thresholds, so most layers are asked the same question
# repeatedly. Keying a layer's result on the inputs it depends on turns those
# repeats into lookups; a layer whose thresholds moved is still recomputed.
LAYER_INPUTS = {
    "STRUCTURING": ("structuring_floor_pct", "structuring_min_count", "structuring_window_days",
                    "smurf_min_senders", "structuring_require_avoidance", "structuring_min_crossers",
                    "structuring_crosser_share", "agg_min_count", "agg_window_days"),
    "FAN": ("fan_min_branches", "fan_min_dedication", "fan_max_retention", "fan_max_inflation"),
    "CYCLE": ("cycle_max_len", "cycle_max_span_slack", "cycle_require_monotone",
              "cycle_max_edge_count", "cycle_min_conservation"),
    "PASS-THROUGH": ("pt_ratio_low", "pt_ratio_high", "pt_window_days", "pt_min_amount",
                     "pt_max_counterparties", "pt_max_repeats", "pt_rail_span_days"),
    "ADAPTIVE": ("adaptive_min_hops", "adaptive_hop_low", "adaptive_hop_high",
                 "adaptive_min_conservation", "adaptive_rhythm_cv", "adaptive_amount_cv",
                 "adaptive_net_dedication", "adaptive_max_edge_count", "adaptive_min_amount",
                 "adaptive_agg_min_count", "adaptive_agg_amount_cv", "adaptive_agg_hug",
                 "adaptive_agg_multiple"),
}


def run_pipeline(df: pd.DataFrame, G: nx.DiGraph, accounts: list[str], cfg: Config,
                 cache: dict | None = None) -> dict:
    flags: dict[str, int] = {}
    reasons: dict[str, list] = defaultdict(list)
    layers_hit: dict[str, set] = defaultdict(set)

    def memo(name, fn, fields=()):
        """Results are read, never mutated, so callers can share one object."""
        if cache is None:
            return fn()
        key = (name,) + tuple(getattr(cfg, f) for f in fields)
        if key not in cache:
            cache[key] = fn()
        return cache[key]

    def merge(res):
        f, rs = res[0], res[1]
        for acc, w in f.items():
            flags[acc] = flags.get(acc, 0) + w
        for acc, items in rs.items():
            reasons[acc].extend(items)
            for it in items:
                layers_hit[acc].add(it["layer"])

    if cfg.use_structuring:
        merge(memo("STRUCTURING", lambda: detect_structuring(df, cfg), LAYER_INPUTS["STRUCTURING"]))
    if cfg.use_fan:
        merge(memo("FAN", lambda: detect_fan(G, cfg), LAYER_INPUTS["FAN"]))
    cycle_report: dict = {}
    if cfg.use_cycles:
        cyc = memo("CYCLE", lambda: detect_cycles(G, cfg), LAYER_INPUTS["CYCLE"])
        merge(cyc)
        cycle_report = cyc[2]
    if cfg.use_pass_through:
        merge(memo("PASS-THROUGH", lambda: detect_pass_through(df, G, cfg),
                   LAYER_INPUTS["PASS-THROUGH"]))
    if cfg.use_adaptive:
        merge(memo("ADAPTIVE", lambda: detect_adaptive(df, G, cfg), LAYER_INPUTS["ADAPTIVE"]))

    primary = dict(flags)                     # snapshot before corroborating layers
    centrality: dict = {}
    if cfg.use_centrality:
        cf, cr, centrality = detect_centrality(G, primary, cfg)
        merge((cf, cr))
    else:
        # Full-graph betweenness is O(V*E) and this branch only runs when the
        # layer is switched off, so its result is never scored against. Sample
        # the pivots on large graphs rather than stalling an ablation run.
        k = min(G.number_of_nodes(), 200) or None
        centrality = memo("BETWEENNESS", lambda: (
            nx.betweenness_centrality(G, k=k, seed=42) if G.number_of_nodes() > 200
            else nx.betweenness_centrality(G)))

    ml_scores: dict = {}
    if cfg.use_ml:
        scored = memo("ISOLATION", lambda: isolation_scores(df, accounts))
        mf, mr, ml_scores, _ = detect_ml_anomaly(df, accounts, primary, cfg, scored)
        merge((mf, mr))

    # de-duplicate identical reasons (a node can sit in several fan patterns)
    for acc, items in reasons.items():
        seen, uniq = set(), []
        for it in items:
            key = (it["layer"], it["detail"])
            if key not in seen:
                seen.add(key)
                uniq.append(it)
        reasons[acc] = uniq

    return {"flags": flags, "reasons": dict(reasons), "primary": primary,
            "layers": {k: sorted(v) for k, v in layers_hit.items()},
            "centrality": centrality, "ml_scores": ml_scores,
            "cycle_report": cycle_report}


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

def evaluate(flags: dict, accounts: list[str], truth: set[str]) -> dict:
    if not truth:
        return {"labelled": False, "precision": None, "recall": None, "f1": None,
                "tp": None, "fp": None, "fn": None, "tn": None,
                "false_positives": [], "false_negatives": []}
    y_true = [1 if a in truth else 0 for a in accounts]
    y_pred = [1 if a in flags else 0 for a in accounts]
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "labelled": True,
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        "false_positives": [a for a in accounts if a in flags and a not in truth],
        "false_negatives": [a for a in accounts if a not in flags and a in truth],
    }


def run_ablation(df, G, accounts, truth, cfg: Config, cache: dict | None = None) -> list[dict]:
    configs = [
        ("L1 Structuring only", dict(use_fan=False, use_cycles=False, use_pass_through=False,
                                     use_adaptive=False, use_centrality=False, use_ml=False)),
        ("+ L2 Fan patterns", dict(use_cycles=False, use_pass_through=False,
                                   use_adaptive=False, use_centrality=False, use_ml=False)),
        ("+ L3 Cycles", dict(use_pass_through=False, use_adaptive=False,
                             use_centrality=False, use_ml=False)),
        ("+ L4 Pass-through (all rules)", dict(use_adaptive=False, use_centrality=False,
                                               use_ml=False)),
        ("+ L9 Evasion-adaptive", dict(use_centrality=False, use_ml=False)),
        ("+ L5 Centrality", dict(use_ml=False)),
        ("+ L6 ML corroboration (full)", dict()),
    ]
    out = []
    for name, over in configs:
        c = Config.from_dict({**cfg.to_dict(), **over})
        res = run_pipeline(df, G, accounts, c, cache)
        m = evaluate(res["flags"], accounts, truth)
        out.append({"name": name, **{k: m[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")},
                    "flagged": len(res["flags"])})
    # ML with no graph context at all, as a baseline for comparison
    c = Config.from_dict({**cfg.to_dict(), "ml_confirming_only": False})
    scored = cache.get(("ISOLATION",)) if cache is not None else None
    mf, _, _, _ = detect_ml_anomaly(df, accounts, {}, c, scored)
    m = evaluate(mf, accounts, truth)
    out.append({"name": "ML alone, no graph context (baseline)",
                **{k: m[k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")},
                "flagged": len(mf)})
    return out


DISCRIMINATORS = [
    ("Threshold avoidance (L1)", {"structuring_require_avoidance": False},
     "Without it, a payroll bureau paid just under the CTR line looks like structuring."),
    ("Branch dedication (L2)", {"fan_min_dedication": 0.0},
     "Without it, any customer paying three contractors who share a supplier is a fan-out ring."),
    ("Standing relationship (L3)", {"cycle_max_edge_count": 999},
     "Without it, ordinary card settlement between merchant, PSP and acquirer is a laundering loop."),
    ("Settlement-rail recurrence (L4)", {"pt_max_repeats": 999},
     "Without it, an escrow agent or acquiring bank is indistinguishable from a money mule."),
    ("One-shot corridor (L9)", {"adaptive_max_edge_count": 999},
     "Without it, every recurring settlement relay reads as a threshold-evasive relay chain."),
    ("Inferred reporting line (L9)", {"adaptive_agg_hug": 0.0},
     "Without it, any account paying the same counterparty repeatedly looks like slow structuring."),
]


def run_discriminator_ablation(df, G, accounts, truth, decoys, cfg: Config,
                               cache: dict | None = None) -> list[dict]:
    """Turn each look-alike discriminator off in isolation.

    The detection layers find the shapes. These four tests are what stop the
    system flagging every business that happens to have the same shape, so this
    table is the one that says whether the precision figure is earned.
    """
    rows = []
    base = run_pipeline(df, G, accounts, cfg, cache)
    bm = evaluate(base["flags"], accounts, truth)
    rows.append({"name": "All discriminators on (baseline)", "why": "",
                 "precision": bm["precision"], "recall": bm["recall"], "f1": bm["f1"],
                 "fp": bm["fp"], "decoys_caught": sum(1 for a in decoys if a in base["flags"]),
                 "decoys_total": len(decoys)})
    for name, over, why in DISCRIMINATORS:
        c = Config.from_dict({**cfg.to_dict(), **over})
        res = run_pipeline(df, G, accounts, c, cache)
        m = evaluate(res["flags"], accounts, truth)
        rows.append({"name": f"Without: {name}", "why": why,
                     "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
                     "fp": m["fp"], "decoys_caught": sum(1 for a in decoys if a in res["flags"]),
                     "decoys_total": len(decoys)})
    allover = {}
    for _, over, _ in DISCRIMINATORS:
        allover.update(over)
    c = Config.from_dict({**cfg.to_dict(), **allover})
    res = run_pipeline(df, G, accounts, c, cache)
    m = evaluate(res["flags"], accounts, truth)
    rows.append({"name": "All discriminators off (shape matching only)",
                 "why": "This is what a topology-only AML detector actually scores.",
                 "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
                 "fp": m["fp"], "decoys_caught": sum(1 for a in decoys if a in res["flags"]),
                 "decoys_total": len(decoys)})
    return rows


def run_robustness(df, G, accounts, baseline: set[str], cfg: Config,
                   cache: dict | None = None) -> dict:
    def jac(a, b):
        return 1.0 if not a and not b else len(a & b) / len(a | b)
    perturbations = [
        ("ML cutoff -15%", {"ml_cutoff": cfg.ml_cutoff * 1.15}),
        ("ML cutoff +15%", {"ml_cutoff": cfg.ml_cutoff * 0.85}),
        ("Centrality z -15%", {"centrality_z": cfg.centrality_z * 0.85}),
        ("Centrality z +15%", {"centrality_z": cfg.centrality_z * 1.15}),
        ("Structuring window -1d", {"structuring_window_days": max(1, cfg.structuring_window_days - 1)}),
        ("Structuring window +1d", {"structuring_window_days": cfg.structuring_window_days + 1}),
        ("Pass-through window +1d", {"pt_window_days": cfg.pt_window_days + 1}),
        ("Fan min branches +1", {"fan_min_branches": cfg.fan_min_branches + 1}),
        ("Cycle span slack +2", {"cycle_max_span_slack": cfg.cycle_max_span_slack + 2}),
        ("Cycle monotonicity off", {"cycle_require_monotone": False}),
        # L9's tunables belong here for the same reason as everyone else's. The
        # panel above this table says every threshold is perturbed independently,
        # and a layer that sits out of its own robustness check is exactly the
        # sort of thing a reviewer is entitled to point at.
        ("Evasion rhythm -25%", {"adaptive_rhythm_cv": cfg.adaptive_rhythm_cv * 0.75}),
        ("Evasion rhythm +25%", {"adaptive_rhythm_cv": cfg.adaptive_rhythm_cv * 1.25}),
        ("Evasion net dedication -15%", {"adaptive_net_dedication": cfg.adaptive_net_dedication * 0.85}),
        ("Evasion net dedication +15%", {"adaptive_net_dedication": cfg.adaptive_net_dedication * 1.15}),
        ("Evasion conservation +25%", {"adaptive_min_conservation": cfg.adaptive_min_conservation * 1.25}),
        ("Inferred line hug -10%", {"adaptive_agg_hug": cfg.adaptive_agg_hug * 0.90}),
    ]
    rows = []
    for name, over in perturbations:
        c = Config.from_dict({**cfg.to_dict(), **over})
        res = run_pipeline(df, G, accounts, c, cache)
        rows.append({"name": name, "jaccard": round(jac(baseline, set(res["flags"])), 3),
                     "flagged": len(res["flags"])})
    return {"perturbations": rows,
            "mean_jaccard": round(float(np.mean([r["jaccard"] for r in rows])), 3)}


# --------------------------------------------------------------------------
# Evasion benchmark
# --------------------------------------------------------------------------
# Two ledgers built specifically to defeat this engine, scored with L9 on and
# off. Everything else in the report measures the system against criminals who
# behave the way the textbook says. This measures it against criminals who have
# read our thresholds, which is the version of the problem a bank actually has
# once a detection rule has been in production for a season.


def _evasive_relay_ledger(seed: int = 11) -> tuple[pd.DataFrame, set]:
    """A six-account loop built to sit outside every published number at once.

    Hops five days apart, so the circular-flow span cap never closes the loop.
    Amounts nowhere near the CTR band, so L1 never looks. Every member given
    reciprocal trade with outside accounts, so the fan layer's dedication ratio
    stays low and the conduit layer sees too many counterparties.
    """
    rng = random.Random(seed)
    rows, tid = [], [0]

    def add(s_, r_, a_, d_):
        tid[0] += 1
        d_ = int(max(1, min(30, d_)))
        rows.append({"txn_id": f"EV{tid[0]:04d}", "sender": s_, "receiver": r_,
                     "amount": int(a_), "day": d_, "channel": "NEFT",
                     "timestamp": f"{EPOCH + timedelta(days=d_ - 1)}T12:00:00"})

    ring = [f"EVADE{i}" for i in range(6)]
    amount, day = 640_000.0, 1.0
    for i in range(6):
        add(ring[i], ring[(i + 1) % 6], amount, round(day))
        amount *= 1 - rng.uniform(0, 0.05)
        day += 5 + rng.uniform(-1, 1)
    for i, node in enumerate(ring):
        for k in range(3):
            add(f"COVER{i}{k}", node, rng.randint(250_000, 400_000), rng.randint(1, 30))
            add(node, f"COVER{i}{k}", rng.randint(230_000, 380_000), rng.randint(1, 30))
    return pd.DataFrame(rows), set(ring)


def _peeling_chain_ledger(seed: int = 4242) -> tuple[pd.DataFrame, set]:
    """A typology no layer in this engine was written for.

    A large sum walks a line of accounts, shedding a slice to a fresh one-time
    beneficiary at every hop. It never returns to its origin, the branches never
    reconverge, and the amounts sit nowhere near a reporting line, so none of
    L1 to L4 has a rule that matches it. Included as a generalisation test:
    whatever L9 scores here, it scores without ever having been shown the shape.
    """
    rng = random.Random(seed)
    rows, tid = [], [0]

    def add(s_, r_, a_, d_):
        tid[0] += 1
        d_ = int(max(1, min(30, d_)))
        rows.append({"txn_id": f"PL{tid[0]:04d}", "sender": s_, "receiver": r_,
                     "amount": int(a_), "day": d_, "channel": "NEFT",
                     "timestamp": f"{EPOCH + timedelta(days=d_ - 1)}T12:00:00"})

    nodes = [f"PEEL{i}" for i in range(8)]
    truth, carried, day = set(), 4_150_000.0, 1
    for i in range(7):
        truth.update({nodes[i], nodes[i + 1]})
        shed = carried * rng.uniform(0.10, 0.16)
        add(nodes[i], f"BENEF{i}", shed, day + 1)
        carried -= shed
        add(nodes[i], nodes[i + 1], carried, day)
        day += rng.randint(2, 6)
    for i, node in enumerate(nodes):
        for k in range(2):
            add(f"TRADE{i}{k}", node, rng.randint(200_000, 900_000), rng.randint(1, 30))
            add(node, f"TRADE{i}{k}", rng.randint(200_000, 900_000), rng.randint(1, 30))
    return pd.DataFrame(rows), truth


EVASION_SCENARIOS = (
    ("Threshold-evasive relay ring", _evasive_relay_ledger,
     "Six accounts moving one sum in a loop, every parameter placed outside the "
     "published thresholds and every member given reciprocal cover traffic."),
    ("Peeling chain (typology not implemented)", _peeling_chain_ledger,
     "A sum walking a line of accounts, shedding a slice to a fresh beneficiary at "
     "each hop. No layer in this engine was written for this shape."),
)


def evasion_benchmark(cfg: Config | None = None) -> list[dict]:
    """Score both adversarial ledgers with the adaptive layer on and off."""
    cfg = cfg or Config()
    without = Config.from_dict({**cfg.to_dict(), "use_adaptive": False})
    out = []
    for name, build, note in EVASION_SCENARIOS:
        ledger, truth = build()
        meta = {"rings": {}, "decoys": {}, "roles": {}}
        off = analyse(ledger, truth, without, meta, do_ablation=False)["metrics"]
        on = analyse(ledger, truth, cfg, meta, do_ablation=False)["metrics"]
        out.append({
            "scenario": name, "note": note,
            "criminal_accounts": len(truth),
            "recall_without_adaptive": round(float(off["recall"] or 0), 4),
            "recall_with_adaptive": round(float(on["recall"] or 0), 4),
            "precision_with_adaptive": round(float(on["precision"] or 0), 4),
            "caught_without": int(off["tp"] or 0),
            "caught_with": int(on["tp"] or 0),
            "false_positives_with": int(on["fp"] or 0),
        })
    return out


# --------------------------------------------------------------------------
# Ring / community detection - on DETECTED accounts, never on ground truth
# --------------------------------------------------------------------------

def detect_rings(G: nx.DiGraph, flags: dict, reasons: dict) -> tuple[list[dict], float]:
    sub = G.subgraph(list(flags)).to_undirected()
    # Ring numbering is what the alert queue, the exports and the case
    # narrative all cite, so it must not depend on the order the components
    # happened to come back in. Total risk still decides the ranking; the
    # remaining ties are settled on the members themselves.
    comps = [sorted(c) for c in nx.connected_components(sub)]
    comps.sort(key=lambda c: (-sum(flags.get(a, 0) for a in c), -len(c), c))

    rings = []
    for i, comp in enumerate(comps, 1):
        members = sorted(comp)
        layers = sorted({r["layer"] for a in members for r in reasons.get(a, [])})
        value = sum(d["amount"] for u, v, d in G.subgraph(members).edges(data=True))
        primary_layer = next((l for l in ("STRUCTURING", "FAN", "CYCLE", "PASS-THROUGH") if l in layers), "MIXED")
        rings.append({
            "ring_id": f"RING-{i:02d}",
            "typology": LAYER_META.get(primary_layer, {}).get("label", "Mixed typology"),
            "primary_layer": primary_layer,
            "members": members,
            "size": len(members),
            "internal_value": int(value),
            "layers": layers,
            "peak_risk": max((flags.get(a, 0) for a in members), default=0),
        })

    # modularity of that partition against the whole graph (valid, exhaustive partition)
    covered = set().union(*comps) if comps else set()
    partition = [set(c) for c in comps] + [{n} for n in G.nodes() if n not in covered]
    try:
        mod = float(nx.community.modularity(G.to_undirected(), partition))
    except Exception:
        mod = 0.0
    return rings, mod


# --------------------------------------------------------------------------
# L7 - Suspicion propagation (lead generation, NOT flagging)
# --------------------------------------------------------------------------

def suspicion_propagation(G: nx.DiGraph, flags: dict, cfg: Config, top_n: int = 25) -> dict:
    """Two independent views of guilt-by-association, deliberately kept separate
    from the alerting pipeline so propagation can never inflate precision.

    (1) Personalised PageRank seeded on confirmed alerts, weighted by risk score.
    (2) Bounded-hop contamination with geometric decay on the undirected graph,
        which also gives the analyst the actual path back to a known bad account.
    """
    if not flags:
        return {"pagerank": [], "contamination": [], "seeds": []}

    total = sum(flags.values())
    personalization = {n: (flags.get(n, 0) / total) for n in G.nodes()}
    try:
        pr = nx.pagerank(G, alpha=cfg.prop_alpha, personalization=personalization, max_iter=200)
    except nx.PowerIterationFailedConvergence:
        pr = nx.pagerank(G, alpha=cfg.prop_alpha, personalization=personalization,
                         max_iter=1000, tol=1e-4)

    UG = G.to_undirected()
    contamination: dict[str, float] = defaultdict(float)
    nearest: dict[str, tuple] = {}
    for seed, w in flags.items():
        if seed not in UG:
            continue
        lengths = nx.single_source_shortest_path_length(UG, seed, cutoff=cfg.prop_max_hops)
        for node, dist in lengths.items():
            if node in flags or dist == 0:
                continue
            score = w * (cfg.prop_decay ** dist)
            contamination[node] += score
            if node not in nearest or dist < nearest[node][1]:
                nearest[node] = (seed, dist)

    max_c = max(contamination.values()) if contamination else 1.0
    contam_rows = []
    for node, score in sorted(contamination.items(), key=lambda t: (-t[1], t[0]))[:top_n]:
        seed, dist = nearest[node]
        try:
            path = nx.shortest_path(UG, seed, node)
        except nx.NetworkXNoPath:
            path = [seed, node]
        contam_rows.append({
            "account": node,
            "score": round(score / max_c, 4),
            "raw": round(score, 4),
            "hops": dist,
            "nearest_alert": seed,
            "path": path,
            "verdict": ("Enhanced due diligence - one hop from a confirmed alert" if dist == 1
                        else "Monitor - indirect exposure at {} hops".format(dist)),
        })

    pr_rows = [{"account": n, "pagerank": round(float(s), 6)}
               for n, s in sorted(pr.items(), key=lambda t: -t[1]) if n not in flags][:top_n]
    return {"pagerank": pr_rows, "contamination": contam_rows,
            "seeds": sorted(flags, key=lambda a: -flags[a])[:10]}


# --------------------------------------------------------------------------
# L8 - Laundering timeline
# --------------------------------------------------------------------------

STAGE_COLOURS = {"PLACEMENT": "Entry of illicit value into the banking channel",
                 "LAYERING": "Movement designed to obscure origin",
                 "INTEGRATION": "Exit into apparently legitimate use",
                 "BACKGROUND": "Unrelated ordinary activity"}


def _classify_stage(sender_flagged: bool, receiver_flagged: bool,
                    receiver_has_outflow: bool, structuring_txn: bool) -> str:
    if not sender_flagged and not receiver_flagged:
        return "BACKGROUND"
    if structuring_txn:
        return "PLACEMENT"
    if not sender_flagged and receiver_flagged:
        return "PLACEMENT"
    if sender_flagged and not receiver_flagged:
        return "INTEGRATION"
    if sender_flagged and receiver_flagged and not receiver_has_outflow:
        return "INTEGRATION"
    return "LAYERING"


def build_timeline(df: pd.DataFrame, G: nx.DiGraph, flags: dict, reasons: dict,
                   rings: list[dict]) -> dict:
    ring_of: dict[str, str] = {}
    for r in rings:
        for m in r["members"]:
            ring_of[m] = r["ring_id"]

    # Evidence lists mix transaction ids with "A->B" edge labels. Selecting them
    # by a "TXN" prefix only worked for ids this engine generated itself: an
    # uploaded ledger carrying its own txn_id scheme produced an empty set here,
    # and every placement leg was silently mis-staged. Edge labels are the thing
    # with a stable syntax, so exclude those instead.
    structuring_txns: set[str] = set()
    for acc, items in reasons.items():
        for it in items:
            if it["layer"] == "STRUCTURING":
                structuring_txns.update(t for t in it.get("evidence", []) if "->" not in t)

    has_out = {n: G.out_degree(n) > 0 for n in G.nodes()}

    events = []
    for row in df.sort_values(["day", "txn_id"], kind="stable").itertuples(index=False):
        sf, rf = row.sender in flags, row.receiver in flags
        stage = _classify_stage(sf, rf, has_out.get(row.receiver, False), row.txn_id in structuring_txns)
        if stage == "BACKGROUND":
            continue
        events.append({
            "txn_id": row.txn_id,
            "day": int(row.day),
            "timestamp": row.timestamp,
            "sender": row.sender,
            "receiver": row.receiver,
            "amount": int(row.amount),
            "channel": row.channel,
            "stage": stage,
            "ring": ring_of.get(row.sender) or ring_of.get(row.receiver) or "UNASSIGNED",
            "sender_risk": int(flags.get(row.sender, 0)),
            "receiver_risk": int(flags.get(row.receiver, 0)),
        })

    # daily series (illicit vs background) for the chart
    min_day = int(df["day"].min()) if len(df) else 1
    max_day = int(df["day"].max()) if len(df) else 1
    lo_day = min(1, min_day)
    by_day = {d: {"day": d, "date": str(EPOCH + timedelta(days=d - 1)),
                  "PLACEMENT": 0, "LAYERING": 0, "INTEGRATION": 0,
                  "count": 0, "background": 0} for d in range(lo_day, max_day + 1)}
    for e in events:
        by_day[e["day"]][e["stage"]] += e["amount"]
        by_day[e["day"]]["count"] += 1
    flagged_txn_ids = {e["txn_id"] for e in events}
    for row in df.itertuples(index=False):
        if row.txn_id not in flagged_txn_ids:
            by_day[int(row.day)]["background"] += int(row.amount)
    series = [by_day[d] for d in sorted(by_day)]
    running = 0
    for s in series:
        running += s["PLACEMENT"] + s["LAYERING"] + s["INTEGRATION"]
        s["cumulative"] = running

    # per-ring episode summary
    episodes = []
    for r in rings:
        ev = [e for e in events if e["ring"] == r["ring_id"]]
        if not ev:
            continue
        days = [e["day"] for e in ev]
        stage_val = defaultdict(int)
        for e in ev:
            stage_val[e["stage"]] += e["amount"]
        duration = max(days) - min(days) + 1
        value = sum(e["amount"] for e in ev)
        # longest time funds rested inside the ring
        rest = 0
        by_acc = defaultdict(lambda: {"in": [], "out": []})
        for e in ev:
            by_acc[e["receiver"]]["in"].append(e["day"])
            by_acc[e["sender"]]["out"].append(e["day"])
        for acc, d in by_acc.items():
            if d["in"] and d["out"]:
                gaps = [o - i for i in d["in"] for o in d["out"] if o >= i]
                if gaps:
                    rest = max(rest, min(gaps))
        episodes.append({
            "ring_id": r["ring_id"],
            "typology": r["typology"],
            "members": r["members"],
            "first_day": min(days), "last_day": max(days),
            "first_date": str(EPOCH + timedelta(days=min(days) - 1)),
            "last_date": str(EPOCH + timedelta(days=max(days) - 1)),
            "duration_days": duration,
            "hops": len(ev),
            "total_value": value,
            "velocity": round(value / duration, 2),
            "max_resting_days": rest,
            "stage_breakdown": {k: int(v) for k, v in stage_val.items()},
            "events": ev,
        })
    episodes.sort(key=lambda e: (e["first_day"], e["ring_id"]))

    return {"events": events, "series": series, "episodes": episodes,
            "stage_legend": STAGE_COLOURS, "max_day": max_day}


# --------------------------------------------------------------------------
# Narrative + STR dossier
# --------------------------------------------------------------------------

def build_narrative(acc: str, tier: str, layers: list[str], reasons: list[dict],
                    total_in: int, total_out: int, ring: dict | None) -> str:
    typ = ", ".join(LAYER_META.get(l, {}).get("label", l).lower() for l in layers) or "anomalous activity"
    ring_txt = ""
    if ring:
        others = [m for m in ring["members"] if m != acc]
        ring_txt = (f" The account does not act alone: it is one of {ring['size']} accounts in "
                    f"{ring['ring_id']}, alongside {', '.join(others[:6])}"
                    f"{' and others' if len(others) > 6 else ''}, moving Rs {ring['internal_value']:,} "
                    f"between themselves.")
    return (
        f"Account {acc} has been assessed as {tier} risk. Over the observation window it received "
        f"Rs {total_in:,} and disbursed Rs {total_out:,}. The account was surfaced by {len(layers)} "
        f"independent detection layer(s) evidencing {typ}.{ring_txt} "
        f"Taken together the transaction pattern has no apparent lawful or economic purpose and is "
        f"consistent with the placement and layering of proceeds of crime. A Suspicious Transaction "
        f"Report is warranted under Section 12 of the Prevention of Money-Laundering Act, 2002 read "
        f"with Rule 3 of the PML (Maintenance of Records) Rules, 2005."
    )


def generate_dossier(acc: str, df: pd.DataFrame, G: nx.DiGraph, result: dict,
                     rings: list[dict], accounts: list[str], feature_matrix: np.ndarray | None = None) -> dict:
    flags, reasons = result["flags"], result["reasons"]
    score = flags.get(acc, 0)
    tier, action, sla = risk_tier(score)

    sent = df[df["sender"] == acc]
    recv = df[df["receiver"] == acc]
    total_out = int(sent["amount"].sum()) if len(sent) else 0
    total_in = int(recv["amount"].sum()) if len(recv) else 0

    if feature_matrix is None:
        feature_matrix = build_features(df, accounts)
    mu, sd = feature_matrix.mean(axis=0), feature_matrix.std(axis=0)
    sd[sd == 0] = 1.0
    z = {}
    if acc in accounts:
        row = feature_matrix[accounts.index(acc)]
        z = {FEATURE_NAMES[i]: round(float((row[i] - mu[i]) / sd[i]), 3) for i in range(len(FEATURE_NAMES))}

    # Sorted, because a subgraph view built from a small node set iterates that
    # set rather than the parent graph, and a set of strings has no stable order
    # between processes. The dossier is a filing document: two runs over one
    # ledger have to produce the same file.
    neighbours = sorted(set(G.predecessors(acc)) | set(G.successors(acc)) | {acc})
    sg = G.subgraph(neighbours)
    ring = next((r for r in rings if acc in r["members"]), None)
    layers = result["layers"].get(acc, [])

    txn_rows = pd.concat([sent, recv]).sort_values("day", kind="stable")
    return {
        "report_type": "SUSPICIOUS TRANSACTION REPORT (STR)",
        "regulatory_basis": ("Section 12, Prevention of Money-Laundering Act, 2002 r/w Rule 3, "
                             "PML (Maintenance of Records) Rules, 2005 - reported to FIU-IND"),
        "report_reference": f"STR/AMLT/{date.today().year}/{acc}",
        "reporting_entity": "AMLTrace Analytics - prototype reporting entity",
        "date_of_report": str(date.today()),
        "account_id": acc,
        "risk_score": int(score),
        "risk_tier": tier,
        "recommended_action": action,
        "sla": sla,
        "ctr_threshold_applied": CTR_THRESHOLD,
        "narrative": build_narrative(acc, tier, layers, reasons.get(acc, []), total_in, total_out, ring),
        "grounds_of_suspicion": [
            {"layer": r["layer"], "layer_label": LAYER_META.get(r["layer"], {}).get("label", r["layer"]),
             "role": LAYER_META.get(r["layer"], {}).get("role", "primary"),
             "detail": r["detail"], "evidence": r.get("evidence", [])}
            for r in reasons.get(acc, [])
        ],
        "transaction_summary": {
            "total_inflow": total_in,
            "total_outflow": total_out,
            "net_position": total_in - total_out,
            "transaction_count": int(len(sent) + len(recv)),
            "counterparties": len(neighbours),
            "first_activity_day": int(txn_rows["day"].min()) if len(txn_rows) else None,
            "last_activity_day": int(txn_rows["day"].max()) if len(txn_rows) else None,
            "credits_just_below_ctr": int(((recv["amount"] >= 0.85 * CTR_THRESHOLD) &
                                           (recv["amount"] < CTR_THRESHOLD)).sum()) if len(recv) else 0,
        },
        "behavioural_z_scores": z,
        "betweenness_centrality": round(float(result["centrality"].get(acc, 0.0)), 6),
        "ml_anomaly_score": round(float(result["ml_scores"].get(acc, 0.0)), 4) if result["ml_scores"] else None,
        "associated_ring": ({"ring_id": ring["ring_id"], "typology": ring["typology"],
                             "members": ring["members"], "internal_value": ring["internal_value"]}
                            if ring else None),
        "one_hop_subgraph": {
            "nodes": [{"id": n, "risk": int(flags.get(n, 0)), "tier": risk_tier(flags.get(n, 0))[0]}
                      for n in neighbours],
            "edges": [{"source": u, "target": v, "amount": int(d["amount"]), "count": int(d["count"])}
                      for u, v, d in sorted(sg.edges(data=True), key=lambda e: (e[0], e[1]))],
        },
        "transactions": [
            {"txn_id": r.txn_id, "date": r.timestamp[:10], "day": int(r.day),
             "direction": "DEBIT" if r.sender == acc else "CREDIT",
             "counterparty": r.receiver if r.sender == acc else r.sender,
             "amount": int(r.amount), "channel": r.channel}
            for r in txn_rows.itertuples(index=False)
        ],
        "action_taken": ("Sub-network temporary hold recommended; account and its one-hop neighbourhood "
                         "quarantined pending FIU-IND acknowledgement." if tier in ("CRITICAL", "HIGH")
                         else "Continued monitoring; no hold recommended at this risk tier."),
    }


# --------------------------------------------------------------------------
# Hard-negative analysis - did we survive the lawful look-alikes?
# --------------------------------------------------------------------------

def analyse_decoys(decoys: dict, flags: dict, reasons: dict, truth: set) -> dict:
    """Score the detector against activity that is lawful but shaped like crime.

    This is the number that actually matters. Precision on a ledger where only
    criminals form triangles is close to meaningless; precision measured against
    deliberately confusing legitimate business is a real claim.
    """
    if not decoys:
        return {"present": False}
    families: dict[str, dict] = {}
    for acc, fam in decoys.items():
        f = families.setdefault(fam, {"family": fam, "accounts": [], "caught": [], "layers": {}})
        f["accounts"].append(acc)
        if acc in flags:
            f["caught"].append(acc)
            for r in reasons.get(acc, []):
                f["layers"][r["layer"]] = f["layers"].get(r["layer"], 0) + 1
    rows = []
    for fam, f in sorted(families.items()):
        n, c = len(f["accounts"]), len(f["caught"])
        rows.append({
            "family": fam,
            "accounts": n,
            "caught": c,
            "survived": n - c,
            "discrimination": round(1 - c / n, 3) if n else 1.0,
            "layers_tripped": f["layers"],
            "caught_accounts": sorted(f["caught"]),
        })
    total = len(decoys)
    caught = sum(1 for a in decoys if a in flags)
    fps = [a for a in flags if truth and a not in truth]
    return {
        "present": True,
        "total": total,
        "caught": caught,
        "discrimination": round(1 - caught / total, 3) if total else 1.0,
        "families": rows,
        "share_of_false_positives": round(caught / len(fps), 3) if fps else 0.0,
        "note": ("Every account above is lawful. Each family is engineered to reproduce the "
                 "topology of one typology, so anything caught here is a detector matching "
                 "shape without understanding purpose."),
    }


# --------------------------------------------------------------------------
# Alert economics - what a threshold choice actually costs
# --------------------------------------------------------------------------

REVIEW_MINUTES = {"CRITICAL": 240, "HIGH": 120, "MEDIUM": 45, "LOW": 10}


def alert_economics(alerts: list, summary: dict, metrics: dict, dataset: dict,
                    analyst_cost_per_hour: int = 1200, portfolio_multiplier: int = 1) -> dict:
    """Translate a threshold setting into money and analyst hours.

    Detection quality is not the decision a bank actually makes; the decision is
    how many analysts to staff. Industry alert-to-report conversion sits in the
    5-10% range, so the cost of a loose threshold is measured in salaried hours,
    not in an F1 score.
    """
    days = max(1, dataset.get("days", 30))
    scale = 365.0 / days                       # observation window -> annual
    tiers = summary["tiers"]

    minutes = sum(REVIEW_MINUTES[t] * n for t, n in tiers.items())
    hours = minutes / 60.0
    annual_hours = hours * scale * portfolio_multiplier
    annual_cost = annual_hours * analyst_cost_per_hour

    exposure = summary["exposure"]
    illicit = summary["illicit_flow"]

    out = {
        "assumptions": {
            "analyst_cost_per_hour": analyst_cost_per_hour,
            "review_minutes_by_tier": REVIEW_MINUTES,
            "observation_days": days,
            "portfolio_multiplier": portfolio_multiplier,
            "productive_hours_per_fte_year": 1800,
        },
        "alerts_per_day": round(summary["flagged"] / days, 2),
        "alerts_per_year": round(summary["flagged"] * scale * portfolio_multiplier),
        "review_hours_window": round(hours, 1),
        "review_hours_year": round(annual_hours),
        "analyst_fte": round(annual_hours / 1800, 2),
        "annual_review_cost": round(annual_cost),
        "value_at_risk": exposure,
        "illicit_flow_surfaced": illicit,
        "return_per_analyst_hour": round(illicit / hours) if hours else 0,
        # Divided by the same annualised, portfolio-scaled alert count the cost
        # itself was built from. Dividing by the unscaled count instead made the
        # cost of reviewing one alert rise with the size of the book, which is
        # the opposite of what the multiplier means: a bank ten times the size
        # has ten times the alerts and ten times the bill, at the same unit cost.
        "cost_per_alert": (round(annual_cost / (summary["flagged"] * scale * portfolio_multiplier))
                           if summary["flagged"] else 0),
    }

    if metrics.get("labelled"):
        fp, tp, fn = metrics["fp"], metrics["tp"], metrics["fn"]
        fp_minutes = sum(REVIEW_MINUTES[a["risk_tier"]] for a in alerts
                         if a.get("is_true_positive") is False)
        wasted_hours = fp_minutes / 60.0
        out["false_positive_burden"] = {
            "false_positives": fp,
            "wasted_hours_window": round(wasted_hours, 1),
            "wasted_cost_year": round(wasted_hours * scale * analyst_cost_per_hour * portfolio_multiplier),
            "share_of_review_effort": round(wasted_hours / hours, 3) if hours else 0,
        }
        out["conversion_rate"] = round(tp / (tp + fp), 3) if (tp + fp) else 0
        out["industry_comparison"] = {
            "typical_bank_conversion": 0.06,
            "note": ("Large banks typically convert 4-10% of AML alerts into a filed report. "
                     "Anything far above that is either a better detector or an easier book "
                     "of business - on synthetic data, assume the latter until proven otherwise."),
        }
        out["missed"] = {"accounts": fn,
                         "note": "Undetected accounts carry unquantified risk; recall is the expensive metric."}
    return out


# --------------------------------------------------------------------------
# Streaming replay - detection as the money actually moves
# --------------------------------------------------------------------------

REPLAY_MAX_STEPS = 120     # pipeline re-runs per replay; the ledger sets the day count


def replay(df: pd.DataFrame, truth: set, cfg: Config, ring_labels: dict | None = None) -> dict:
    """Re-run detection each day on only the transactions seen so far.

    Batch scoring of a closed month flatters every AML system ever built. The
    operational question is when an alert could first have fired, because every
    day of latency is another day the money is gone. This walks the ledger
    forward one day at a time and records the moment each account first becomes
    detectable.
    """
    ring_labels = ring_labels or {}
    max_day = int(df["day"].max()) if len(df) else 0
    frames, first_seen = [], {}
    prev: set = set()

    # Each step re-runs the whole pipeline, so the cost is linear in the number
    # of steps and an uploaded ledger sets that number. Past the budget the walk
    # advances in even strides instead of daily: latency stays measurable, the
    # run stays bounded.
    stride = max(1, -(-max_day // REPLAY_MAX_STEPS))
    days = list(range(stride, max_day + 1, stride))
    if days and days[-1] != max_day:
        days.append(max_day)

    for day in days:
        window = df[df["day"] <= day]
        if window.empty:
            continue
        G = build_graph(window)
        accounts = sorted(G.nodes())
        res = run_pipeline(window, G, accounts, cfg)
        flags = res["flags"]
        new = [a for a in flags if a not in prev]
        for a in new:
            first_seen[a] = day
        tiers = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for a, sc in flags.items():
            tiers[risk_tier(sc)[0]] += 1
        m = evaluate(flags, accounts, truth) if truth else {"labelled": False}
        frames.append({
            "day": day,
            "date": str(EPOCH + timedelta(days=day - 1)),
            "txns_seen": int(len(window)),
            "value_seen": int(window["amount"].sum()),
            "flagged": len(flags),
            "tiers": tiers,
            "new_alerts": sorted(new),
            "accounts": sorted(flags),
            "precision": round(m["precision"], 3) if m.get("labelled") else None,
            "recall": round(m["recall"], 3) if m.get("labelled") else None,
        })
        prev = set(flags)

    entry = {}
    for row in df.itertuples(index=False):
        for a in (row.sender, row.receiver):
            d = int(row.day)
            if a not in entry or d < entry[a]:
                entry[a] = d
    latency = []
    for acc, day in sorted(first_seen.items(), key=lambda t: (t[1], t[0])):
        if truth and acc not in truth:
            continue
        latency.append({
            "account": acc,
            "typology": ring_labels.get(acc, "-"),
            "first_active_day": entry.get(acc),
            "detected_day": day,
            "latency_days": day - entry.get(acc, day),
        })
    missed = sorted(a for a in truth if a not in first_seen) if truth else []
    lat_vals = [x["latency_days"] for x in latency]
    return {
        "frames": frames,
        "latency": latency,
        "never_detected": missed,
        "max_day": max_day,
        "summary": {
            "mean_latency_days": round(float(np.mean(lat_vals)), 2) if lat_vals else None,
            "median_latency_days": float(np.median(lat_vals)) if lat_vals else None,
            "worst_latency_days": max(lat_vals) if lat_vals else None,
            "first_alert_day": next((f["day"] for f in frames if f["flagged"]), None),
            "detected": len(latency),
            "never_detected": len(missed),
        },
    }


# --------------------------------------------------------------------------
# Orchestrator - one call produces the entire application state
# --------------------------------------------------------------------------

def analyse(df: pd.DataFrame, truth: set[str], cfg: Config,
            meta: dict | None = None, do_ablation: bool = True) -> dict:
    # The web layer always arrives through load_dataset_from_records, which
    # establishes every optional column. A caller using the engine as a library
    # need not, and the timeline reads a timestamp off each row, so a frame
    # carrying only sender, receiver and amount is filled in here rather than
    # failing several layers down with an attribute error.
    df = _ensure_columns(df)
    meta = meta or {}
    ring_labels = meta.get("rings", {})
    decoys = meta.get("decoys", {})
    roles = meta.get("roles", {})
    G = build_graph(df)
    accounts = sorted(G.nodes())
    # One memo for the whole call. The sweeps below re-run the pipeline about
    # two dozen times over this same ledger, and nearly every layer is being
    # asked something it has already answered.
    cache: dict = {}
    result = run_pipeline(df, G, accounts, cfg, cache)
    flags, reasons = result["flags"], result["reasons"]

    metrics = evaluate(flags, accounts, truth)
    rings, modularity_score = detect_rings(G, flags, reasons)
    propagation = suspicion_propagation(G, flags, cfg)
    timeline = build_timeline(df, G, flags, reasons, rings)

    ablation = run_ablation(df, G, accounts, truth, cfg, cache) if (do_ablation and truth) else []
    disc_ablation = (run_discriminator_ablation(df, G, accounts, truth, decoys, cfg, cache)
                     if (do_ablation and truth and decoys) else [])
    robustness = (run_robustness(df, G, accounts, set(flags), cfg, cache) if do_ablation
                  else {"perturbations": [], "mean_jaccard": None})

    # One pass each, reused by the alert rows and the graph payload below.
    # These were previously full-column scans inside per-account loops, i.e.
    # O(accounts x transactions) twice over - the dominant cost on any real
    # ledger, and quadratic in the size of an upload.
    total_value = int(df["amount"].sum()) if len(df) else 0
    sent_by = df.groupby("sender")["amount"].sum().to_dict() if len(df) else {}
    recv_by = df.groupby("receiver")["amount"].sum().to_dict() if len(df) else {}
    sent_n = df["sender"].value_counts().to_dict() if len(df) else {}
    recv_n = df["receiver"].value_counts().to_dict() if len(df) else {}
    ring_of = {m: r for r in rings for m in r["members"]}

    tiers = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    alerts = []
    for acc, score in sorted(flags.items(), key=lambda t: (-t[1], t[0])):
        tier, action, sla = risk_tier(score)
        tiers[tier] += 1
        sent = int(sent_by.get(acc, 0))
        recv = int(recv_by.get(acc, 0))
        ring = ring_of.get(acc)
        alerts.append({
            "account": acc, "risk_score": int(score), "risk_tier": tier,
            "action": action, "sla": sla,
            "layers": result["layers"].get(acc, []),
            "inflow": recv, "outflow": sent, "exposure": max(sent, recv),
            "txn_count": int(sent_n.get(acc, 0) + recv_n.get(acc, 0)),
            "ring": ring["ring_id"] if ring else None,
            "typology": ring["typology"] if ring else "Standalone",
            "centrality": round(float(result["centrality"].get(acc, 0.0)), 6),
            "ml_score": round(float(result["ml_scores"].get(acc, 0.0)), 4) if result["ml_scores"] else None,
            "reasons": reasons.get(acc, []),
            "is_true_positive": (acc in truth) if truth else None,
        })

    # Observed against published: the ledger is synthetic, so the only honest
    # claim about it is that its rails behave like the published national mix.
    # Measured on ordinary traffic alone, because the typologies deliberately
    # skew away from RTGS and would otherwise flatter the comparison.
    channel_mix = []
    if roles:
        ordinary = df[df["sender"].map(lambda a: roles.get(a) == "ordinary")
                      & df["receiver"].map(lambda a: roles.get(a) == "ordinary")]
        n = max(1, len(ordinary))
        obs = ordinary["channel"].value_counts()
        for ch, (vol, _val) in IN.CHANNEL_MIX.items():
            channel_mix.append({"channel": ch,
                                "observed": round(100.0 * int(obs.get(ch, 0)) / n, 2),
                                "published": round(100.0 * vol, 2),
                                "note": f"avg ticket Rs {IN.AVERAGE_TICKET[ch]:,}"})

    warnings_out = []
    cr = result.get("cycle_report") or {}
    if cr.get("truncated"):
        warnings_out.append({
            "layer": "CYCLE",
            "message": ("Circular-flow search was stopped early: " + cr.get("reason", "budget reached")
                        + ". This ledger is dense enough that enumerating every loop does not "
                          "finish in reasonable time, so some may be unreported. Treat the cycle "
                          "layer's recall on this ledger as a lower bound."),
        })

    decoy_report = analyse_decoys(decoys, flags, reasons, truth)
    exposure = sum(a["exposure"] for a in alerts)
    illicit_flow = sum(e["amount"] for e in timeline["events"])

    layer_counts = defaultdict(int)
    for acc, ls in result["layers"].items():
        for l in ls:
            layer_counts[l] += 1

    # graph payload for the browser (whole network; the UI filters)
    nodes = [{"id": n, "risk": int(flags.get(n, 0)), "tier": risk_tier(flags.get(n, 0))[0],
              "ring": (ring_of[n]["ring_id"] if n in ring_of else None),
              "truth": (n in truth) if truth else None,
              "volume": int(sent_by.get(n, 0) + recv_by.get(n, 0))}
             for n in accounts]
    edges = [{"source": u, "target": v, "amount": int(d["amount"]), "count": int(d["count"]),
              "first_day": d["first_day"], "last_day": d["last_day"],
              "illicit": (u in flags and v in flags)}
             for u, v, d in G.edges(data=True)]

    payload = {
        "config": cfg.to_dict(),
        "dataset": {
            "accounts": len(accounts),
            "transactions": int(len(df)),
            "total_value": total_value,
            "days": int(df["day"].max()) if len(df) else 0,
            "labelled": bool(truth),
            "fraud_linked_accounts": len(truth),
            "decoy_accounts": len(decoys),
            "epoch": str(EPOCH),
        },
        "summary": {
            "flagged": len(flags),
            "tiers": tiers,
            "rings": len(rings),
            "modularity": round(modularity_score, 4),
            "exposure": int(exposure),
            "illicit_flow": int(illicit_flow),
            "illicit_share": round(illicit_flow / total_value, 4) if total_value else 0,
            "layer_counts": dict(layer_counts),
            "watchlist": len(propagation["contamination"]),
        },
        "metrics": metrics,
        "decoys": decoy_report,
        "discriminator_ablation": disc_ablation,
        "ablation": ablation,
        "robustness": robustness,
        "alerts": alerts,
        "rings": rings,
        "propagation": propagation,
        "timeline": timeline,
        "graph": {"nodes": nodes, "edges": edges},
        "layer_meta": LAYER_META,
        "accounts": accounts,
        "ring_labels": ring_labels,
        "provenance": meta.get("calibration"),
        "channel_mix": channel_mix,
        "ledger_channel_mix": channel_mix,
        "warnings": warnings_out,
        "roles": meta.get("roles", {}),
    }
    payload["economics"] = alert_economics(alerts, payload["summary"], metrics, payload["dataset"])
    return payload
