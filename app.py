"""
AMLTrace - web application layer.

Run:  python app.py      then open http://127.0.0.1:5000

Security posture
----------------
This is an analyst console that ingests untrusted CSV ledgers, so the web layer
treats every request parameter and every uploaded field as hostile:

  * responses carry a strict CSP and the usual hardening headers, so a payload
    that survives into the page still cannot fetch or execute anything;
  * state-changing endpoints reject cross-site requests, because the app binds
    a predictable localhost port and any page in the analyst's browser could
    otherwise replace the ledger under investigation;
  * every numeric parameter is coerced and clamped rather than passed to int(),
    so neither a malformed value (a 500 with a traceback) nor an extreme one
    (an unbounded generation loop) reaches the engine;
  * pipeline runs are throttled, since each one is seconds of CPU;
  * internal exception text never reaches the client.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import date
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import aml_engine as E
import dataset_adapters as A

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Bounds for everything the client may set. A parameter outside its range is
# not a bad setting, it is a denial of service: n_normal drives an O(n*m)
# generation loop and hops drives a graph expansion, so both are clamped
# before the engine sees them.
SEED_RANGE = (0, 2 ** 31 - 1)
N_NORMAL_RANGE = (10, 2_000)
N_NORMAL_TXN_RANGE = (10, 10_000)
HOPS_RANGE = (1, 3)
COST_RANGE = (0, 10_000_000)
MULTIPLIER_RANGE = (1, 1_000_000)
MAX_CSV_CHARS = 24 * 1024 * 1024        # JSON-body upload path
MAX_ACCOUNT_ID = 256                    # path-segment sanity bound

_lock = threading.RLock()
_STATE: dict = {}

# Held only while the first ledger is being built. Flask serves requests on
# threads, so the browser's opening burst of view requests arrives before any
# state exists and each one would otherwise start its own pipeline run.
_first_build = threading.Lock()
_replay_lock = threading.Lock()   # one replay computation at a time, never two

# One pipeline run at a time; a second concurrent request waits briefly and is
# then told the service is busy rather than multiplying CPU load.
_pipeline = threading.Semaphore(1)
_PIPELINE_WAIT_S = 30


class ServiceBusy(Exception):
    """Raised when the pipeline is saturated."""


@contextmanager
def pipeline_slot():
    if not _pipeline.acquire(timeout=_PIPELINE_WAIT_S):
        raise ServiceBusy()
    try:
        yield
    finally:
        _pipeline.release()


# ------------------------------------------------------------ validation ---

def clamp_int(value, default: int, lo: int, hi: int) -> int:
    """Coerce to int and clamp. Never raises: a client cannot turn a typo in a
    text box into a 500, nor a large number into an unbounded run."""
    if value is None or isinstance(value, bool):
        return default
    try:
        n = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, n))


def json_body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name, fallback: str = "export") -> str:
    """Content-Disposition filenames are built from account identifiers that
    came out of an uploaded ledger. A quote or a newline in one would otherwise
    let the uploader rewrite the response header."""
    cleaned = _UNSAFE_NAME.sub("_", str(name)).strip("._-")
    return cleaned[:120] or fallback


def valid_account(account) -> bool:
    return bool(account) and len(str(account)) <= MAX_ACCOUNT_ID and "\x00" not in str(account)


# ----------------------------------------------------------------- state ---

def rebuild(seed=7, n_normal=135, n_normal_txn=150, cfg_dict=None, records=None, schema=None):
    global _STATE
    cfg = E.Config.from_dict(cfg_dict)
    if records is not None:
        df, truth, meta = E.load_dataset_from_records(records)
        source = "uploaded (" + str(schema) + ")" if schema else "uploaded"
    else:
        df, truth, meta = E.generate_dataset(seed, n_normal, n_normal_txn)
        source = "synthetic"
    result = E.analyse(df, truth, cfg, meta)
    result["source"] = source
    result["seed"] = seed
    result["schema"] = schema or "built-in"
    # Publish by rebinding rather than clearing in place: a reader that already
    # grabbed the previous dict keeps a complete, self-consistent snapshot
    # instead of observing a half-populated one mid-rebuild.
    with _lock:
        _STATE = {"df": df, "truth": truth, "cfg": cfg, "meta": meta,
                  "payload": result, "replay": None}
    return result


_EVASION = None            # adversarial benchmark; ledger-independent, so cached once
_evasion_lock = threading.Lock()   # ...and computed once, even under a race


def state(build: bool = True) -> dict:
    """Current snapshot. Callers hold the returned dict, never the global."""
    with _lock:
        snap = _STATE
    if snap or not build:
        return snap
    with _first_build:
        # Checked again inside the lock: whoever held it before us has published
        # a ledger by now, and rebuilding over it would throw that work away.
        with _lock:
            snap = _STATE
        if not snap:
            rebuild()
            with _lock:
                snap = _STATE
    return snap


def payload() -> dict:
    return state()["payload"]


def ensure_replay(snap: dict) -> dict:
    """Day-by-day re-detection, computed once per ledger and cached.

    Single-flight, not merely cached. The replay re-runs the whole pipeline once
    per day of the window, and the startup warm-up computes it in the background,
    so an analyst who opens the panel early used to arrive while that work was
    still in progress, find the cache empty and start a second copy of it. Two
    threads then competed for the same cores and the visible wait roughly
    doubled. Holding the lock across the computation means the second caller
    waits for the first one's answer instead of duplicating it.
    """
    if snap.get("replay") is not None:
        return snap["replay"]
    with _replay_lock:
        if snap.get("replay") is None:
            rp = E.replay(snap["df"], snap["truth"], snap["cfg"],
                          snap.get("meta", {}).get("rings", {}))
            with _lock:
                snap["replay"] = rp
    return snap["replay"]


def alert_index(p: dict) -> dict:
    return {"flags": {a["account"]: a["risk_score"] for a in p["alerts"]},
            "reasons": {a["account"]: a["reasons"] for a in p["alerts"]},
            "layers": {a["account"]: a["layers"] for a in p["alerts"]},
            "centrality": {a["account"]: a["centrality"] for a in p["alerts"]},
            "ml_scores": {a["account"]: a["ml_score"] for a in p["alerts"]
                          if a["ml_score"] is not None}}


# -------------------------------------------------------------- security ---

CSP = ("default-src 'self'; "
       "script-src 'self'; "
       "style-src 'self' 'unsafe-inline'; "      # the console styles elements inline
       "img-src 'self' data:; "
       "font-src 'self'; "
       "connect-src 'self'; "
       "object-src 'none'; "
       "base-uri 'none'; "
       "form-action 'self'; "
       "frame-ancestors 'none'")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_cross_site() -> bool:
    """True when the browser says this request came from another site.

    The server listens on a guessable localhost port with no authentication, so
    without this check any page the analyst happens to visit could silently
    POST a new ledger over the one under investigation. Non-browser clients
    (curl, the report generator) send none of these headers and are unaffected.
    """
    if request.headers.get("Sec-Fetch-Site", "").lower() in ("cross-site", "same-site"):
        return True
    for header in ("Origin", "Referer"):
        value = request.headers.get(header)
        if not value:
            continue
        if value == "null":
            return True
        netloc = urlparse(value).netloc
        return bool(netloc) and netloc != request.host
    return False


@app.before_request
def guard_request():
    if request.method in SAFE_METHODS:
        return None
    if is_cross_site():
        return jsonify({"error": "cross-site request rejected"}), 403
    return None


@app.after_request
def harden(resp: Response) -> Response:
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy",
                            "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.path.startswith("/api/"):
        # Alert queues and STR dossiers are case material; keep them out of
        # disk and proxy caches.
        resp.headers["Cache-Control"] = "no-store, private"
    return resp


@app.errorhandler(ServiceBusy)
def _busy(_exc):
    return jsonify({"error": "analysis service busy - retry in a moment"}), 429


@app.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        return jsonify({"error": exc.description or exc.name}), exc.code
    return exc


@app.errorhandler(Exception)
def _unhandled(exc: Exception):
    # Never surface internal exception text: it carries file paths, column
    # names and library internals that a client has no business seeing.
    app.logger.exception("unhandled error on %s", request.path)
    return jsonify({"error": "internal error"}), 500


# ---------------------------------------------------------------- routes ---

@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(payload())


@app.post("/api/simulate")
def api_simulate():
    body = json_body()
    cfg_dict = body.get("config")
    with pipeline_slot():
        result = rebuild(
            seed=clamp_int(body.get("seed"), 7, *SEED_RANGE),
            n_normal=clamp_int(body.get("n_normal"), 135, *N_NORMAL_RANGE),
            n_normal_txn=clamp_int(body.get("n_normal_txn"), 150, *N_NORMAL_TXN_RANGE),
            cfg_dict=cfg_dict if isinstance(cfg_dict, dict) else None,
        )
    return jsonify(result)


@app.post("/api/reconfigure")
def api_reconfigure():
    """Re-run detection on the CURRENT ledger with new thresholds."""
    global _STATE
    body = json_body()
    snap = state()
    overrides = body.get("config")
    cfg = E.Config.from_dict({**snap["cfg"].to_dict(),
                              **(overrides if isinstance(overrides, dict) else {})})
    with pipeline_slot():
        result = E.analyse(snap["df"], snap["truth"], cfg, snap.get("meta", {}),
                           do_ablation=bool(body.get("ablation", True)))
    result["source"] = snap["payload"].get("source", "synthetic")
    result["schema"] = snap["payload"].get("schema", "built-in")
    result["seed"] = snap["payload"].get("seed")
    with _lock:
        # Detection ran outside the lock, on the ledger that was current when
        # this request started. If an upload has replaced it since, publishing
        # this result would put the previous ledger back under the analyst
        # without saying so, so the new one is left alone.
        if _STATE is not snap:
            return jsonify({"error": "the ledger changed while this ran; retry"}), 409
        _STATE = {**snap, "cfg": cfg, "payload": result, "replay": None}
    return jsonify(result)


@app.post("/api/upload")
def api_upload():
    """Accept a CSV ledger: sender,receiver,amount[,day|timestamp][,txn_id][,channel][,is_fraud]."""
    if "file" in request.files:
        raw = request.files["file"].read().decode("utf-8-sig", errors="replace")
    else:
        supplied = json_body().get("csv")
        raw = supplied if isinstance(supplied, str) else None
    if not raw:
        return jsonify({"error": "no CSV supplied"}), 400
    if len(raw) > MAX_CSV_CHARS:
        return jsonify({"error": "CSV too large"}), 413

    snap = state(build=False)
    try:
        import pandas as pd
        raw_df = pd.read_csv(io.StringIO(raw), low_memory=False)
        if raw_df.empty:
            return jsonify({"error": "CSV contained no rows"}), 400
        limit = clamp_int(request.form.get("limit"), 0, 0, E.MAX_INGEST_ROWS)
        if limit:
            raw_df = raw_df.head(limit)
        canon, schema = A.to_canonical(raw_df)
        if canon.empty:
            return jsonify({"error": "no usable transactions after normalisation"}), 400
        with pipeline_slot():
            result = rebuild(records=canon.to_dict("records"), schema=schema,
                             cfg_dict=snap["cfg"].to_dict() if snap else None)
    except ServiceBusy:
        raise
    except E.LedgerError as exc:
        # Deliberate, analyst-facing rejections from the adapters and the
        # ingest guards: unknown schema, missing column, row/day-span caps.
        return jsonify({"error": str(exc)[:500]}), 400
    except ValueError:
        # A ValueError from anywhere else is pandas or numpy describing its own
        # internals. Forwarding it told the uploader about column alignment and
        # dtype handling, which is neither actionable nor theirs to see.
        app.logger.exception("upload rejected by the parser")
        return jsonify({"error": "could not parse this file as a ledger"}), 400
    except MemoryError:
        return jsonify({"error": "ledger too large to analyse"}), 413
    except Exception:
        app.logger.exception("upload failed")
        return jsonify({"error": "could not parse this file as a ledger"}), 400
    return jsonify(result)


def build_dossier(account):
    """Shared by the dossier endpoint and the STR export. Returns None when the
    account is not in the current ledger, so neither route can be used to probe
    for arbitrary identifiers."""
    if not valid_account(account):
        return None
    snap = state()
    p = snap["payload"]
    if account not in p["accounts"]:
        return None
    G = E.build_graph(snap["df"])
    return E.generate_dossier(account, snap["df"], G, alert_index(p), p["rings"], p["accounts"])


@app.get("/api/dossier/<account>")
def api_dossier(account):
    d = build_dossier(account)
    if d is None:
        return jsonify({"error": "unknown account"}), 404
    return jsonify(d)


@app.get("/api/subgraph/<account>")
def api_subgraph(account):
    hops = clamp_int(request.args.get("hops"), 1, *HOPS_RANGE)
    if not valid_account(account):
        return jsonify({"error": "unknown account"}), 404
    snap = state()
    G = E.build_graph(snap["df"])
    if account not in G:
        return jsonify({"error": "unknown account"}), 404
    import networkx as nx
    reach = nx.single_source_shortest_path_length(G.to_undirected(), account, cutoff=hops)
    p = snap["payload"]
    risk = {a["account"]: a["risk_score"] for a in p["alerts"]}
    members = sorted(reach)
    sg = G.subgraph(members)
    return jsonify({
        "center": account, "hops": hops,
        "nodes": [{"id": n, "risk": risk.get(n, 0), "tier": E.risk_tier(risk.get(n, 0))[0],
                   "hop": reach[n]} for n in members],
        "edges": [{"source": u, "target": v, "amount": int(d["amount"]), "count": int(d["count"])}
                  for u, v, d in sorted(sg.edges(data=True), key=lambda e: (e[0], e[1]))],
    })


@app.get("/api/schemas")
def api_schemas():
    return jsonify(A.describe())


@app.get("/api/replay")
def api_replay():
    """Day-by-day re-detection. Cached: it runs the whole pipeline once per day."""
    snap = state()
    with pipeline_slot():
        return jsonify(ensure_replay(snap))


@app.get("/api/evasion")
def api_evasion():
    """Adversarial benchmark: two ledgers built to defeat this engine's own rules.

    Computed once per process, not per request. It scores four full pipelines and
    none of it depends on the ledger currently loaded, so a repeat visit to the
    panel should not cost the analyst another few seconds.
    """
    global _EVASION
    with _lock:
        cached = _EVASION
    if cached is None:
        with _evasion_lock:
            with _lock:
                cached = _EVASION
            if cached is None:
                with pipeline_slot():
                    cached = E.evasion_benchmark()
                with _lock:
                    _EVASION = cached
    return jsonify({"scenarios": cached})


@app.post("/api/economics")
def api_economics():
    """Recost the current alert population under different staffing assumptions."""
    body = json_body()
    p = payload()
    return jsonify(E.alert_economics(
        p["alerts"], p["summary"], p["metrics"], p["dataset"],
        analyst_cost_per_hour=clamp_int(body.get("analyst_cost_per_hour"), 1200, *COST_RANGE),
        portfolio_multiplier=clamp_int(body.get("portfolio_multiplier"), 1, *MULTIPLIER_RANGE)))


# ---------------------------------------------------------------- export ---

def _attachment(body: bytes, mimetype: str, filename: str) -> Response:
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition":
                             'attachment; filename="' + safe_filename(filename) + '"'})


def _csv_response(rows, fields, filename):
    # Every cell goes through the formula guard on the way out. These files are
    # opened in a spreadsheet by an analyst who was told they are case evidence,
    # and the identifiers in them came from whatever ledger was uploaded.
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows({k: E.csv_safe(v) for k, v in row.items()} for row in rows)
    return _attachment(buf.getvalue().encode("utf-8-sig"), "text/csv", filename)


@app.get("/api/export/alerts.csv")
def export_alerts():
    p = payload()
    rows = [{
        "account": a["account"], "risk_score": a["risk_score"], "risk_tier": a["risk_tier"],
        "typology": a["typology"], "ring": a["ring"] or "", "sla": a["sla"],
        "layers_triggered": " | ".join(a["layers"]),
        "total_inflow_inr": a["inflow"], "total_outflow_inr": a["outflow"],
        "exposure_inr": a["exposure"], "txn_count": a["txn_count"],
        "betweenness": a["centrality"], "ml_anomaly_score": a["ml_score"],
        "recommended_action": a["action"],
        "grounds_of_suspicion": " || ".join(f"[{r['layer']}] {r['detail']}" for r in a["reasons"]),
    } for a in p["alerts"]]
    return _csv_response(rows, list(rows[0]) if rows else ["account"],
                         f"AMLTrace_alerts_{date.today()}.csv")


@app.get("/api/export/timeline.csv")
def export_timeline():
    p = payload()
    fields = ["txn_id", "timestamp", "day", "stage", "ring", "sender", "receiver",
              "amount", "channel", "sender_risk", "receiver_risk"]
    return _csv_response(p["timeline"]["events"], fields,
                         f"AMLTrace_timeline_{date.today()}.csv")


@app.get("/api/export/watchlist.csv")
def export_watchlist():
    p = payload()
    rows = [{**r, "path": " -> ".join(r["path"])} for r in p["propagation"]["contamination"]]
    fields = ["account", "score", "raw", "hops", "nearest_alert", "path", "verdict"]
    return _csv_response(rows, fields, f"AMLTrace_watchlist_{date.today()}.csv")


@app.get("/api/export/transactions.csv")
def export_transactions():
    df = E.csv_safe_frame(state()["df"])
    return _attachment(df.to_csv(index=False).encode("utf-8-sig"), "text/csv",
                       f"AMLTrace_ledger_{date.today()}.csv")


@app.get("/api/export/rings.csv")
def export_rings():
    p = payload()
    rows = [{"ring_id": r["ring_id"], "typology": r["typology"], "size": r["size"],
             "peak_risk": r["peak_risk"], "internal_value_inr": r["internal_value"],
             "layers": " | ".join(r["layers"]), "members": " ".join(r["members"])}
            for r in p["rings"]]
    return _csv_response(rows, ["ring_id", "typology", "size", "peak_risk",
                                "internal_value_inr", "layers", "members"],
                         f"AMLTrace_rings_{date.today()}.csv")


@app.get("/api/export/replay.csv")
def export_replay():
    snap = state()
    with pipeline_slot():
        rp = ensure_replay(snap)
    rows = [{"day": f["day"], "date": f["date"], "txns_seen": f["txns_seen"],
             "value_seen": f["value_seen"], "alerts": f["flagged"],
             "critical": f["tiers"]["CRITICAL"], "high": f["tiers"]["HIGH"],
             "medium": f["tiers"]["MEDIUM"], "precision": f["precision"],
             "recall": f["recall"], "new_alerts": " ".join(f["new_alerts"])}
            for f in rp["frames"]]
    return _csv_response(rows, list(rows[0]) if rows else ["day"],
                         f"AMLTrace_replay_{date.today()}.csv")


@app.get("/api/export/latency.csv")
def export_latency():
    snap = state()
    with pipeline_slot():
        rp = ensure_replay(snap)
    return _csv_response(rp["latency"],
                         ["account", "typology", "first_active_day", "detected_day",
                          "latency_days"],
                         f"AMLTrace_latency_{date.today()}.csv")


@app.get("/api/export/decoys.csv")
def export_decoys():
    d = payload().get("decoys", {})
    if not d.get("present"):
        return _csv_response([], ["family"], "AMLTrace_hard_negatives.csv")
    rows = [{"family": f["family"], "accounts": f["accounts"], "caught": f["caught"],
             "survived": f["survived"], "discrimination": f["discrimination"],
             "caught_accounts": " ".join(f["caught_accounts"])} for f in d["families"]]
    return _csv_response(rows, ["family", "accounts", "caught", "survived", "discrimination",
                                "caught_accounts"],
                         f"AMLTrace_hard_negatives_{date.today()}.csv")


@app.get("/api/export/provenance.json")
def export_provenance():
    """Where every calibrated parameter came from, and how the generated ledger
    compares with the published figure it was calibrated to."""
    p = payload()
    doc = {"generated": str(date.today()),
           "provenance": p.get("provenance"),
           "observed_channel_mix": p.get("channel_mix"),
           "dataset": p["dataset"]}
    return _attachment(json.dumps(doc, indent=2).encode("utf-8"), "application/json",
                       f"AMLTrace_data_provenance_{date.today()}.json")


@app.get("/api/export/metrics.json")
def export_metrics():
    p = payload()
    doc = {"generated": str(date.today()), "dataset": p["dataset"], "summary": p["summary"],
           "metrics": p["metrics"], "ablation": p["ablation"], "robustness": p["robustness"],
           "hard_negatives": p.get("decoys"),
           "discriminator_ablation": p.get("discriminator_ablation"),
           "economics": p.get("economics"), "config": p["config"],
           "key_finding": ("Unsupervised anomaly detection alone is a weak AML control. Relational "
                           "structure - who pays whom, in what shape, in what order - carries the "
                           "signal; the ML layer is used only to corroborate a rule-based flag.")}
    return _attachment(json.dumps(doc, indent=2).encode("utf-8"), "application/json",
                       f"AMLTrace_metrics_{date.today()}.json")


@app.get("/api/export/str/<account>.json")
def export_str(account):
    d = build_dossier(account)
    if d is None:
        return jsonify({"error": "unknown account"}), 404
    return _attachment(json.dumps(d, indent=2).encode("utf-8"), "application/json",
                       f"STR_{account}_{date.today()}.json")


@app.get("/api/export/str_bundle.csv")
def export_str_bundle():
    """One row per CRITICAL/HIGH account - the tabular STR filing extract."""
    snap = state()
    p = snap["payload"]
    G = E.build_graph(snap["df"])
    result = alert_index(p)
    fm = E.build_features(snap["df"], p["accounts"])
    rows = []
    for a in p["alerts"]:
        if a["risk_tier"] not in ("CRITICAL", "HIGH"):
            continue
        d = E.generate_dossier(a["account"], snap["df"], G, result, p["rings"], p["accounts"], fm)
        ts = d["transaction_summary"]
        rows.append({
            "str_reference": d["report_reference"], "date_of_report": d["date_of_report"],
            "account_id": d["account_id"], "risk_tier": d["risk_tier"], "risk_score": d["risk_score"],
            "typology": a["typology"], "ring": a["ring"] or "",
            "total_inflow_inr": ts["total_inflow"], "total_outflow_inr": ts["total_outflow"],
            "transaction_count": ts["transaction_count"], "counterparties": ts["counterparties"],
            "credits_just_below_ctr": ts["credits_just_below_ctr"],
            "grounds_of_suspicion": " || ".join(g["detail"] for g in d["grounds_of_suspicion"]),
            "narrative": d["narrative"], "action_taken": d["action_taken"],
            "regulatory_basis": d["regulatory_basis"],
        })
    fields = ["str_reference", "date_of_report", "account_id", "risk_tier", "risk_score", "typology",
              "ring", "total_inflow_inr", "total_outflow_inr", "transaction_count", "counterparties",
              "credits_just_below_ctr", "grounds_of_suspicion", "narrative", "action_taken",
              "regulatory_basis"]
    return _csv_response(rows, fields, f"FIU-IND_STR_bundle_{date.today()}.csv")


def warm_caches() -> None:
    """Compute the two slow, ledger-independent panels before anyone asks for them.

    The streaming replay re-runs the whole pipeline once per day of the window,
    and the adversarial benchmark scores four more. Both are cached, but the
    first visitor pays for them, and in a demo that first visitor is whoever is
    being shown the console. Doing the work while the browser is still opening
    costs nothing: the server is idle at that point anyway.
    """
    try:
        snap = state()
        ensure_replay(snap)
        global _EVASION
        with _evasion_lock:
            with _lock:
                already = _EVASION
            if already is None:
                result = E.evasion_benchmark()
                with _lock:
                    _EVASION = result
    except Exception:
        # A warm-up is an optimisation. If it fails, the endpoints still compute
        # on demand, and taking the server down over it would be the wrong trade.
        pass


if __name__ == "__main__":
    rebuild()
    threading.Thread(target=warm_caches, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    # Loopback only: the console has no authentication, so it must not be
    # reachable from the network.
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"\n  AMLTrace running on http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
