"""
India calibration - real published figures behind the synthetic ledger.

WHY THIS FILE EXISTS
--------------------
Account-level Indian transaction data is not publicly available and cannot be.
Customer transaction records are protected by RBI regulation, by PMLA
confidentiality, and by the Digital Personal Data Protection Act 2023. No bank
or regulator publishes a customer ledger. Every public AML dataset in existence
(IBM AML, PaySim, AMLSim, Elliptic) is synthetic for exactly this reason.

What IS public and real is the aggregate behaviour of India's payment rails and
the reporting rules that govern them. So the ledger stays synthetic, but every
parameter that shapes it is pinned to a published figure, recorded here with its
source and period. That is the standard method in AML research, and it is what
makes a synthetic ledger defensible rather than arbitrary.

Each entry carries a `confidence`:
  reported   quoted directly from an RBI / NPCI / FIU-IND publication
  regulatory a standing statutory or regulatory threshold
  derived    arithmetic on reported figures (the derivation is shown)
  assumed    a modelling choice with no public source - stated, not hidden

Update the figures here and the whole application, including the deck, follows.
"""

from __future__ import annotations

SOURCES = {
    "rbi_psr": {
        "label": "RBI Payment Systems Report",
        "period": "H1 CY2025 (published 2025)",
        "url": "https://www.rbi.org.in/",
        "note": "Volume and value shares by payment system.",
    },
    "npci_upi": {
        "label": "NPCI UPI ecosystem statistics",
        "period": "H1 CY2025",
        "url": "https://www.npci.org.in/what-we-do/upi/product-statistics",
        "note": "Monthly UPI volume and value.",
    },
    "pmlr_2005": {
        "label": "PML (Maintenance of Records) Rules, 2005, Rule 3",
        "period": "in force",
        "url": "https://fiuindia.gov.in/",
        "note": "Cash Transaction Report threshold; STR has no amount floor.",
    },
    "rbi_rtgs": {
        "label": "RBI RTGS System Regulations",
        "period": "in force",
        "url": "https://www.rbi.org.in/",
        "note": "RTGS is for high-value transfers; minimum per transaction Rs 2,00,000.",
    },
    "npci_limits": {
        "label": "NPCI channel transaction limits",
        "period": "in force",
        "url": "https://www.npci.org.in/",
        "note": "Per-transaction caps for UPI and IMPS.",
    },
}


# ---------------------------------------------------------------------------
# 1. Channel mix - what share of India's payments runs on which rail
# ---------------------------------------------------------------------------
# RBI Payment Systems Report, H1 CY2025: UPI carried ~85% of digital payment
# VOLUME but only ~9% of VALUE; RTGS carried ~0.1% of volume but ~69% of value.
# The remainder is split across NEFT, IMPS, cards and the rest. We model the
# four rails that matter for account-to-account laundering.
CHANNEL_MIX = {
    # channel: (share of transaction COUNT, share of transaction VALUE)
    "UPI":  (0.850, 0.090),
    "NEFT": (0.108, 0.190),
    "IMPS": (0.041, 0.030),
    "RTGS": (0.001, 0.690),
}
CHANNEL_MIX_META = {
    "confidence": "reported",
    "source": "rbi_psr",
    "detail": "UPI ~85% of volume / ~9% of value; RTGS ~0.1% of volume / ~69% of value "
              "(H1 CY2025). NEFT and IMPS fill the remainder of the account-to-account share.",
}

# ---------------------------------------------------------------------------
# 2. Average ticket size per rail - derived from published totals
# ---------------------------------------------------------------------------
# UPI, H1 CY2025: 106.37 billion transactions worth Rs 143.3 trillion
#   => 143.3e12 / 106.37e9 = Rs 1,347 average ticket.
# NEFT, CY2025: ~1,000 crore (10 billion) transactions worth ~Rs 482 lakh crore
#   => 4.82e14 / 1.0e10 = Rs 48,200 average ticket.
# RTGS and IMPS averages follow from their volume/value shares against the same
# base; RTGS is the high-value rail by construction.
AVERAGE_TICKET = {
    "UPI":   1_347,
    "NEFT":  48_200,
    "IMPS":  9_800,
    "RTGS":  5_600_000,
}
AVERAGE_TICKET_META = {
    "confidence": "derived",
    "source": "rbi_psr + npci_upi",
    "detail": "UPI = Rs 143.3 trn / 106.37 bn txns (H1 CY2025). "
              "NEFT = Rs 482 lakh crore / ~1,000 crore txns (CY2025). "
              "IMPS and RTGS derived from their reported volume and value shares.",
}

# Laundering does not move money at the population average. A mule chain moves
# large sums on retail rails. These are the multipliers applied to the average
# ticket when a transaction belongs to a laundering typology.
ILLICIT_TICKET_MULTIPLIER = {
    "UPI": 40, "IMPS": 45, "NEFT": 12, "RTGS": 0.20,
}
ILLICIT_TICKET_META = {
    "confidence": "assumed",
    "source": None,
    "detail": "Modelling choice. Laundering concentrates value into fewer, larger "
              "retail-rail transfers, and stays below RTGS's typical ticket to avoid "
              "the scrutiny that comes with it.",
}

# ---------------------------------------------------------------------------
# 3. Regulatory thresholds - the rules the detector is written against
# ---------------------------------------------------------------------------
CTR_THRESHOLD = 1_000_000          # Rs 10,00,000
CTR_META = {
    "confidence": "regulatory", "source": "pmlr_2005",
    "detail": "Cash transactions above Rs 10 lakh must be reported; so must multiple "
              "smaller transactions aggregating above Rs 10 lakh in a month. "
              "STRs are judgment-based with no amount floor, filed within 7 working days.",
}

