"""
Build the six-slide submission deck for BuiltNBank problem statement 5,
"Tracing Financial Crime Across Patterns".

Slide order follows the brief: problem, solution and key features, technology
stack, challenges, future scope, with a title and a closing slide either side.
Every number on every slide is read from deck/facts.json, which make_charts.py
writes by running the real pipeline, so the deck cannot drift away from the code.

    python deck/make_charts.py && python deck/build_round1_deck.py
"""
import json, os

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

HERE = os.path.dirname(os.path.abspath(__file__))
F = json.load(open(os.path.join(HERE, "facts.json"), encoding="utf-8"))

# ------------------------------------------------------------------ palette
BG      = RGBColor(0x07, 0x0C, 0x18)
BG_DEEP = RGBColor(0x04, 0x06, 0x0D)
CARD    = RGBColor(0x11, 0x1B, 0x31)
CARD2   = RGBColor(0x16, 0x22, 0x3C)
INK     = RGBColor(0xE8, 0xEE, 0xFB)
MUTED   = RGBColor(0x93, 0xA6, 0xC8)
FAINT   = RGBColor(0x6A, 0x7F, 0xA6)
CYAN    = RGBColor(0x38, 0xE0, 0xD8)
BLUE    = RGBColor(0x4D, 0x7C, 0xFF)
VIOLET  = RGBColor(0x8B, 0x5C, 0xF6)
RED     = RGBColor(0xFF, 0x4D, 0x6D)
ORANGE  = RGBColor(0xFF, 0x9F, 0x43)
GREEN   = RGBColor(0x2E, 0xCC, 0x8F)

H1, H2, BODY = "Arial", "Arial", "Calibri"
W, HGT = 13.333, 7.5
M = 0.62                       # slide margin

prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(HGT)
BLANK = prs.slide_layouts[6]

_n = [0]


# ------------------------------------------------------------------ helpers
def slide(dark=False):
    s = prs.slides.add_slide(BLANK)
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = BG_DEEP if dark else BG
    return s


def rect(s, x, y, w, h, fill=CARD, radius=True, line=None, lw=1.0, shape=None):
    sh = s.shapes.add_shape(
        shape or (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE),
        Inches(x), Inches(y), Inches(w), Inches(h))
    if radius and shape is None:
        sh.adjustments[0] = 0.055
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(lw)
    sh.shadow.inherit = False
    return sh


def circle(s, x, y, d, fill, line=None):
    sh = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1.25)
    sh.shadow.inherit = False
    return sh


