"""
Red team: attack AMLTrace the way a hostile reviewer would.

Not a list of caveats - each check below actually runs the system and reports
what it finds. Anything printed with [HOLE] is a real weakness someone can put
their finger on.
"""
import io, json, os, sys, time, warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

import aml_engine as E

HOLE, OK, INFO = "[HOLE] ", "[ ok ] ", "       "
holes = []


def head(t):
    print("\n" + "=" * 76 + f"\n{t}\n" + "=" * 76)


def hole(msg, detail=""):
    holes.append((msg, detail))
    print(HOLE + msg)
    if detail:
        for line in detail.split("\n"):
            print(INFO + line)


df, truth, meta = E.generate_dataset()
G = E.build_graph(df)
accounts = sorted(G.nodes())
cfg = E.Config()
base = E.analyse(df, truth, cfg, meta)

# ===========================================================================
head("1  Do the corroborating layers (L5, L6) actually do anything?")
# The pitch says 'eight layers'. If two of them cannot move precision or recall,
# a reviewer is entitled to ask what they are for.
no56 = E.Config.from_dict({**cfg.to_dict(), "use_centrality": False, "use_ml": False})
r56 = E.run_pipeline(df, G, accounts, no56)
m56 = E.evaluate(r56["flags"], accounts, truth)
mb = base["metrics"]
same_set = set(r56["flags"]) == {a["account"] for a in base["alerts"]}
print(f"{INFO}with L5+L6:    P={mb['precision']:.3f} R={mb['recall']:.3f} alerts={len(base['alerts'])}")
print(f"{INFO}without L5+L6: P={m56['precision']:.3f} R={m56['recall']:.3f} alerts={len(r56['flags'])}")
if same_set and abs(m56["precision"] - mb["precision"]) < 1e-9 and abs(m56["recall"] - mb["recall"]) < 1e-9:
    tiers_with = base["summary"]["tiers"]
    tiers_without = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a, sc in r56["flags"].items():
        tiers_without[E.risk_tier(sc)[0]] += 1
    hole("L5 and L6 change no metric at all - identical alert set, identical P/R.",
         f"They only move accounts between tiers: {tiers_with} -> {tiers_without}.\n"
         f"'Eight layers' is true, but two of them are unfalsifiable on your own evidence.\n"
         f"Fix: present them as a TRIAGE contribution (which alert an analyst opens first),\n"
         f"not a DETECTION contribution, and say so before someone asks.")
else:
    print(OK + "they change the alert set or the metrics")

# ===========================================================================
head("2  Is precision measured against a population that is trivially easy?")
# 187 accounts, but most are ordinary retail accounts with 1-2 transactions.
# The only genuinely hard negatives are the 19 decoys.
decoys = set(meta["decoys"])
flagged = {a["account"] for a in base["alerts"]}
easy = [a for a in accounts if a not in truth and a not in decoys]
print(f"{INFO}population: {len(accounts)} accounts")
print(f"{INFO}  {len(truth):>3} genuinely criminal")
print(f"{INFO}  {len(decoys):>3} hard negatives (lawful, criminal-shaped)")
print(f"{INFO}  {len(easy):>3} ordinary accounts")
tp = len(flagged & truth)
fp_hard = len(flagged & decoys)
fp_easy = len(flagged & set(easy))
prec_hard = tp / (tp + fp_hard) if (tp + fp_hard) else 0
print(f"{INFO}precision over everything        = {mb['precision']:.3f}")
print(f"{INFO}precision over hard cases only   = {prec_hard:.3f}  (criminals + decoys)")
if len(easy) / len(accounts) > 0.6:
    hole(f"{100*len(easy)/len(accounts):.0f}% of the population is trivially separable.",
         "Ordinary accounts have 1-2 transactions and no structure, so they can never be\n"
         "flagged by any layer. They pad the true-negative count and make precision look\n"
         "easier than it is. The hard-case precision above is the number worth quoting.")

# ===========================================================================
head("3  Is recall 1.00 by construction?")
# Every labelled account is a member of a planted typology, so every labelled
# account is reachable by the matching detector. There is no laundering account
# in this ledger that the design does not, in principle, cover.
roles = meta["roles"]
covered = {"structuring_source", "structuring_sink", "fan_origin", "fan_branch",
           "fan_destination", "cycle_member", "mule"}
