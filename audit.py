"""
Self-audit: is the 1.00 / 1.00 real, or an artefact of how the data was built?

Five checks:
  A  ledger <-> graph integrity      (does the graph faithfully represent the ledger)
  B  deck <-> engine sync            (do the slide numbers match a fresh run)
  C  seed stability                  (does 1.00 survive on ledgers it never saw)
  D  circularity probe               (how much of 1.00 is the generator agreeing
                                      with the detector's own thresholds)
  E  label audit                     (is ground truth defined circularly)
"""
import json, os, sys, io

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import aml_engine as E

OK, BAD = "  [ok]  ", "  [!!]  "
issues = []


def head(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74)


# ---------------------------------------------------------------- A ledger/graph
head("A  Ledger <-> graph integrity")
df, truth, meta = E.generate_dataset()
G = E.build_graph(df)

ledger_total = int(df["amount"].sum())
graph_total = int(sum(d["amount"] for _, _, d in G.edges(data=True)))
ledger_accounts = set(df["sender"]) | set(df["receiver"])
edge_count_total = sum(d["count"] for _, _, d in G.edges(data=True))

print(f"{OK if graph_total == ledger_total else BAD}value: ledger Rs {ledger_total:,} == graph Rs {graph_total:,}")
print(f"{OK if set(G.nodes()) == ledger_accounts else BAD}nodes: {len(G.nodes())} == distinct accounts {len(ledger_accounts)}")
print(f"{OK if edge_count_total == len(df) else BAD}txns: edge counts sum {edge_count_total} == rows {len(df)}")
print(f"{OK if not any(u == v for u, v in G.edges()) else BAD}no self-loops")
dupes = df.duplicated(subset=["txn_id"]).sum()
print(f"{OK if dupes == 0 else BAD}txn_id unique ({dupes} duplicates)")
if graph_total != ledger_total or set(G.nodes()) != ledger_accounts or edge_count_total != len(df):
    issues.append("graph does not faithfully represent the ledger")

# -------------------------------------------------------------- B deck/engine
head("B  Deck <-> engine sync")
st = E.analyse(df, truth, E.Config(), meta)
fp = os.path.join("deck", "facts.json")
if os.path.exists(fp):
    F = json.load(open(fp, encoding="utf-8"))
    checks = [
        ("accounts", F["dataset"]["accounts"], st["dataset"]["accounts"]),
        ("transactions", F["dataset"]["transactions"], st["dataset"]["transactions"]),
        ("alerts", F["summary"]["flagged"], st["summary"]["flagged"]),
        ("rings", F["summary"]["rings"], st["summary"]["rings"]),
        ("precision", round(F["metrics"]["precision"], 6), round(st["metrics"]["precision"], 6)),
        ("recall", round(F["metrics"]["recall"], 6), round(st["metrics"]["recall"], 6)),
        ("decoys caught", F["decoys"]["caught"], st["decoys"]["caught"]),
        ("shape-only precision", round(F["discriminator_ablation"][-1]["precision"], 6),
         round(st["discriminator_ablation"][-1]["precision"], 6)),
    ]
    # The evasion slide carries the strongest claim in the deck, so it gets the
    # same treatment as the headline metrics: recomputed here, compared to what
    # the slide actually says.
    deck_ev = F.get("evasion", [])
    if deck_ev:
        live_ev = E.evasion_benchmark()
        by_name = {r["scenario"]: r for r in live_ev}
        for r in deck_ev:
            live = by_name.get(r["scenario"])
            if live is None:
                issues.append(f"deck claims an evasion scenario the engine no longer runs: {r['scenario']}")
                continue
            for field in ("recall_without_adaptive", "recall_with_adaptive", "false_positives_with"):
                checks.append((f"{r['scenario'][:18]} {field.split('_')[0]}", r[field], live[field]))

    for name, a, b in checks:
        same = a == b
        print(f"{OK if same else BAD}{name:24s} deck={a}  engine={b}")
        if not same:
            issues.append(f"deck out of sync with engine on {name}")
else:
    print(BAD + "deck/facts.json missing")

# ------------------------------------------------------------- C seed stability
head("C  Seed stability - ledgers the thresholds were never tuned on")
rows = []
for seed in (7, 13, 21, 42, 99, 101, 777, 2026, 31337, 5):
    d2, t2, m2 = E.generate_dataset(seed=seed)
    s2 = E.analyse(d2, t2, E.Config(), m2, do_ablation=False)
    m = s2["metrics"]
    rows.append((seed, m["precision"], m["recall"], m["f1"], m["fp"], m["fn"],
                 s2["decoys"]["caught"], s2["decoys"]["total"]))
    print(f"  seed {seed:<6} P={m['precision']:.3f}  R={m['recall']:.3f}  F1={m['f1']:.3f}  "
          f"FP={m['fp']:<2} FN={m['fn']:<2} look-alikes wrongly flagged {s2['decoys']['caught']}/{s2['decoys']['total']}")