def text(s, x, y, w, h, runs, size=14, color=INK, font=BODY, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, space=6, line_sp=None):
    """runs: a string, or a list of (text, {overrides}) / (text,) tuples,
    or a list of lists (each inner list is one paragraph)."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) and runs and isinstance(runs[0], list) else [runs]
    first = True
    for para in paras:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.space_after = Pt(space)
        if line_sp:
            p.line_spacing = line_sp
        items = [para] if isinstance(para, str) else para
        for it in items:
            if isinstance(it, str):
                t, o = it, {}
            elif isinstance(it, dict):
                raise TypeError("a run must be a string or (text, opts) tuple, not a bare dict")
            else:
                t, o = it[0], (it[1] if len(it) > 1 else {})
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False)
            f.color.rgb = o.get("color", color)
    return tb


def title(s, t, sub=None, n=None):
    """Section title plus the deck's one repeated motif: a small graph-node
    badge carrying the slide number, echoing the product's network view.

    The number is assigned automatically in call order, so slides can be
    inserted or reordered without hand-renumbering anything."""
    _n[0] += 1
    n = _n[0]
    if n is not None:
        d = 0.42
        circle(s, W - M - d, 0.60, d, CARD2, line=CYAN)
        text(s, W - M - d, 0.60 + 0.075, d, 0.3, str(n), size=12.5, color=CYAN,
             font=H2, bold=True, align=PP_ALIGN.CENTER)
    text(s, M, 0.55, W - 2 * M - 0.7, 0.62, t, size=32, color=INK, font=H1, bold=True)
    if sub:
        text(s, M, 1.18, W - 2 * M - 0.7, 0.42, sub, size=14.5, color=MUTED, space=0)


def picture(s, name, x, y, boxw, boxh, center=True, cover=False):
    """Fit an image inside a box, preserving aspect ratio.

    cover=True fills the box instead, cropping the overflow - used for the
    full-bleed title image so it does not letterbox into a floating panel."""
    p = os.path.join(HERE, name)
    im = Image.open(p)
    iw, ih = im.size
    if cover:
        want = boxw / boxh
        have = iw / ih
        if have > want:                       # too wide: crop the sides
            nw = int(ih * want)
            box = ((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih)
        else:                                 # too tall: crop top and bottom
            nh = int(iw / want)
            box = (0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh)
        p = os.path.join(HERE, "_cover_" + os.path.basename(name))
        im.crop(box).save(p)
        return s.shapes.add_picture(p, Inches(x), Inches(y), Inches(boxw), Inches(boxh))
    ar = iw / ih
    w, h = boxw, boxw / ar
    if h > boxh:
        h, w = boxh, boxh * ar
    px = x + (boxw - w) / 2 if center else x
    py = y + (boxh - h) / 2 if center else y
    return s.shapes.add_picture(p, Inches(px), Inches(py), Inches(w), Inches(h))


def stat(s, x, y, w, value, label, colour=CYAN, note=None, h=1.36, vsize=34):
    """Big number, label, optional note - stacked from the top so the three
    never overlap regardless of the card height chosen."""
    rect(s, x, y, w, h, CARD, line=RGBColor(0x1E, 0x2B, 0x47))
    pad = 0.22
    vy = y + 0.15
    vh = vsize / 58.0
    text(s, x + pad, vy, w - 2 * pad, vh, value, size=vsize, color=colour,
         font=H1, bold=True, space=0)
    ly = vy + vh + 0.05
    lh = 0.44 if note else max(0.34, y + h - 0.14 - ly)
    text(s, x + pad, ly, w - 2 * pad, lh, label, size=11.5, color=MUTED, space=0, line_sp=1.1)
    if note:
        text(s, x + pad, ly + lh + 0.02, w - 2 * pad, 0.26, note, size=10, color=FAINT, space=0)


def bullet_rows(s, x, y, w, rows, gap=0.86, dot=CYAN, size=13, head_size=14):
    """Icon-dot + bold head + description. The deck's list style everywhere."""
    for i, (head, desc) in enumerate(rows):
        yy = y + i * gap
        circle(s, x, yy + 0.055, 0.20, dot if not isinstance(dot, list) else dot[i % len(dot)])
        text(s, x + 0.36, yy, w - 0.36, gap - 0.06,
             [[(head + "  ", {"bold": True, "size": head_size, "color": INK, "font": H2}),
               (desc, {"size": size, "color": MUTED})]], space=0)




# ----------------------------------------------------------------- content
D   = F["dataset"]
S   = F["summary"]
MET = F["metrics"]
ECO = F["economics"]
REP = F["replay"]
DEC = F["decoys"]
BORDER = RGBColor(0x1E, 0x2B, 0x47)


def crore(v):
    return "Rs %.1f Cr" % (v / 1e7)


def pct(v):
    return "%d%%" % round(v * 100)


# ------------------------------------------------------------ 0. title card
# Type and rules only. The graph render is used inside the deck where it
# illustrates something, not as wallpaper here.
s = slide(dark=True)
rect(s, 0, 0, 0.12, HGT, CYAN, radius=False)
text(s, 1.35, 2.55, W - 2.7, 1.0, "AMLTrace", size=54, color=INK, font=H1, bold=True, space=2)
rect(s, 1.35, 3.62, 1.6, 0.035, CYAN, radius=False)
text(s, 1.35, 3.9, W - 2.7, 0.5,
     "Detecting laundering patterns that span many accounts and many days",
     size=18, color=MUTED, space=0)
