"""
AMLTrace - headless report generator.

Runs the whole pipeline once with no server and writes every deliverable to
./outputs: STR dossiers, CSVs, an Excel workbook (if openpyxl is installed),
metrics JSON and a static network PNG (if matplotlib is installed).

    python generate_report.py [--seed 7] [--out outputs]

This is the offline counterpart to app.py - same engine, same numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import date, datetime

import pandas as pd

import aml_engine as E
import dataset_adapters as A


# Windows consoles default to cp1252 and choke on the arrows and bullets below.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(account, taken: set) -> str:
    """Account identifiers come out of the analysed ledger, so they are attacker
    controlled when that ledger is a third-party export. Interpolated into a
    path they escape the output directory ("../../..") and, on Windows, the
    colon the IBM adapter puts in every identifier opens an alternate data
    stream instead of a file. The dossier keeps the true identifier in its
    body; only the filename is reduced to a safe stem."""
    stem = _UNSAFE_NAME.sub("_", str(account)).strip("._-")[:80] or "account"
    candidate, n = stem, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{stem}_{n}"
    taken.add(candidate.lower())
    return candidate


def write_csv(frame: pd.DataFrame, path: str) -> None:
    """Every CSV this script writes goes through here, so the formula guard is
    not something a later export can be added without."""
    E.csv_safe_frame(frame).to_csv(path, index=False)


def sep(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def normalise_xlsx(path: str) -> None:
    """Strip the wall-clock time out of a written workbook.

    Every other artefact this script produces is reproducible byte for byte, and
    the workbook was the one exception. An .xlsx is a zip, and both the archive's
    member timestamps and the modified date in docProps/core.xml are taken from
    the clock rather than from the data, so two runs over an identical ledger
    produced files that differed. That is the kind of thing that makes a reviewer
    wonder what else moved between runs. Rewriting the archive with the ledger's
    own epoch on every member settles it without touching a single cell.
    """
    stamp = (E.EPOCH.year, E.EPOCH.month, E.EPOCH.day, 0, 0, 0)
    fixed = f"{E.EPOCH}T00:00:00Z"
    with zipfile.ZipFile(path) as src:
        members = [(i.filename, i.external_attr, src.read(i.filename)) for i in src.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, attr, data in members:
            if name == "docProps/core.xml":
                text = data.decode("utf-8")
                for tag in ("dcterms:created", "dcterms:modified"):
                    text = re.sub(f"(<{tag}[^>]*>)[^<]*(</{tag}>)",
                                  lambda m: m.group(1) + fixed + m.group(2), text)
                data = text.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = attr
            dst.writestr(info, data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--accounts", type=int, default=135)
    ap.add_argument("--txns", type=int, default=150)
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--csv", help="analyse this ledger instead of generating one")
    ap.add_argument("--limit", type=int, default=None,
                    help="read only the first N rows (for large public datasets)")
    ap.add_argument("--no-replay", action="store_true", help="skip the day-by-day replay")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cfg = E.Config()

    if args.csv:
        canon, schema = A.load_any(args.csv, limit=args.limit)
        df, truth, meta = E.load_dataset_from_records(canon.to_dict("records"))
        print(f"Loaded {len(df)} transactions from {args.csv} (schema detected: {schema})")
    else:
        df, truth, meta = E.generate_dataset(args.seed, args.accounts, args.txns)

    state = E.analyse(df, truth, cfg, meta)
    G = E.build_graph(df)
    d, s, m = state["dataset"], state["summary"], state["metrics"]

    sep("DATASET")
    print(f"{d['accounts']} accounts · {d['transactions']} transactions · "
          f"Rs {d['total_value']:,} moved over {d['days']} days")
    if truth:
        print(f"{d['fraud_linked_accounts']} accounts are genuinely fraud-linked (ground truth)")

    sep("ALERTS")
    for a in state["alerts"]:
        print(f"\n{a['account']:<10} score {a['risk_score']}  {a['risk_tier']:<8} "
              f"{a['typology']:<26} {a['ring'] or '-'}")
        print(f"           {a['action']}")
        for r in a["reasons"]:
            print(f"           [{r['layer']}] {r['detail'][:300]}")

    if m["labelled"]:
        sep("EVALUATION")
        print(f"TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
        print(f"Precision {m['precision']:.3f} · Recall {m['recall']:.3f} · F1 {m['f1']:.3f}")
        if m["false_positives"]:
            print("False positives:", ", ".join(m["false_positives"]))
        if m["false_negatives"]:
            print("Missed:", ", ".join(m["false_negatives"]))

        sep("ABLATION - what each layer adds")
        for r in state["ablation"]:
            print(f"{r['name']:<40} P={r['precision']:.2f} R={r['recall']:.2f} "
                  f"F1={r['f1']:.2f}  (TP={r['tp']} FP={r['fp']} FN={r['fn']})")

    sep("ADVERSARIAL BENCHMARK - a launderer who has read the thresholds")
    print("Layers 1 to 4 are published rules with numbers in them. Both ledgers below were")
    print("built to sit outside those numbers. Each is scored twice: with L9 off, and with it on.")
    print()
    evb = E.evasion_benchmark(cfg)
    for r in evb:
        print(f"  {r['scenario']}")
        print(f"    {r['note']}")
        print(f"    without L9: recall {r['recall_without_adaptive']:.2f} "
              f"({r['caught_without']}/{r['criminal_accounts']} caught)")
        print(f"    with L9:    recall {r['recall_with_adaptive']:.2f} "
              f"({r['caught_with']}/{r['criminal_accounts']} caught), "
              f"precision {r['precision_with_adaptive']:.2f}, "
              f"{r['false_positives_with']} false positives")
        print()

    dc = state.get("decoys", {})
    if dc.get("present"):
        sep("HARD NEGATIVES - lawful business shaped like financial crime")
        print(f"{dc['total'] - dc['caught']} of {dc['total']} look-alike accounts correctly NOT alerted "
              f"(discrimination {dc['discrimination']:.3f})")
        for f in dc["families"]:
            flag = "OK " if not f["caught"] else "!! "
            print(f"  {flag}{f['family']:<40} {f['caught']}/{f['accounts']} wrongly flagged"
                  + (f"  -> {', '.join(f['caught_accounts'])}" if f["caught"] else ""))

    da = state.get("discriminator_ablation", [])
    if da:
        sep("DISCRIMINATOR ABLATION - what stops the look-alikes")
        print(f"{'Configuration':<48}{'P':>6}{'R':>6}{'F1':>6}{'FP':>5}  look-alikes flagged")
        for r in da:
            print(f"{r['name']:<48}{r['precision']:6.2f}{r['recall']:6.2f}{r['f1']:6.2f}{r['fp']:5d}"
                  f"  {r['decoys_caught']}/{r['decoys_total']}")

    ec = state.get("economics", {})
    if ec:
        sep("ALERT ECONOMICS")
        print(f"Alerts per day        {ec['alerts_per_day']}")
        print(f"Review hours (window) {ec['review_hours_window']}")
        print(f"Analyst headcount     {ec['analyst_fte']} FTE")
        print(f"Annual review cost    Rs {ec['annual_review_cost']:,} "
              f"(at Rs {ec['assumptions']['analyst_cost_per_hour']}/hour)")
        print(f"Cost per alert        Rs {ec['cost_per_alert']:,}")
        print(f"Return per hour       Rs {ec['return_per_analyst_hour']:,} of illicit flow surfaced")
        if "conversion_rate" in ec:
            print(f"Conversion rate       {ec['conversion_rate']:.1%} "
                  f"(typical large bank ~{ec['industry_comparison']['typical_bank_conversion']:.0%})")
        fpb = ec.get("false_positive_burden")
        if fpb:
            print(f"Wasted on clean accts {fpb['wasted_hours_window']} h "
                  f"({fpb['share_of_review_effort']:.0%} of effort), "
                  f"Rs {fpb['wasted_cost_year']:,}/year")

    sep("ROBUSTNESS - threshold sensitivity")
    for r in state["robustness"]["perturbations"]:
        print(f"{r['name']:<28} alerts={r['flagged']:<4} Jaccard vs baseline {r['jaccard']:.2f}")
    print(f"\nMean stability: {state['robustness']['mean_jaccard']}")

    sep(f"RINGS - {s['rings']} detected (modularity {s['modularity']})")
    for r in state["rings"]:
        print(f"{r['ring_id']}  {r['typology']:<26} {r['size']} accounts  "
              f"Rs {r['internal_value']:,}  [{', '.join(r['layers'])}]")
        print(f"          {', '.join(r['members'])}")

    sep("LAUNDERING TIMELINE - episodes")
    for e in state["timeline"]["episodes"]:
        print(f"{e['ring_id']}  {e['first_date']} -> {e['last_date']}  "
              f"{e['duration_days']}d · {e['hops']} hops · Rs {e['total_value']:,} · "
              f"Rs {e['velocity']:,.0f}/day · max rest {e['max_resting_days']}d")
        print("          stages: " + ", ".join(f"{k} Rs {v:,}" for k, v in e["stage_breakdown"].items()))

    sep("SUSPICION PROPAGATION - new leads (never flagged by any rule)")
    for c in state["propagation"]["contamination"][:12]:
        print(f"{c['account']:<8} exposure {c['score']:.3f}  {c['hops']} hop(s) from "
              f"{c['nearest_alert']:<8} via {' -> '.join(c['path'])}")

    rp = None
    if not args.no_replay:
        sep("STREAMING REPLAY - detection as the money moves")
        rp = E.replay(df, truth, cfg, meta.get("rings", {}))
        rs = rp["summary"]
        print(f"First alert possible on day {rs['first_alert_day']}")
        print(f"Detection lag: mean {rs['mean_latency_days']} d, median {rs['median_latency_days']} d, "
              f"worst {rs['worst_latency_days']} d")
        print(f"{rs['detected']} accounts eventually detected, {rs['never_detected']} never surfaced")
        scored = [f for f in rp["frames"] if f["precision"] is not None and f["flagged"]]
        if scored:
            worst = min(scored, key=lambda f: f["precision"])
            done = next((f for f in scored if f["precision"] >= 0.999), None)
            print(f"\nStreaming precision bottoms at {worst['precision']:.2f} on day {worst['day']} "
                  f"and reaches the batch figure on day {done['day'] if done else 'never'}.")
            print("The look-alike discriminators need history: a settlement rail is indistinguishable")
            print("from a mule until you have watched it settle several times. Batch scoring hides this.")

    # ------------------------------------------------------------ outputs
    sep("WRITING OUTPUTS")
    out = args.out

    write_csv(df, os.path.join(out, "ledger.csv"))

    alerts_df = pd.DataFrame([{
        "account": a["account"], "risk_score": a["risk_score"], "risk_tier": a["risk_tier"],
        "typology": a["typology"], "ring": a["ring"] or "", "sla": a["sla"],
        "layers_triggered": " | ".join(a["layers"]),
        "total_inflow_inr": a["inflow"], "total_outflow_inr": a["outflow"],
        "exposure_inr": a["exposure"], "txn_count": a["txn_count"],
        "betweenness": a["centrality"], "ml_anomaly_score": a["ml_score"],
        "recommended_action": a["action"],
        "grounds_of_suspicion": " || ".join(f"[{r['layer']}] {r['detail']}" for r in a["reasons"]),
    } for a in state["alerts"]])
    write_csv(alerts_df, os.path.join(out, "alerts.csv"))

    timeline_df = pd.DataFrame(state["timeline"]["events"])
    write_csv(timeline_df, os.path.join(out, "timeline.csv"))

    watch_df = pd.DataFrame([{**c, "path": " -> ".join(c["path"])}
                             for c in state["propagation"]["contamination"]])
    if not watch_df.empty:
        write_csv(watch_df, os.path.join(out, "watchlist.csv"))

    rings_df = pd.DataFrame([{"ring_id": r["ring_id"], "typology": r["typology"], "size": r["size"],
                              "peak_risk": r["peak_risk"], "internal_value_inr": r["internal_value"],
                              "layers": " | ".join(r["layers"]), "members": " ".join(r["members"])}
                             for r in state["rings"]])
    write_csv(rings_df, os.path.join(out, "rings.csv"))

    ablation_df = pd.DataFrame(state["ablation"])
    if not ablation_df.empty:
        write_csv(ablation_df, os.path.join(out, "ablation.csv"))

    if state.get("discriminator_ablation"):
        write_csv(pd.DataFrame(state["discriminator_ablation"]),
                  os.path.join(out, "discriminator_ablation.csv"))
    if dc.get("present"):
        write_csv(pd.DataFrame([{k: (" ".join(v) if isinstance(v, list) else v)
                                 for k, v in f.items() if k != "layers_tripped"}
                                for f in dc["families"]]),
                  os.path.join(out, "hard_negatives.csv"))
    if rp:
        write_csv(pd.DataFrame(
            [{**{k: v for k, v in f.items() if k not in ("tiers", "accounts", "new_alerts")},
              **{"critical": f["tiers"]["CRITICAL"], "high": f["tiers"]["HIGH"],
                 "medium": f["tiers"]["MEDIUM"], "new_alerts": " ".join(f["new_alerts"])}}
             for f in rp["frames"]]), os.path.join(out, "replay.csv"))
        if rp["latency"]:
            write_csv(pd.DataFrame(rp["latency"]), os.path.join(out, "detection_latency.csv"))

    with open(os.path.join(out, "metrics_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated": str(date.today()), "dataset": d, "summary": s, "metrics": m,
                   "ablation": state["ablation"], "robustness": state["robustness"],
                   "hard_negatives": state.get("decoys"),
                   "discriminator_ablation": state.get("discriminator_ablation"),
                   "economics": state.get("economics"),
                   "evasion_benchmark": evb,
                   "replay_summary": (rp or {}).get("summary"),
                   "config": state["config"],
                   "key_finding": ("Unsupervised anomaly detection alone is a weak AML control. "
                                   "Relational structure carries the signal; ML corroborates.")},
                  fh, indent=2)

    # STR dossiers for everything CRITICAL/HIGH
    str_dir = os.path.join(out, "STR")
    os.makedirs(str_dir, exist_ok=True)
    result = {"flags": {a["account"]: a["risk_score"] for a in state["alerts"]},
              "reasons": {a["account"]: a["reasons"] for a in state["alerts"]},
              "layers": {a["account"]: a["layers"] for a in state["alerts"]},
              "centrality": {a["account"]: a["centrality"] for a in state["alerts"]},
              "ml_scores": {a["account"]: a["ml_score"] for a in state["alerts"]
                            if a["ml_score"] is not None}}
    fm = E.build_features(df, state["accounts"])
    str_rows, n_str, used_names = [], 0, set()
    for a in state["alerts"]:
        if a["risk_tier"] not in ("CRITICAL", "HIGH"):
            continue
        doc = E.generate_dossier(a["account"], df, G, result, state["rings"], state["accounts"], fm)
        fname = "STR_" + safe_stem(a["account"], used_names) + ".json"
        with open(os.path.join(str_dir, fname), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        ts = doc["transaction_summary"]
        str_rows.append({"str_reference": doc["report_reference"], "date_of_report": doc["date_of_report"],
                         "account_id": doc["account_id"], "risk_tier": doc["risk_tier"],
                         "risk_score": doc["risk_score"], "typology": a["typology"],
                         "ring": a["ring"] or "", "total_inflow_inr": ts["total_inflow"],
                         "total_outflow_inr": ts["total_outflow"],
                         "transaction_count": ts["transaction_count"],
                         "counterparties": ts["counterparties"],
                         "credits_just_below_ctr": ts["credits_just_below_ctr"],
                         "grounds_of_suspicion": " || ".join(g["detail"] for g in doc["grounds_of_suspicion"]),
                         "narrative": doc["narrative"], "action_taken": doc["action_taken"],
                         "regulatory_basis": doc["regulatory_basis"]})
        n_str += 1
    str_df = pd.DataFrame(str_rows)
    if not str_df.empty:
        write_csv(str_df, os.path.join(out, "FIU-IND_STR_bundle.csv"))

    for f in ["ledger.csv", "alerts.csv", "timeline.csv", "watchlist.csv", "rings.csv",
              "ablation.csv", "discriminator_ablation.csv", "hard_negatives.csv",
              "replay.csv", "detection_latency.csv",
              "metrics_summary.json", "FIU-IND_STR_bundle.csv"]:
        p = os.path.join(out, f)
        if os.path.exists(p):
            print(f"  {p}")
    print(f"  {str_dir}/  ({n_str} STR dossiers)")

    # optional Excel workbook. openpyxl reads a string that starts with "="
    # back as a formula, so the workbook needs the same guard as the CSVs -
    # more so, since this is the file a reviewer is most likely to open.
    try:
        xl = os.path.join(out, "AMLTrace_report.xlsx")
        sheets = [("Alerts", alerts_df), ("STR bundle", str_df), ("Timeline", timeline_df),
                  ("Rings", rings_df), ("Watchlist", watch_df), ("Ablation", ablation_df),
                  ("Ledger", df)]
        with pd.ExcelWriter(xl, engine="openpyxl") as w:
            for name, frame in sheets:
                if not frame.empty:
                    E.csv_safe_frame(frame).to_excel(w, sheet_name=name, index=False)
            # Every other artefact in this directory is reproducible byte for
            # byte, and the workbook was the one exception: openpyxl stamps the
            # current time into docProps, so two runs over identical data
            # produced different files. Pinning it to the ledger's own epoch
            # keeps the whole output set diffable.
            props = w.book.properties
            props.created = props.modified = datetime(E.EPOCH.year, E.EPOCH.month, E.EPOCH.day)
            props.creator = props.lastModifiedBy = "AMLTrace"
        normalise_xlsx(xl)
        print(f"  {xl}")
    except ImportError:
        print("  (skipped Excel workbook - pip install openpyxl to enable)")

    # optional static network PNG
    try:
        png = draw_png(G, state, os.path.join(out, "network_graph.png"))
        print(f"  {png}")
    except ImportError:
        print("  (skipped network PNG - pip install matplotlib to enable)")

    print(f"\nDone. Open the interactive console with:  python app.py")
    return 0


def draw_png(G, state, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    tier_colour = {"CRITICAL": "#c9184a", "HIGH": "#e8590c", "MEDIUM": "#f2b705", "LOW": "#adb5bd"}
    risk = {a["account"]: a["risk_score"] for a in state["alerts"]}
    tier = {a["account"]: a["risk_tier"] for a in state["alerts"]}
    ring_of = {m: r["ring_id"] for r in state["rings"] for m in r["members"]}

    keep = set(risk)
    for n in list(risk):
        keep |= set(G.predecessors(n)) | set(G.successors(n))
    # Fixed order. A subgraph view drawn from a small node set iterates that set,
    # and set order varies between processes, which reshuffles the node sizes,
    # the colours and the draw order and gives a different image each run.
    order = sorted(keep)
    H = G.subgraph(order)

    # Deterministic layout: one cluster per detected ring, arranged on a circle,
    # with each ring's ordinary counterparties parked just outside it. A force
    # layout is the wrong tool here - we already know the community structure,
    # so we draw it rather than rediscovering it.
    import numpy as np

    rings = sorted({ring_of[n] for n in order if n in ring_of})
    n_rings = max(1, len(rings))
    R = 4.4
    anchors, pos = {}, {}
    for i, rid in enumerate(rings):
        ang = 2 * np.pi * i / n_rings - np.pi / 2
        anchors[rid] = np.array([np.cos(ang) * R, np.sin(ang) * R])
        members = [n for n in order if ring_of.get(n) == rid]
        rad = 0.36 + 0.10 * len(members)
        for j, n in enumerate(members):
            a2 = 2 * np.pi * j / len(members)
            pos[n] = anchors[rid] + rad * np.array([np.cos(a2), np.sin(a2)])

    # counterparties that no rule flagged: park them outside their ring
    outside = [n for n in order if n not in pos]
    for n in outside:
        nb = sorted(m for m in set(H.predecessors(n)) | set(H.successors(n)) if m in ring_of)
        if nb:
            rid = ring_of[nb[0]]
            base = anchors[rid]
            k = sum(ord(c) for c in n)
            ang = (k % 360) * np.pi / 180
            out = base * 1.30 + 0.95 * np.array([np.cos(ang), np.sin(ang)])
        else:
            k = sum(ord(c) for c in n)
            ang = (k % 360) * np.pi / 180
            out = (R + 2.6) * np.array([np.cos(ang), np.sin(ang)])
        pos[n] = out

    vol = {n: sum(d["amount"] for _, _, d in H.in_edges(n, data=True)) +
              sum(d["amount"] for _, _, d in H.out_edges(n, data=True)) for n in order}
    mx = max(vol.values()) or 1
    sizes = [140 + 900 * (vol[n] / mx) ** .5 for n in order]
    colours = [tier_colour.get(tier.get(n), "#33405c") for n in order]

    fig, ax = plt.subplots(figsize=(22, 15), facecolor="#0b0f16")
    ax.set_facecolor("#0b0f16")
    edges = sorted(H.edges())
    illicit = [(u, v) for u, v in edges if u in risk and v in risk]
    other = [(u, v) for u, v in edges if not (u in risk and v in risk)]
    nx.draw_networkx_edges(H, pos, edgelist=other, ax=ax, edge_color="#3a4a63", width=.7,
                           alpha=.5, arrowstyle="-|>", arrowsize=9,
                           connectionstyle="arc3,rad=0.09")
    nx.draw_networkx_edges(H, pos, edgelist=illicit, ax=ax, edge_color="#ff7a5c", width=1.5,
                           alpha=.85, arrowstyle="-|>", arrowsize=15,
                           connectionstyle="arc3,rad=0.09")
    nx.draw_networkx_nodes(H, pos, ax=ax, nodelist=order, node_color=colours, node_size=sizes,
                           edgecolors="#0b0f16", linewidths=1.1)
    nx.draw_networkx_labels(H, pos, ax=ax, font_size=7.5, font_color="#e6edf7",
                            labels={n: n for n in order if n in risk})
    handles = [plt.Line2D([], [], marker="o", ls="", markersize=11, color=c, label=t)
               for t, c in tier_colour.items()]
    handles.append(plt.Line2D([], [], marker="o", ls="", markersize=9, color="#33405c",
                              label="Not alerted"))
    leg = ax.legend(handles=handles, loc="upper left", title="Risk tier", facecolor="#141c2b",
                    edgecolor="#243149", labelcolor="#e6edf7", fontsize=11)
    leg.get_title().set_color("#8ea0bd")
    s = state["summary"]
    ax.set_title(f"AMLTrace - laundering network\n{state['dataset']['accounts']} accounts, "
                 f"{state['dataset']['transactions']} transactions · {s['flagged']} alerted "
                 f"across {s['rings']} rings",
                 color="#e6edf7", fontsize=17, fontweight="bold", pad=18)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=180, facecolor="#0b0f16", bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    sys.exit(main())