uncovered = [a for a in truth if roles.get(a) not in covered]
print(f"{INFO}labelled criminal accounts: {len(truth)}")
print(f"{INFO}  belonging to a typology the detector implements: {len(truth) - len(uncovered)}")
print(f"{INFO}  belonging to no implemented typology:            {len(uncovered)}")
if not uncovered:
    hole("Every criminal account sits in a typology the detector was built to find.",
         "Recall cannot be less than 1.00 unless a threshold happens to miss - there is no\n"
         "laundering behaviour in this ledger that the system has no layer for.\n"
         "Fix: say 'recall 1.00 against the four typologies we implement', never\n"
         "'recall 1.00' unqualified. A fifth typology with no detector would fix it properly.")

# ===========================================================================
head("3b Held-out typology - does it generalise past what we coded for?")
# Check 3 says every labelled account belongs to a typology one of the layers
# was written for, so recall on that ledger cannot really fall. The only way to
# answer the question properly is to launder money a way the engine has never
# been shown and see what happens. Nothing below was used to tune any threshold.
#
# Peeling chain: a large sum walks a line of accounts, shedding a slice to a
# fresh one-time beneficiary at every hop. It never returns to its origin, so it
# is not a cycle. The branches never reconverge, so it is not a fan. The amounts
# sit nowhere near a reporting line, so it is not structuring. The hops keep only
# part of what arrived and rest for days, so it is not a conduit under L4.
peel_rows, ptid = [], [0]


def padd(s_, r_, a_, d_):
    ptid[0] += 1
    peel_rows.append({"txn_id": f"P{ptid[0]:04d}", "sender": s_, "receiver": r_,
                      "amount": int(a_), "day": int(d_), "channel": "NEFT",
                      "timestamp": f"2026-08-{min(28, max(1, int(d_))):02d}T12:00:00"})


import random as _r
_rng = _r.Random(4242)
peel_truth = set()
carried, day = 4_150_000.0, 1
chain_nodes = [f"PEEL{i}" for i in range(8)]
for i in range(7):
    peel_truth.add(chain_nodes[i])
    peel_truth.add(chain_nodes[i + 1])
    slice_off = carried * _rng.uniform(0.10, 0.16)
    padd(chain_nodes[i], f"BEN{i}", slice_off, day + 1)     # the peel, cashed out
    carried -= slice_off
    padd(chain_nodes[i], chain_nodes[i + 1], carried, day)  # the bulk moves on
    day += _rng.randint(2, 6)
# ordinary trade for every account on the chain, so nothing stands out by volume
for i, n in enumerate(chain_nodes):
    for k in range(2):
        padd(f"TRD{i}{k}", n, _rng.randint(200_000, 900_000), _rng.randint(1, 30))
        padd(n, f"TRD{i}{k}", _rng.randint(200_000, 900_000), _rng.randint(1, 30))

peel = pd.DataFrame(peel_rows)
st_peel = E.analyse(peel, peel_truth, cfg, {"rings": {}, "decoys": {}, "roles": {}},
                    do_ablation=False)
m_peel = st_peel["metrics"]
print(f"{INFO}peeling chain: 8 accounts, 7 hops, 10-16% shed to a fresh beneficiary each hop")
print(f"{INFO}no layer in this engine was written for this typology")
print(f"{INFO}caught: {m_peel['tp']}/{len(peel_truth)}   recall {m_peel['recall']:.2f}   "
      f"false positives {m_peel['fp']}")
if m_peel["recall"] >= 0.5:
    print(OK + f"generalises: {m_peel['tp']} of {len(peel_truth)} caught by shape alone, "
               f"with no typology-specific rule")
else:
    hole(f"A typology the engine was not built for is mostly missed "
         f"(recall {m_peel['recall']:.2f}).",
         "This is the honest ceiling of a rule-and-graph system: it finds the shapes it "
         "knows plus whatever the adaptive layer can generalise to, and a genuinely novel "
         "laundering pattern needs either a new layer or a supervised model trained on "
         "labelled outcomes. Report recall as 'against the typologies we implement'.")

# ===========================================================================
head("4  Does it survive a ledger with no crime in it at all?")
clean = df[df["sender"].map(lambda a: roles.get(a) == "ordinary")
           & df["receiver"].map(lambda a: roles.get(a) == "ordinary")].copy()