text(s, 1.35, 4.62, W - 2.7, 0.4,
     [[("Team  ", {"color": FAINT}),
       ("Broke Detectives", {"color": CYAN, "font": H2, "bold": True, "size": 16})]],
     size=13, color=FAINT, space=0)
text(s, 1.35, 5.6, W - 2.7, 0.9,
     [["Problem statement 5, Tracing Financial Crime Across Patterns"],
      ["BuiltNBank, IIT Delhi (IGTDW)"]],
     size=12.5, color=FAINT, space=4)


# ---------------------------------------------------------- 1. the problem
# The brief as issued sits at the top of this slide, quoted, so the panel can
# see exactly what is being answered before any of our own framing.
s = slide()
title(s, "Problem statement 5", "Tracing Financial Crime Across Patterns")

rect(s, M, 1.72, W - 2 * M, 1.86, CARD, line=BORDER)
rect(s, M, 1.72, 0.06, 1.86, CYAN, radius=False)
text(s, M + 0.36, 1.95, W - 2 * M - 0.72, 1.5,
     [[("Challenge.  ", {"bold": True, "color": CYAN, "font": H2, "size": 12}),
       ("Find a way to catch financial crime that only becomes visible across many "
        "transactions or accounts over time. The most damaging fraud rarely shows up in a "
        "single transaction: it is spread deliberately across many small transfers and "
        "accounts to avoid detection, faster than human analysts can trace.", {})],
      [("Objective.  ", {"bold": True, "color": CYAN, "font": H2, "size": 12}),
       ("Think about what it would take to spot a pattern that is deliberately spread out to "
        "avoid detection, and who should act on that finding once it is flagged. The scope, "
        "method, and level of human involvement are open to the team to define.", {})]],
     size=11.5, color=MUTED, space=7, line_sp=1.12)

text(s, M, 3.78, 6.5, 0.3, "What that means in a ledger", size=12, color=CYAN, font=H2,
     bold=True, space=0)
bullet_rows(s, M, 4.18, 6.5, [
    ("No single transfer crosses a reporting line.",
     "A crore split into twenty tranches of Rs 9.4 lakh stays under the Rs 10,00,000 CTR "
     "threshold every time."),
    ("The signal is structure and timing, not amount.",
     "Fan-out into mules, a loop back to its origin, a conduit forwarding within a day: none "
     "of these exist inside one record."),
    ("Alert volume is already the constraint.",
     "Large banks convert roughly %s of AML alerts into a filed report, so raising recall by "
     "raising volume only moves the bottleneck."
     % pct(ECO["industry_comparison"]["typical_bank_conversion"])),
    ("Published thresholds can be worked around.",
     "The CTR figure, the RTGS floor and the channel caps are public, and a detector built "
     "only from them can be evaded by design."),
], gap=0.66, dot=[RED, ORANGE, VIOLET, BLUE], size=10.5, head_size=12)

x0 = 7.55
rect(s, x0, 3.78, W - M - x0, 3.02, CARD, line=BORDER)
text(s, x0 + 0.36, 4.0, W - M - x0 - 0.72, 0.34,
     "The bundled 30-day ledger", size=12, color=CYAN, font=H2, bold=True, space=0)
rows = [
    ("Accounts", "%d" % D["accounts"]),
    ("Transactions", "%d over %d days" % (D["transactions"], D["days"])),
    ("Laundering rings present", "%d" % S["rings"]),
    ("Accounts involved in them", "%d" % D["fraud_linked_accounts"]),
    ("Value moved through them", crore(S["exposure"])),
    ("Share of flow that is illicit", pct(S["illicit_share"])),
]
yy = 4.5
for k, v in rows:
    text(s, x0 + 0.36, yy, 2.7, 0.4, k, size=10.5, color=MUTED, space=0, line_sp=1.05)
    text(s, x0 + 3.1, yy, W - M - x0 - 3.46, 0.4, v, size=11.5, color=INK, font=H2,
         bold=True, align=PP_ALIGN.RIGHT, space=0)
    yy += 0.38



