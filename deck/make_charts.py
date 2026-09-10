"""Render every chart the pitch deck uses, straight from the live engine.

No numbers are typed by hand anywhere in the deck build: this script runs the
real pipeline and writes both the PNGs and a facts.json that the deck generator
reads. If the engine changes, the deck changes with it.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import aml_engine as E

OUT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)

BG      = "#080e1c"
PANEL   = "#0e1729"
INK     = "#e8eefb"
MUTED   = "#93a6c8"
FAINT   = "#6a7fa6"
CYAN    = "#38e0d8"
BLUE    = "#4d7cff"
VIOLET  = "#8b5cf6"
RED     = "#ff4d6d"
ORANGE  = "#ff9f43"
YELLOW  = "#ffd93d"
GREEN   = "#2ecc8f"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.edgecolor": "#25324d", "grid.color": "#1b2740",
    "font.family": "sans-serif", "font.sans-serif": ["Calibri", "Arial", "DejaVu Sans"],
    "font.size": 12, "axes.titlesize": 14, "axes.titleweight": "bold",
})


def frame(ax, grid_axis="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#25324d")
    ax.grid(True, axis=grid_axis, lw=.8, alpha=.55)
    ax.set_axisbelow(True)


def save(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p, dpi=200, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print("  ", name)
    return p


print("Running the pipeline...")
df, truth, meta = E.generate_dataset()
st = E.analyse(df, truth, E.Config(), meta)
rp = E.replay(df, truth, E.Config(), meta.get("rings", {}))
print("Charts:")

# ---------------------------------------------------------------- 1. ablation
ab = st["ablation"]
labels = [r["name"].replace("+ ", "").replace(" (all rules)", "").replace(", no graph context (baseline)", "")
          for r in ab]
rec = [r["recall"] for r in ab]
prec = [r["precision"] for r in ab]
fig, ax = plt.subplots(figsize=(9.2, 4.4))
y = np.arange(len(labels))[::-1]
cols = [CYAN] * (len(ab) - 1) + [RED]
ax.barh(y, rec, height=.52, color=cols, zorder=3)
for i, (yy, r, p) in enumerate(zip(y, rec, prec)):
    ax.text(r + .015, yy, f"recall {r:.2f}   precision {p:.2f}",
            va="center", ha="left", fontsize=11,
            color=RED if i == len(ab) - 1 else INK,
            fontweight="bold" if i == len(ab) - 1 else "normal")
ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=11.5)
ax.set_xlim(0, 1.42); ax.set_xticks([0, .25, .5, .75, 1.0])
ax.set_xlabel("Recall")
frame(ax, "x")
save(fig, "chart_ablation.png")

# ------------------------------------------------- 2. hard negatives headline
da = st["discriminator_ablation"]
base, off = da[0], da[-1]
fig, ax = plt.subplots(figsize=(9.2, 4.0))
# Both metrics are framed so TALLER IS BETTER, and neither good value is zero:
# a zero-height bar renders as nothing and reads as a broken chart.
groups = ["Precision", "Lawful businesses" + chr(10) + "correctly ignored"]
xs = np.arange(2)
w = .34
n_dec = base["decoys_total"]
full = [base["precision"], (n_dec - base["decoys_caught"]) / n_dec]
shape = [off["precision"], (n_dec - off["decoys_caught"]) / n_dec]
lab_full = [f"{base['precision']:.2f}", f"{n_dec - base['decoys_caught']}/{n_dec}"]
lab_shape = [f"{off['precision']:.2f}", f"{n_dec - off['decoys_caught']}/{n_dec}"]

b1 = ax.bar(xs - w / 2, full, w, color=GREEN, zorder=3, label="AMLTrace (with economic tests)")
b2 = ax.bar(xs + w / 2, shape, w, color=RED, zorder=3, label="Topology matching only")
for rect, v, lab in zip(b1, full, lab_full):
    ax.text(rect.get_x() + rect.get_width() / 2, v + .03, lab, ha="center", fontsize=15,
            fontweight="bold", color=GREEN)
for rect, v, lab in zip(b2, shape, lab_shape):
    ax.text(rect.get_x() + rect.get_width() / 2, v + .03, lab, ha="center", fontsize=15,
            fontweight="bold", color=RED)
ax.set_xticks(xs); ax.set_xticklabels(groups, fontsize=13)
ax.set_ylim(0, 1.18); ax.set_yticks([0, .25, .5, .75, 1.0])
leg = ax.legend(frameon=False, fontsize=11.5, loc="upper center", ncol=2, bbox_to_anchor=(.5, 1.16))
for t in leg.get_texts():
    t.set_color(MUTED)
frame(ax, "y")
save(fig, "chart_hardneg.png")

# ------------------------------------------- 3. discriminator ablation detail
rows = da[1:-1]
names = [r["name"].replace("Without: ", "").replace(" (L1)", "").replace(" (L2)", "")
         .replace(" (L3)", "").replace(" (L4)", "") for r in rows]
fig, ax = plt.subplots(figsize=(9.2, 4.2))
y = np.arange(len(rows))[::-1]
caught = [r["decoys_caught"] for r in rows]
ax.barh(y, caught, height=.5, color=ORANGE, zorder=3)
ax.axvline(0, color="#25324d", lw=1)
for yy, r, c in zip(y, rows, caught):
    ax.text(c + .18, yy, f"{c} look-alikes flagged    precision {r['precision']:.2f}",
            va="center", fontsize=11, color=INK)
ax.set_yticks(y)
ax.set_yticklabels(["Without " + n.lower() for n in names], fontsize=11.5)
ax.set_xlim(0, max(caught) * 2.55)
ax.set_xlabel("Legitimate accounts wrongly alerted (of 19)")
frame(ax, "x")
save(fig, "chart_discriminators.png")

# ----------------------------------------------------- 4. streaming precision
F = [f for f in rp["frames"] if f["precision"] is not None and f["flagged"]]
days = [f["day"] for f in F]
pr = [f["precision"] for f in F]
rc = [f["recall"] for f in F]
fig, ax = plt.subplots(figsize=(9.4, 4.3))
ax.plot(days, pr, color=CYAN, lw=2.8, marker="o", ms=4, label="Precision", zorder=4)
ax.plot(days, rc, color=GREEN, lw=2.2, marker="o", ms=3.4, label="Recall", zorder=4, alpha=.9)
ax.axhline(1.0, color=MUTED, ls="--", lw=1.1, alpha=.6)
worst = min(F, key=lambda f: f["precision"])
perfect = next((f for f in F if f["precision"] >= .999), F[-1])
ax.annotate(f"day {worst['day']}: {worst['precision']:.2f}",
            xy=(worst["day"], worst["precision"]), xytext=(worst["day"] + 2.6, worst["precision"] - .17),
            color=RED, fontsize=12, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=RED, lw=1.6))
ax.annotate("batch figure reached" + chr(10) + "on day %d" % perfect["day"],
            xy=(perfect["day"] + .4, 1.0), xytext=(perfect["day"] + 2.2, .68),
            color=CYAN, fontsize=11.5, ha="left",
            arrowprops=dict(arrowstyle="->", color=CYAN, lw=1.5,
                            connectionstyle="arc3,rad=-0.25"))
ax.set_xlabel("Day of the observation window"); ax.set_ylabel("Score")
ax.set_ylim(0, 1.1)
leg = ax.legend(frameon=False, fontsize=12, loc="lower right")
for t in leg.get_texts():
    t.set_color(MUTED)
frame(ax, "y")
save(fig, "chart_streaming.png")

# -------------------------------------------------------------- 5. latency
lat = rp["latency"]
buckets = {}
for l in lat:
    key = l["typology"].split("-RING")[0].replace("-CHAIN", "").title()
    buckets.setdefault(key, []).append(l["latency_days"])
names = sorted(buckets, key=lambda k: -np.mean(buckets[k]))
means = [np.mean(buckets[n]) for n in names]
worst = [max(buckets[n]) for n in names]
fig, ax = plt.subplots(figsize=(9.2, 3.9))
x = np.arange(len(names))
ax.bar(x - .19, means, .36, color=BLUE, zorder=3, label="Mean lag")
ax.bar(x + .19, worst, .36, color=VIOLET, zorder=3, label="Worst case")
for i, (m, w2) in enumerate(zip(means, worst)):
    ax.text(i - .19, m + .25, f"{m:.1f}", ha="center", fontsize=11, color=INK)
    ax.text(i + .19, w2 + .25, f"{w2:.0f}", ha="center", fontsize=11, color=INK)
ax.set_xticks(x); ax.set_xticklabels(names, fontsize=12)
ax.set_ylabel("Days until detectable")
ax.set_ylim(0, max(worst) * 1.25)
leg = ax.legend(frameon=False, fontsize=11.5, loc="upper right")
for t in leg.get_texts():
    t.set_color(MUTED)
frame(ax, "y")
save(fig, "chart_latency.png")

# ---------------------------------------------------------- 6. cost frontier
pts = []
for fb in (2, 3, 4, 5):
    cfg = E.Config.from_dict({**E.Config().to_dict(), "fan_min_branches": fb})
    G = E.build_graph(df)
    accs = sorted(G.nodes())
    res = E.run_pipeline(df, G, accs, cfg)
    m = E.evaluate(res["flags"], accs, truth)
    tiers = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for a, sc in res["flags"].items():
        tiers[E.risk_tier(sc)[0]] += 1
    summ = {"flagged": len(res["flags"]), "tiers": tiers,
            "exposure": st["summary"]["exposure"], "illicit_flow": st["summary"]["illicit_flow"]}
    ec = E.alert_economics([], summ, {"labelled": False}, st["dataset"])
    pts.append((fb, m["recall"], ec["annual_review_cost"], ec["analyst_fte"]))

fig, ax = plt.subplots(figsize=(9.2, 4.1))
fbs = [p[0] for p in pts]
recs = [p[1] for p in pts]
costs = [p[2] / 1e5 for p in pts]
ax.bar([str(f) for f in fbs], costs, .46, color=BLUE, zorder=3)
for i, (c, p) in enumerate(zip(costs, pts)):
    ax.text(i, c + .12, f"Rs {c:.2f}L", ha="center", fontsize=11.5, color=INK, fontweight="bold")
ax.set_ylabel("Annual review cost (Rs lakh)", color=BLUE)
ax.set_xlabel("Fan-branch threshold")
ax.set_ylim(0, max(costs) * 1.35)
frame(ax, "y")
ax2 = ax.twinx()
ax2.plot([str(f) for f in fbs], recs, color=ORANGE, lw=3, marker="o", ms=9, zorder=5)
for i, r in enumerate(recs):
    ax2.text(i, r + .055, f"recall {r:.2f}", ha="center", fontsize=11.5, color=ORANGE, fontweight="bold")
ax2.set_ylabel("Recall", color=ORANGE)
ax2.set_ylim(0, 1.25)
ax2.tick_params(colors=ORANGE)
for s in ("top", "left"):
    ax2.spines[s].set_visible(False)
ax2.spines["right"].set_color("#25324d")
save(fig, "chart_frontier.png")

# ------------------------------------------------------------- 7. tier donut
t = st["summary"]["tiers"]
keys = [k for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW") if t[k]]
vals = [t[k] for k in keys]
cmap = {"CRITICAL": RED, "HIGH": ORANGE, "MEDIUM": YELLOW, "LOW": FAINT}
fig, ax = plt.subplots(figsize=(4.6, 4.6))
w, _, at = ax.pie(vals, colors=[cmap[k] for k in keys], startangle=90,
                  wedgeprops=dict(width=.36, edgecolor=BG, linewidth=3),
                  autopct=lambda p: f"{int(round(p * sum(vals) / 100))}",
                  pctdistance=.82, textprops=dict(color=BG, fontsize=13, fontweight="bold"))
ax.text(0, .06, str(sum(vals)), ha="center", va="center", fontsize=34,
        color=INK, fontweight="bold")
ax.text(0, -.20, "ALERTS", ha="center", va="center", fontsize=11, color=FAINT)
ax.legend(keys, frameon=False, fontsize=12, loc="lower center", ncol=3,
          bbox_to_anchor=(.5, -.10), labelcolor=MUTED)
save(fig, "chart_tiers.png")

# ------------------------------------------------- 8. seed stability (honest)
SEEDS = (7, 13, 21, 42, 99, 101, 777, 2026, 31337, 5)
seed_rows = []
for sd in SEEDS:
    d2, t2, m2 = E.generate_dataset(seed=sd)
    s2 = E.analyse(d2, t2, E.Config(), m2, do_ablation=False)
    mm = s2["metrics"]
    seed_rows.append((sd, mm["precision"], mm["recall"], mm["fp"]))

fig, ax = plt.subplots(figsize=(9.4, 4.0))
xs = np.arange(len(SEEDS))
pv = [r[1] for r in seed_rows]
rv = [r[2] for r in seed_rows]
ax.bar(xs - .19, pv, .36, color=[GREEN if v == 1 else ORANGE for v in pv], zorder=3, label="Precision")
ax.bar(xs + .19, rv, .36, color=BLUE, zorder=3, label="Recall")
for i, v in enumerate(pv):
    ax.text(i - .19, v + .022, f"{v:.2f}", ha="center", fontsize=10,
            color=GREEN if v == 1 else ORANGE, fontweight="bold")
ax.axhline(float(np.mean(pv)), color=ORANGE, ls="--", lw=1.4, alpha=.75)
ax.text(len(SEEDS) - .4, float(np.mean(pv)) - .075, f"mean precision {np.mean(pv):.3f}",
        color=ORANGE, fontsize=11.5, ha="right", fontweight="bold")
ax.set_xticks(xs); ax.set_xticklabels([str(x) for x in SEEDS], fontsize=11)
ax.set_xlabel("Random seed - a different ledger each time")
ax.set_ylim(0, 1.15); ax.set_yticks([0, .25, .5, .75, 1.0])
leg = ax.legend(frameon=False, fontsize=11.5, loc="lower right", ncol=2)
for t in leg.get_texts():
    t.set_color(MUTED)
frame(ax, "y")
save(fig, "chart_seeds.png")

# --------------------------------------------- 9. adaptive adversary (honest)
# Each probe selects the accounts it is meant to bend by their ROLE, not by the
# shape of their identifier. The earlier version matched on names like "S1a",
# "M1" and anything starting with "C". Once accounts were given bank-coded
# identifiers those patterns stopped matching, so every probe quietly mutated
# nothing and the chart reported perfect recall against attacks it had never
# actually applied. A probe that cannot fail is worse than no probe at all.
def _probe(mutate):
    d3, t3, m3 = E.generate_dataset()
    roles = m3.get("roles", {})
    d3 = mutate(d3.copy(), roles)
    return E.analyse(d3, t3, E.Config(), m3, do_ablation=False)["metrics"]


def _by_role(d, roles, *wanted):
    mask = d["sender"].map(lambda a: roles.get(a) in wanted)
    return mask.fillna(False).astype(bool)

def _spread(d, roles):
    m = _by_role(d, roles, "structuring_source")
    d.loc[m, "day"] = d.loc[m, "day"] + np.arange(int(m.sum())) % 12
    return d

def _slow(d, roles):
    m = _by_role(d, roles, "cycle_member")
    d.loc[m, "day"] = d.loc[m, "day"] * 3
    return d

def _patient(d, roles):
    m = _by_role(d, roles, "mule")
    d.loc[m, "day"] = d.loc[m, "day"] + 4
    return d

def _deeper(d, roles):
    m = _by_role(d, roles, "structuring_source")
    d.loc[m, "amount"] = 700_000
    return d

probes = [
    ("Textbook criminals\n(the bundled ledger)", lambda d, roles: d),
    ("Mules hold funds\n4 days, not 1", _patient),
    ("Deposits at 70% of CTR,\nnot 95%", _deeper),
    ("Structuring spread\nover 12 days", _spread),
    ("Cycles closing\n3x slower", _slow),
]
prow = [(lab, _probe(fn)["recall"]) for lab, fn in probes]

fig, ax = plt.subplots(figsize=(9.4, 4.2))
labs = [p[0] for p in prow]
vals = [p[1] for p in prow]
cols = [GREEN] + [ORANGE if v > .8 else RED for v in vals[1:]]
bars = ax.bar(np.arange(len(labs)), vals, .55, color=cols, zorder=3)
for b, v in zip(bars, vals):
    ax.text(b.get_x() + b.get_width() / 2, v + .028, f"{v:.2f}", ha="center",
            fontsize=14, fontweight="bold", color=b.get_facecolor())
ax.set_xticks(np.arange(len(labs))); ax.set_xticklabels(labs, fontsize=10.5)
ax.set_ylabel("Recall")
ax.set_ylim(0, 1.18); ax.set_yticks([0, .25, .5, .75, 1.0])
frame(ax, "y")
save(fig, "chart_adversary.png")

# ------------------------------------------- 10. evasion benchmark (the fix)
# The adversary chart above bends one assumption at a time. This one is the
# whole point of L9: two ledgers built to defeat layers 1 to 8 outright, each
# scored twice. The left bar of every pair is what a published rule set is worth
# once the rules are public.
print("Running the adversarial benchmark...")
evb = E.evasion_benchmark()
fig, ax = plt.subplots(figsize=(9.4, 4.3))
x = np.arange(len(evb))
w = 0.34
off = [r["recall_without_adaptive"] for r in evb]
on = [r["recall_with_adaptive"] for r in evb]
# A recall of exactly zero draws no bar at all, which reads from the back of a
# room as "no result" rather than "caught nothing". The stub is cosmetic; the
# printed figure above each bar is the real number.
STUB = 0.012
b1 = ax.bar(x - w / 2, [max(v, STUB) for v in off], w, color=RED, zorder=3,
            label="Layers 1-8 only")
b2 = ax.bar(x + w / 2, [max(v, STUB) for v in on], w, color=GREEN, zorder=3,
            label="With L9 adaptive layer")
for bars, vals in ((b1, off), (b2, on)):
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, max(v, STUB) + .03, f"{v:.2f}", ha="center",
                fontsize=13, fontweight="bold", color=b.get_facecolor())
ax.set_xticks(x)
ax.set_xticklabels([r["scenario"].replace(" (", chr(10) + "(") for r in evb], fontsize=10.5)
ax.set_ylabel("Recall")
ax.set_ylim(0, 1.2); ax.set_yticks([0, .25, .5, .75, 1.0])
ax.legend(frameon=False, loc="upper center", ncol=2, fontsize=10.5,
          bbox_to_anchor=(0.5, 1.16), labelcolor=MUTED)
frame(ax, "y")
save(fig, "chart_evasion.png")

# ------------------------------------------------------------------- facts
worstf = min(F, key=lambda f: f["precision"])
facts = {
    "dataset": st["dataset"], "summary": st["summary"], "metrics": st["metrics"],
    "decoys": st["decoys"], "discriminator_ablation": da, "ablation": ab,
    "robustness": st["robustness"], "economics": st["economics"],
    "replay": rp["summary"],
    "streaming": {"worst_precision": worstf["precision"], "worst_day": worstf["day"],
                  "converged_day": perfect["day"]},
    "rings": [{"ring_id": r["ring_id"], "typology": r["typology"], "size": r["size"],
               "internal_value": r["internal_value"], "layers": r["layers"]} for r in st["rings"]],
    "frontier": [{"fan_min_branches": p[0], "recall": p[1], "cost": p[2], "fte": p[3]} for p in pts],
    "seeds": [{"seed": r[0], "precision": r[1], "recall": r[2], "fp": r[3]} for r in seed_rows],
    "seed_summary": {"n": len(SEEDS), "mean_precision": float(np.mean(pv)),
                     "min_precision": float(np.min(pv)), "mean_recall": float(np.mean(rv)),
                     "min_recall": float(np.min(rv))},
    "adversary": [{"scenario": lab.replace(chr(10), " "), "recall": v} for lab, v in prow],
    "evasion": evb,
    "provenance": st.get("provenance"),
    "channel_mix": st.get("channel_mix"),
}
with open(os.path.join(OUT, "facts.json"), "w", encoding="utf-8") as fh:
    json.dump(facts, fh, indent=2)
print("   facts.json")
print("Done.")