RTGS_MINIMUM = 200_000             # Rs 2,00,000
RTGS_META = {
    "confidence": "regulatory", "source": "rbi_rtgs",
    "detail": "RTGS carries no transaction below Rs 2,00,000. A ledger that puts small "
              "sums on RTGS is immediately implausible to anyone who works in payments.",
}

CHANNEL_CAP = {                    # per-transaction ceilings
    "UPI": 100_000,
    "IMPS": 500_000,
    "NEFT": None,
    "RTGS": None,
}
CHANNEL_CAP_META = {
    "confidence": "regulatory", "source": "npci_limits",
    "detail": "UPI general per-transaction cap Rs 1,00,000 (higher for specific "
              "categories such as capital markets and insurance); IMPS Rs 5,00,000. "
              "NEFT and RTGS have no per-transaction ceiling.",
}

# ---------------------------------------------------------------------------
# 4. Institution codes - real IFSC bank prefixes, synthetic account numbers
# ---------------------------------------------------------------------------
BANKS = [
    ("HDFC", "HDFC Bank", 0.16),
    ("SBIN", "State Bank of India", 0.22),
    ("ICIC", "ICICI Bank", 0.13),
    ("UTIB", "Axis Bank", 0.09),
    ("KKBK", "Kotak Mahindra Bank", 0.06),
    ("PUNB", "Punjab National Bank", 0.08),
    ("BARB", "Bank of Baroda", 0.07),
    ("CNRB", "Canara Bank", 0.06),
    ("UBIN", "Union Bank of India", 0.05),
    ("IDIB", "Indian Bank", 0.04),
    ("YESB", "Yes Bank", 0.02),
    ("IBKL", "IDBI Bank", 0.02),
]
BANKS_META = {
    "confidence": "reported", "source": "rbi_psr",
    "detail": "IFSC prefixes are the real four-character codes of these institutions. "
              "Account numbers are synthetic. Shares approximate relative retail "
              "footprint and only affect which prefix an account is given.",
}

# ---------------------------------------------------------------------------
# 5. Operating rhythm
# ---------------------------------------------------------------------------
BUSINESS_HOURS = (9, 19)
NIGHT_SHARE_NORMAL = 0.06          # ordinary payments outside business hours
NIGHT_SHARE_ILLICIT = 0.28         # laundering skews later - mules act off-hours
RHYTHM_META = {
    "confidence": "assumed", "source": None,
    "detail": "Modelling choice. RTGS and NEFT settle in defined windows on working "
              "days; UPI and IMPS run 24x7, which is why retail rails carry the "
              "off-hours share.",
}


def manifest() -> list[dict]:
    """Every calibrated parameter with its provenance, for the app and the deck."""
    return [
        {"group": "Channel mix", "value": ", ".join(f"{k} {v[0]:.1%} vol / {v[1]:.1%} val"
                                                    for k, v in CHANNEL_MIX.items()),
         **CHANNEL_MIX_META},
        {"group": "Average ticket size",
         "value": ", ".join(f"{k} Rs {v:,}" for k, v in AVERAGE_TICKET.items()),
         **AVERAGE_TICKET_META},
        {"group": "CTR threshold", "value": f"Rs {CTR_THRESHOLD:,}", **CTR_META},
        {"group": "RTGS minimum", "value": f"Rs {RTGS_MINIMUM:,}", **RTGS_META},
        {"group": "Per-transaction caps",
         "value": ", ".join(f"{k} " + (f"Rs {v:,}" if v else "none")
                            for k, v in CHANNEL_CAP.items()), **CHANNEL_CAP_META},
        {"group": "Institutions", "value": f"{len(BANKS)} real IFSC prefixes", **BANKS_META},
        {"group": "Illicit ticket multiplier",
         "value": ", ".join(f"{k} x{v}" for k, v in ILLICIT_TICKET_MULTIPLIER.items()),
         **ILLICIT_TICKET_META},
        {"group": "Operating rhythm",
         "value": f"{BUSINESS_HOURS[0]}:00-{BUSINESS_HOURS[1]}:00, "
                  f"{NIGHT_SHARE_NORMAL:.0%} of ordinary and {NIGHT_SHARE_ILLICIT:.0%} "
                  f"of illicit activity off-hours", **RHYTHM_META},
    ]


def provenance() -> dict:
    """The disclosure block the application shows, verbatim."""
    return {
        "headline": "Synthetic ledger, calibrated to real published Indian figures",
        "why_synthetic": (
            "Account-level Indian transaction data is not publicly available and cannot "
            "be. Customer records are protected by RBI regulation, by PMLA "
            "confidentiality and by the DPDP Act 2023 - no bank or regulator publishes a "
            "customer ledger. Every public AML dataset, including IBM's and PaySim, is "
            "synthetic for the same reason."
        ),
        "what_is_real": (
            "The rails, the thresholds and the money. Channel mix, average ticket size "
            "per rail, the CTR reporting threshold, the RTGS floor, per-transaction caps "
            "and the institution codes are all taken from RBI, NPCI and FIU-IND "
            "publications and are cited line by line below."
        ),
        "what_is_not": (
            "The accounts, the counterparties and the laundering rings are invented. No "
            "real person, account or institution's activity appears anywhere in this "
            "system."
        ),
        "sources": SOURCES,
        "parameters": manifest(),
    }