P = [r[1] for r in rows]
R = [r[2] for r in rows]
print(f"\n  precision  mean {np.mean(P):.3f}  min {min(P):.3f}  max {max(P):.3f}")
print(f"  recall     mean {np.mean(R):.3f}  min {min(R):.3f}  max {max(R):.3f}")
if min(P) == 1.0 and min(R) == 1.0:
    print("\n  Perfect on every seed. That is NOT reassuring on its own - see check D.")

# ---------------------------------------------------------- D circularity probe
head("D  Circularity probe - make the criminals less textbook")
print("  The generator plants patterns whose parameters sit comfortably inside the")
print("  detector's thresholds. This bends each typology toward the legitimate side")
print("  and measures how fast recall falls away.\n")


def probe(label, mutate):
    """Regenerate with a mutated ledger and re-score with UNCHANGED thresholds."""
    d3, t3, m3 = E.generate_dataset()
    d3 = mutate(d3.copy())
    s3 = E.analyse(d3, t3, E.Config(), m3, do_ablation=False)
    m = s3["metrics"]
    print(f"  {label:<46} P={m['precision']:.2f}  R={m['recall']:.2f}  "
          f"TP={m['tp']:<3} FN={m['fn']}")
    return m


ROLES = meta.get("roles", {})


def _by_role(*roles):
    return {a for a, r in ROLES.items() if r in roles}


def spread_structuring(d):
    # structuring transfers spread over 12 days (beats the window test)
    src = _by_role("structuring_source")
    m = d["sender"].isin(src)
    d.loc[m, "day"] = d.loc[m, "day"] + np.arange(int(m.sum())) % 12
    return d


def slow_cycles(d):
    # cycle hops 3x further apart (beats the closing-speed test)
    cyc = _by_role("cycle_member")
    m = d["sender"].isin(cyc) & d["receiver"].isin(cyc)
    d.loc[m, "day"] = d.loc[m, "day"] * 3
    return d


def patient_mules(d):
    # mule holds the funds 4 days instead of 1 (beats the pass-through window)
    m = d["sender"].isin(_by_role("mule"))
    d.loc[m, "day"] = d.loc[m, "day"] + 4
    return d


def skim_harder(d):
    # mule forwards only 70% instead of ~97% (beats the conservation ratio)
    m = d["sender"].isin(_by_role("mule"))
    d.loc[m, "amount"] = (d.loc[m, "amount"] * 0.70).astype("int64")
    return d


def deeper_structuring(d):
    # deposits at 70% of CTR, outside the "just under the line" band
    m = d["sender"].isin(_by_role("structuring_source"))
    d.loc[m, "amount"] = 700_000
    return d


probe("baseline (textbook criminals)", lambda d: d)
probe("structuring spread over 12 days", spread_structuring)
probe("cycles closing 3x slower", slow_cycles)
probe("mules holding funds 4 days", patient_mules)
probe("mules skimming 30% instead of 3%", skim_harder)
probe("deposits at 70% of CTR, not 95%", deeper_structuring)

# ------------------------------------------------------------- E label audit
head("E  Label audit - is ground truth defined circularly?")
planted = sorted(truth)
prefixes = {}
for a in planted:
    prefixes.setdefault(ROLES.get(a, "?"), []).append(a)
print(f"  {len(planted)} accounts labelled fraud:")
for k, v in sorted(prefixes.items()):
    print(f"    {k:<22} {len(v):>2}  {', '.join(v[:4])}{' ...' if len(v) > 4 else ''}")

# accounts that PARTICIPATE in laundering flows but are deliberately NOT labelled
touching = set()
for row in df.itertuples(index=False):
    if row.sender in truth or row.receiver in truth:
        touching.add(row.sender)
        touching.add(row.receiver)
unlabelled_contacts = sorted(touching - truth)
print(f"\n  {len(unlabelled_contacts)} accounts transact directly with a labelled account but are")
print(f"  NOT labelled fraud (funders, cash-out beneficiaries, mule endpoints):")
print(f"    {', '.join(unlabelled_contacts[:14])}{' ...' if len(unlabelled_contacts) > 14 else ''}")
flagged = {a["account"] for a in st["alerts"]}
wrongly = sorted(set(unlabelled_contacts) & flagged)
print(f"  of those, wrongly alerted: {len(wrongly)} {wrongly if wrongly else ''}")

decoys = meta.get("decoys", {})
print(f"\n  {len(decoys)} additional accounts are lawful look-alikes, also unlabelled.")
print(f"  Total unlabelled accounts adjacent to or shaped like crime: "
      f"{len(set(unlabelled_contacts) | set(decoys))}")

head("VERDICT")
for i in issues:
    print(BAD + i)
if not issues:
    print(OK + "No integrity or sync faults found.")
print("""
  Read D as the real answer to "is 1.00 trustworthy".
  A and B say the pipeline is internally consistent and the deck matches the code.
  C says the result is not seed-luck.
  D says how much of it is the generator agreeing with the detector.
""")