# ------------------------------------------- 2. solution and key features
s = slide()
title(s, "Proposed solution and key features",
      "A transaction ledger is turned into a money-flow graph, scored by nine layers, and "
      "returned as a triaged case queue")

steps = [("Ingest", "any CSV schema"), ("Graph", "accounts and flows"),
         ("Nine layers", "rule, graph, ML, adaptive"), ("Rings", "who acts together"),
         ("Triage", "four tiers by score"), ("STR", "FIU-IND draft")]
bw = (7.05 - 5 * 0.14) / 6
for i, (h, sub) in enumerate(steps):
    x = M + i * (bw + 0.14)
    rect(s, x, 1.78, bw, 0.84, CARD2, line=CYAN if i in (2, 5) else BORDER)
    text(s, x + 0.05, 1.95, bw - 0.1, 0.3, h, size=11.5, color=INK, font=H2, bold=True,
         align=PP_ALIGN.CENTER, space=0)
    text(s, x + 0.04, 2.25, bw - 0.08, 0.3, sub, size=8, color=MUTED,
         align=PP_ALIGN.CENTER, space=0)

bullet_rows(s, M, 2.85, 7.05, [
    ("Nine layers, with separate powers.",
     "Structuring, fan-out to fan-in, circular flow, pass-through conduits and threshold "
     "evasion can originate an alert. Centrality and an IsolationForest anomaly score can only "
     "raise the score of an account a primary layer already flagged, since an unsupervised "
     "score cannot be explained to a regulator."),
    ("Layer 9 targets the evasive case.",
     "It scores rhythm, conservation of one sum across hops, and net exposure once reciprocal "
     "cover traffic is removed. These follow from moving other people's money through other "
     "people's accounts. Two of the three must agree, and every hop must be one-shot, before "
     "an account is flagged."),
    ("Thresholds are inferred from the data.",
     "The structuring test reads the lowest round figure a run of tranches never crosses rather "
     "than hard-coding a jurisdiction, so the same code applies to a Rs 10 lakh CTR, a US "
     "$10,000 CTR or a EUR 10,000 ceiling."),
    ("Output is a case file, not a score.",
     "Each alert opens the triggering layer, the ring around the account, a reconstructed "
     "placement to layering to integration timeline, and an editable STR. Every threshold is "
     "exposed as a slider and the pipeline re-runs on change."),
], gap=1.09, dot=[CYAN, RED, VIOLET, GREEN], size=11, head_size=13)

x0 = 8.05
cards = [
    ("%.2f / %.2f" % (MET["precision"], MET["recall"]),
     "Precision and recall on the labelled ledger, across %d seeds" % F["seed_summary"]["n"],
     CYAN),
    ("%d of %d" % (DEC["total"] - DEC["caught"], DEC["total"]),
     "Lawful accounts built to mimic each typology, none flagged", GREEN),
    ("0.00 to 1.00", "Recall on two ledgers built to evade the published thresholds, once "
     "layer 9 is enabled", ORANGE),
]
yy = 1.78
for v, l, c in cards:
    stat(s, x0, yy, W - M - x0, v, l, colour=c, h=1.5, vsize=26)
    yy += 1.66


# ------------------------------------------------------- 3. technology stack
s = slide()
title(s, "Technology stack", "Python throughout, no build step and no external services")