try:
    st_clean = E.analyse(clean, set(), cfg, {"rings": {}, "decoys": {}, "roles": {}},
                         do_ablation=False)
    n = st_clean["summary"]["flagged"]
    print(f"{INFO}{len(clean)} ordinary transactions, {st_clean['dataset']['accounts']} accounts")
    print(f"{INFO}alerts raised: {n}")
    if n == 0:
        print(OK + "no false alarms on a crime-free book")
    else:
        hole(f"{n} alerts on a ledger containing no crime.", "False-positive rate on clean data is not zero.")
except Exception as exc:
    hole("Crashes on a crime-free ledger.", f"{type(exc).__name__}: {exc}")

# ===========================================================================
head("5  Malformed and hostile input")
cases = {
    "empty file": "sender,receiver,amount\n",
    "self-loops only": "sender,receiver,amount,day\nA,A,50000,1\nA,A,60000,2\n",
    "negative amounts": "sender,receiver,amount,day\nA,B,-500000,1\nB,C,-400000,2\n",
    "zero amounts": "sender,receiver,amount,day\nA,B,0,1\nB,C,0,2\n",
    "non-numeric amount": "sender,receiver,amount,day\nA,B,abc,1\nB,C,x,2\n",
    "one account": "sender,receiver,amount,day\nA,B,100000,1\n",
    "duplicate txn ids": "txn_id,sender,receiver,amount,day\nT1,A,B,100000,1\nT1,B,C,90000,2\n",
    "huge day numbers": "sender,receiver,amount,day\nA,B,100000,999999\nB,C,90000,1\n",
    "unicode names": "sender,receiver,amount,day\nखाता१,खाता२,100000,1\nखाता२,खाता३,95000,2\n",
}
import csv as _csv
for name, blob in cases.items():
    try:
        recs = list(_csv.DictReader(io.StringIO(blob)))
        if not recs:
            print(f"{OK}{name:22s} -> rejected before parsing")
            continue
        d, t, m = E.load_dataset_from_records(recs)
        if len(d) == 0:
            print(f"{OK}{name:22s} -> parsed to empty ledger, no crash")
            continue
        s = E.analyse(d, t, cfg, m, do_ablation=False)
        print(f"{OK}{name:22s} -> {len(d)} txns, {s['summary']['flagged']} alerts")
    except E.LedgerError as exc:
        # A LedgerError is the ingest layer refusing a file on purpose and
        # saying why; the web layer turns it into a 400 the analyst can read.
        # Counting that as a crash overstated the weakness, which is the one
        # mistake a red team cannot afford to make.
        print(f"{OK}{name:22s} -> rejected: {exc}")
    except Exception as exc:
        hole(f"Uploading '{name}' crashes the analysis.", f"{type(exc).__name__}: {exc}")

# ===========================================================================
head("6  Scalability - what a judge with a real Kaggle file will hit")
sizes = [200, 600, 1500, 3000]
prev = None
for n in sizes:
    rng = np.random.default_rng(7)
    ids = [f"ACC{i:05d}" for i in range(max(40, n // 6))]
    rows = [{"txn_id": f"T{i}", "sender": ids[int(rng.integers(len(ids)))],
             "receiver": ids[int(rng.integers(len(ids)))],
             "amount": int(rng.integers(10_000, 900_000)),
             "day": int(rng.integers(1, 31)), "channel": "NEFT"} for i in range(n)]
    d = pd.DataFrame([r for r in rows if r["sender"] != r["receiver"]])
    t0 = time.time()
    try:
        E.analyse(d, set(), cfg, {"rings": {}, "decoys": {}, "roles": {}}, do_ablation=False)
        el = time.time() - t0
        rate = f"  ({el/prev[1]:.1f}x slower than {prev[0]} txns)" if prev else ""
        print(f"{INFO}{n:>5} txns / {d['sender'].nunique():>4} accounts -> {el:5.2f}s{rate}")
        prev = (n, el)
        if el > 25:
            hole(f"{n} transactions already takes {el:.1f}s.",
                 "A real dataset is 100k-1M rows. Cycle enumeration and betweenness are the\n"
                 "expensive parts and neither is windowed. The upload panel invites a judge to\n"
                 "try a file that will hang the demo.\n"
                 "Fix: cap uploads (the API already accepts a row limit) and say so in the UI.")
            break
    except Exception as exc:
        hole(f"Crashes at {n} transactions.", f"{type(exc).__name__}: {exc}")
        break

# ===========================================================================
head("7  Threshold gaming - can a launderer sit exactly in the blind spot?")
# Construct a launderer who deliberately violates every single discriminator's
# assumption at once, and see whether anything catches them.
rows = []
tid = 0


def add(s, r, a, d):
    global tid
    tid += 1
    rows.append({"txn_id": f"E{tid:04d}", "sender": s, "receiver": r, "amount": int(a),
                 "day": int(d), "channel": "NEFT",
                 "timestamp": f"2026-08-{min(28, max(1, int(d))):02d}T12:00:00"})


# an "evasive" ring: 6 hops, each 5 days apart, amounts nowhere near the CTR band,
# every intermediary given unrelated trade so dedication stays low
ring = [f"EV{i}" for i in range(6)]
for i in range(6):
    add(ring[i], ring[(i + 1) % 6], 640_000, 1 + i * 5)
for i, n in enumerate(ring):
    for k in range(3):
        add(f"OUT{i}{k}", n, 300_000 + k * 10_000, 2 + k * 7)
        add(n, f"OUT{i}{k}", 280_000 + k * 9_000, 4 + k * 7)
ev = pd.DataFrame(rows)
ev_truth = set(ring)
st_ev = E.analyse(ev, ev_truth, cfg, {"rings": {}, "decoys": {}, "roles": {}}, do_ablation=False)
m_ev = st_ev["metrics"]
print(f"{INFO}evasive ring: 6 accounts, 6-hop loop, 5 days per hop, amounts off the CTR band,")
print(f"{INFO}every member given unrelated trade")
print(f"{INFO}caught: {m_ev['tp']}/{len(ev_truth)}   recall {m_ev['recall']:.2f}")
if m_ev["recall"] < 0.5:
    hole(f"A launderer who knows the thresholds evades the system almost entirely "
         f"(recall {m_ev['recall']:.2f}).",
         "Every discriminator is a published rule, and a published rule is an evasion manual.\n"
         "This is inherent to rule-based AML, not a coding error - but you must name it\n"
         "before a judge does, and say adaptive per-institution thresholds are the answer.")
else:
    print(OK + f"still caught {m_ev['tp']} of {len(ev_truth)}")

# ===========================================================================
head("8  Determinism - does the same input always give the same answer?")
a1 = E.analyse(df, truth, cfg, meta, do_ablation=False)
a2 = E.analyse(df, truth, cfg, meta, do_ablation=False)
same = ({x["account"]: x["risk_score"] for x in a1["alerts"]}
        == {x["account"]: x["risk_score"] for x in a2["alerts"]})
print(OK + "identical alert set and scores on repeat runs" if same
      else HOLE + "non-deterministic between runs")
if not same:
    hole("Analysis is not deterministic.", "Two runs on identical input disagree.")

# ===========================================================================
head("9  Does the ring count depend on the alert set being complete?")
# Rings come from connected components of the ALERT sub-graph. If detection
# misses one bridging account, two rings become one - or nine become ten.
import random as _rnd
_rnd.seed(7)
drop = _rnd.choice([a["account"] for a in base["alerts"]])
flags2 = {a["account"]: a["risk_score"] for a in base["alerts"] if a["account"] != drop}
reasons2 = {a["account"]: a["reasons"] for a in base["alerts"] if a["account"] != drop}
rings2, _ = E.detect_rings(G, flags2, reasons2)
print(f"{INFO}rings with all {len(base['alerts'])} alerts: {len(base['rings'])}")
print(f"{INFO}rings after dropping one alert ({drop}): {len(rings2)}")
if len(rings2) != len(base["rings"]):
    hole("Ring count is sensitive to a single missed alert.",
         f"Dropping one account changes the ring count from {len(base['rings'])} to {len(rings2)}.\n"
         "'9 rings' is therefore a property of the detector's completeness, not of the data.\n"
         "Fix: quote ring count as a finding, never as an accuracy metric.")
else:
    print(OK + "ring count stable to a single dropped alert")

# ===========================================================================
head("VERDICT")
if not holes:
    print("  Nothing found. That usually means the tests are too gentle.")
else:
    print(f"  {len(holes)} weakness(es) a reviewer could put a finger on:\n")
    for i, (h, _) in enumerate(holes, 1):
        print(f"   {i}. {h}")
print("\n  None of these are coding errors except where marked. They are the limits of")
print("  the approach - which is exactly what a reviewer is testing you on.")