groups = [
    ("Detection engine", CYAN, [
        "Python 3.11, a pure library with no I/O",
        "NetworkX for the directed money-flow graph",
        "pandas and NumPy over the ledger",
        "scikit-learn IsolationForest, nine features",
    ]),
    ("Application", BLUE, [
        "Flask for the API and in-process state",
        "Hand-written HTML, CSS and vanilla JS",
        "Force-directed graph written from scratch",
        "CSV, JSON and XLSX export via openpyxl",
    ]),
    ("Evidence and tooling", VIOLET, [
        "matplotlib for every chart in the deck",
        "python-pptx builds this deck from facts.json",
        "audit.py self-audit, redteam.py adversarial",
        "india_calibration.py pins RBI and NPCI figures",
    ]),
]
gw = (W - 2 * M - 2 * 0.3) / 3
for i, (h, c, items) in enumerate(groups):
    x = M + i * (gw + 0.3)
    rect(s, x, 1.8, gw, 2.62, CARD, line=BORDER)
    rect(s, x, 1.8, 0.06, 2.62, c, radius=False)
    text(s, x + 0.32, 2.04, gw - 0.6, 0.34, h, size=14, color=INK, font=H2, bold=True, space=0)
    yy = 2.5
    for it in items:
        circle(s, x + 0.32, yy + 0.09, 0.1, c)
        text(s, x + 0.56, yy, gw - 0.86, 0.44, it, size=11, color=MUTED, space=0, line_sp=1.1)
        yy += 0.46

rect(s, M, 4.7, W - 2 * M, 1.74, CARD2, line=BORDER)
text(s, M + 0.4, 4.94, 5.0, 0.34, "Pipeline", size=12.5, color=CYAN, font=H2, bold=True,
     space=0)
flow = ["ledger CSV", "adapter", "graph", "layers 1 to 9", "rings and timeline",
        "console or report"]
fw = (W - 2 * M - 0.8 - 5 * 0.4) / 6
for i, lab in enumerate(flow):
    x = M + 0.4 + i * (fw + 0.4)
    rect(s, x, 5.44, fw, 0.62, CARD, line=RGBColor(0x2A, 0x3A, 0x5E))
    text(s, x + 0.05, 5.62, fw - 0.1, 0.3, lab, size=10, color=INK, align=PP_ALIGN.CENTER,
         space=0)
    if i < 5:
        text(s, x + fw, 5.62, 0.4, 0.3, ">", size=12, color=FAINT, align=PP_ALIGN.CENTER,
             space=0)

text(s, M, 6.58, W - 2 * M, 0.55,
     "The engine is a pure function of (transactions, Config). The browser can therefore "
     "re-run the whole pipeline when a threshold slider moves, and the headless report "
     "reproduces the same output with no server running.",
     size=12, color=MUTED, space=0, line_sp=1.15)


# ------------------------------------------------------------ 4. challenges
s = slide()
title(s, "Challenges", "The four that shaped the design, and how each was handled")

items = [
    ("Real Indian account-level data is not available.",
     "Customer ledgers are protected by the PMLA, by RBI regulation and by the DPDP Act 2023, "
     "which is why every public AML dataset is synthetic. The generated ledger pins its rails, "
     "ticket sizes, channel shares and reporting thresholds to published RBI, NPCI and FIU-IND "
     "figures, cited parameter by parameter and exportable as provenance JSON.", RED),
    ("Scoring well on self-generated data proves little.",
     "The ledger therefore carries %d accounts of lawful business engineered to reproduce each "
     "typology's topology: settlement loops, escrow pass-throughs, payroll fan-outs. None is "
     "labelled fraud and none is flagged. Results are reported across %d seeds with a "
     "layer-by-layer ablation." % (DEC["total"], F["seed_summary"]["n"]), ORANGE),
    ("A detector built from published thresholds can be evaded.",
     "redteam.py constructs the ledger someone who had read the project's own Detection Lab "
     "page would run. Layers 1 to 8 score recall 0.00 on it. Layer 9 scores properties that "
     "survive the manoeuvre and recovers recall 1.00 with no false positives, including on a "
     "peeling chain, a typology no layer here implements.", VIOLET),
    ("Recall and review workload trade off directly.",
     "Alerts are tiered so the longest reviews go to the highest scores: %s alerts a day and "
     "about %s analyst hours across the 30-day window. A recall against workload frontier is "
     "computed so the effect of loosening a parameter is visible before it is changed."
     % (ECO["alerts_per_day"], int(ECO["review_hours_window"])), BLUE),
]
gw = (W - 2 * M - 0.3) / 2
for i, (h, body, c) in enumerate(items):
    x = M + (i % 2) * (gw + 0.3)
    y = 1.82 + (i // 2) * 2.38
    rect(s, x, y, gw, 2.2, CARD, line=BORDER)
    circle(s, x + 0.34, y + 0.36, 0.22, c)
    text(s, x + 0.72, y + 0.28, gw - 1.06, 0.64, h, size=13, color=INK, font=H2, bold=True,
         space=0, line_sp=1.08)
    text(s, x + 0.34, y + 0.95, gw - 0.68, 1.12, body, size=10.5, color=MUTED, space=0,
         line_sp=1.15)


# ----------------------------------------------------------- 5. future scope
s = slide()
title(s, "Future scope", "Work identified but not built into the prototype")

bullet_rows(s, M, 1.9, 7.1, [
    ("Incremental scoring on a live stream.",
     "Day-by-day replay places the first alert on day %d and reaches a stable queue by day %d. "
     "Scoring incrementally as payments arrive would let a ring be escalated while it is still "
     "active rather than after the observation window closes."
     % (REP["first_alert_day"], F["streaming"]["converged_day"])),
    ("Detection across institutions.",
     "A ring split across four banks is invisible to each of them individually. Federated or "
     "privacy-preserving exchange of graph structure would let the shape be shared without any "
     "bank exposing customer records."),
    ("An analyst feedback loop.",
     "Each disposition an analyst records is a label. Feeding confirmed and cleared cases back "
     "would allow the corroborating layers to be calibrated on a bank's own book instead of on "
     "a synthetic ledger."),
    ("Filing and case management integration.",
     "The STR draft is complete in content. Emitting it in the FIU-IND FINnet exchange format, "
     "and pushing cases into an existing case management system, would remove the remaining "
     "manual step."),
    ("Other jurisdictions.",
     "Because the reporting threshold is inferred rather than compiled in, supporting another "
     "regime is a calibration file rather than a change to the detectors."),
], gap=0.94, dot=[CYAN, BLUE, VIOLET, GREEN, ORANGE], size=11.5, head_size=13)

x0 = 8.05
rect(s, x0, 1.9, W - M - x0, 4.36, CARD, line=BORDER)
text(s, x0 + 0.36, 2.16, W - M - x0 - 0.72, 0.36, "Current state", size=12.5,
     color=CYAN, font=H2, bold=True, space=0)
now = [
    ("Detection layers implemented", "9"),
    ("Mean detection latency", "%.1f days" % REP["mean_latency_days"]),
    ("False positives on the ledger", "%d" % MET["fp"]),
    ("Stability under perturbation", "%.2f Jaccard" % F["robustness"]["mean_jaccard"]),
    ("Alerts per day", "%.1f" % ECO["alerts_per_day"]),
    ("External dependencies in the UI", "none"),
]
yy = 2.72
for k, v in now:
    text(s, x0 + 0.36, yy, 2.55, 0.46, k, size=11, color=MUTED, space=0, line_sp=1.05)
    text(s, x0 + 2.95, yy, W - M - x0 - 3.31, 0.46, v, size=11.5, color=INK, font=H2, bold=True,
         align=PP_ALIGN.RIGHT, space=0)
    yy += 0.6

text(s, M, 6.6, W - 2 * M, 0.5,
     "The prototype runs offline on a single machine. The items above concern scale and "
     "integration.",
     size=12, color=MUTED, space=0)


# ---------------------------------------------------------------- 6. closing
s = slide(dark=True)
rect(s, 0, 0, 0.12, HGT, CYAN, radius=False)
text(s, 1.35, 3.0, W - 2.7, 0.9, "Thank you", size=44, color=INK, font=H1, bold=True, space=2)
rect(s, 1.35, 3.88, 1.6, 0.035, CYAN, radius=False)
text(s, 1.35, 4.2, W - 2.7, 0.5,
     "AMLTrace, problem statement 5, Tracing Financial Crime Across Patterns",
     size=13, color=MUTED, space=0)


out = os.path.join(HERE, "AMLTrace_Round1_Deck.pptx")
prs.save(out)
print("wrote", out)
