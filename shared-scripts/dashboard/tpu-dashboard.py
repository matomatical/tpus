#!/usr/bin/python3
"""TPU dashboard — in-memory time series of HBM and duty cycle across the cluster.

Polls each TPU VM's tpu-heartbeat-web (port 8080) for /status.json and
/metrics.json every INTERVAL seconds, stores up to RETENTION_SEC of samples
per chip in memory, and tracks the latest combined per-chip occupancy +
metrics. Serves a static HTML page (vendored uPlot) and a JSON API on
port 8082.

Runs on tpu0 only. History is in-memory, so a service restart wipes it.
Reachable from a developer laptop via:
    ssh -L 8082:localhost:8082 tpu0
    open http://localhost:8082/
"""

import json
import math
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# # #
# CONFIGURATION

TPU_HOSTS = ["tpu0", "tpu1", "tpu2", "tpu3"]
NUM_CHIPS = 4              # TPU v4 — 4 chips per VM

PORT_UPSTREAM = 8080       # tpu-heartbeat-web
PORT_LISTEN = 8082         # 8081 informally claimed by user http.servers

INTERVAL = 5               # poll cadence, matches sidecar
RETENTION_SEC = 86400      # 24h
STALE_AFTER = 15           # mirrors tpups.py:27
EXPECTED_SCHEMA = 1        # mirrors tpu-metrics.py SCHEMA_VERSION

DEQUE_MAXLEN = math.ceil(RETENTION_SEC / INTERVAL) + 200  # ~17480

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# # #
# HISTORY


class History:
    """Thread-safe per-chip rolling buffers + per-node last-seen state.

    Every poll cycle records one entry per chip at the *dashboard's* shared
    timestamp (not the upstream sidecar's `last_updated`). This keeps all
    16 chips on a single x-axis grid so charts render contiguous lines —
    if instead each chip used its node's `last_updated`, sibling nodes'
    interleaved timestamps would surround every value with nulls and break
    the line under `spanGaps: false`. Stale upstreams record `(ts, None,
    None)` sentinels so the chart shows gaps.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._series = {
            (host, c): deque(maxlen=DEQUE_MAXLEN)
            for host in TPU_HOSTS
            for c in range(NUM_CHIPS)
        }
        # Latest per-chip occupancy + metrics (refreshed every poll cycle).
        # Shape mirrors what tpups.py renders. {state: "?"} until first poll.
        self._status = {
            (host, c): {"state": "?"}
            for host in TPU_HOSTS
            for c in range(NUM_CHIPS)
        }
        self._nodes = {
            host: {"last_updated": None, "fetch_error": None}
            for host in TPU_HOSTS
        }

    def ingest(self, host, ts, status_payload, metrics_payload):
        """Record one poll cycle for one host.

        ts                — shared dashboard-poll timestamp for this cycle
        status_payload    — parsed status.json, or {"_error": ...}
        metrics_payload   — parsed metrics.json, or {"_error": ...}
        """
        # Status.json parse.
        s_devices = None
        s_last = None
        s_err = None
        if "_error" in status_payload:
            s_err = status_payload["_error"]
        else:
            s_devices = status_payload.get("devices", [])
            s_last = status_payload.get("last_updated")

        # Metrics.json parse — schema-version aware.
        m_devices = None
        m_last = None
        m_err = None
        if "_error" in metrics_payload:
            m_err = metrics_payload["_error"]
        elif metrics_payload.get("schema_version") != EXPECTED_SCHEMA:
            m_err = (
                f"unexpected metrics schema_version="
                f"{metrics_payload.get('schema_version')}"
            )
        else:
            m_devices = metrics_payload.get("devices", [])
            m_last = metrics_payload.get("last_updated")

        s_fresh = (s_err is None and s_last is not None
                   and (ts - s_last) <= STALE_AFTER)
        m_fresh = (m_err is None and m_last is not None
                   and (ts - m_last) <= STALE_AFTER)

        # Combine errors and the older of the two last_updated for the
        # node-level summary that drives the status footer.
        node_errs = []
        if s_err: node_errs.append(f"status: {s_err}")
        if m_err: node_errs.append(f"metrics: {m_err}")

        if s_last is not None and m_last is not None:
            node_last = min(s_last, m_last)
        elif s_last is not None:
            node_last = s_last
        elif m_last is not None:
            node_last = m_last
        else:
            node_last = None

        s_by_id = {d.get("id"): d for d in (s_devices or [])}
        m_by_id = {d.get("id"): d for d in (m_devices or [])}

        with self._lock:
            if node_last is not None:
                self._nodes[host]["last_updated"] = node_last
            self._nodes[host]["fetch_error"] = (
                "; ".join(node_errs) if node_errs else None
            )

            for c in range(NUM_CHIPS):
                sd = s_by_id.get(c) if s_fresh else None
                md = m_by_id.get(c) if m_fresh else None

                # ----- Time-series append -----
                if md is not None and md.get("available"):
                    hbm_used = md.get("hbm_used", 0)
                    hbm_total = md.get("hbm_total", 0)
                    hbm_pct = (
                        round(100 * hbm_used / hbm_total, 2)
                        if hbm_total else None
                    )
                    duty = round(md.get("duty_cycle_pct", 0), 2)
                    self._series[(host, c)].append((ts, hbm_pct, duty))
                else:
                    # Idle / warming / error / fetch failure — gap sample.
                    self._series[(host, c)].append((ts, None, None))

                # ----- Latest combined status -----
                self._status[(host, c)] = self._build_status_row(sd, md)

    @staticmethod
    def _build_status_row(sd, md):
        """Combine per-chip status + metrics into a row for the live table."""
        if sd is None:
            return {"state": "?"}
        state = sd.get("state", "?")
        if state != "BUSY":
            return {"state": state}
        row = {
            "state": "BUSY",
            "user": sd.get("user", "-"),
            "pid": sd.get("pid", "-"),
            "time": sd.get("time", "-"),
            "command": sd.get("command", "-"),
            "hbm_pct": None,
            "duty_pct": None,
        }
        if md is not None and md.get("available"):
            hbm_used = md.get("hbm_used", 0)
            hbm_total = md.get("hbm_total", 0)
            row["hbm_pct"] = (
                round(100 * hbm_used / hbm_total, 2) if hbm_total else None
            )
            row["duty_pct"] = round(md.get("duty_cycle_pct", 0), 2)
        return row

    def snapshot(self, since=None):
        """Return (now, nodes_status, series_dict, status_dict).

        If `since` is given, only series points strictly newer than `since`
        are returned. Node status and per-chip status are always the latest.
        """
        now = time.time()
        with self._lock:
            nodes = {}
            for host, info in self._nodes.items():
                last = info["last_updated"]
                stale = (last is None) or (now - last > STALE_AFTER)
                nodes[host] = {
                    "last_updated": last,
                    "stale": stale,
                    "fetch_error": info["fetch_error"],
                }
            series = {}
            for (host, cid), buf in self._series.items():
                if since is None:
                    items = list(buf)
                else:
                    # deque entries are time-ordered; iterate from the right.
                    items = []
                    for entry in reversed(buf):
                        if entry[0] > since:
                            items.append(entry)
                        else:
                            break
                    items.reverse()
                series[f"{host}/{cid}"] = items
            status = {
                f"{host}/{cid}": dict(row)
                for (host, cid), row in self._status.items()
            }
        return now, nodes, series, status


# # #
# POLLING


def fetch(host, endpoint):
    """Adapted from tpups.py:37-49 — same timeout, same error shape."""
    url = f"http://{host}:{PORT_UPSTREAM}/{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        return {"_error": reason}
    except socket.timeout:
        return {"_error": "timeout"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def poll_once(history, executor):
    ts = time.time()  # shared timestamp for every chip in this cycle
    futs = {}
    for host in TPU_HOSTS:
        futs[(host, "status")] = executor.submit(fetch, host, "status.json")
        futs[(host, "metrics")] = executor.submit(fetch, host, "metrics.json")
    for host in TPU_HOSTS:
        status = futs[(host, "status")].result()
        metrics = futs[(host, "metrics")].result()
        history.ingest(host, ts, status, metrics)


def poll_loop(history):
    with ThreadPoolExecutor(
        max_workers=2 * len(TPU_HOSTS), thread_name_prefix="poll"
    ) as ex:
        while True:
            t0 = time.time()
            try:
                poll_once(history, ex)
            except Exception as e:
                # Defensive — don't let a single bad cycle kill the loop.
                print(f"[poll] error: {e}", file=sys.stderr)
            elapsed = time.time() - t0
            time.sleep(max(0.0, INTERVAL - elapsed))


# # #
# HTTP


class Handler(BaseHTTPRequestHandler):
    history = None  # set by main()

    # Suppress per-request logs — at 5s polling × N tabs this would spam
    # journald (same lesson as tpu-heartbeat-web logging to tmpfs).
    def log_message(self, format, *args):
        return

    def _send_static(self, path, content_type, cache=True):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path
        if route in ("/", "/index.html"):
            self._send_static(
                os.path.join(SCRIPT_DIR, "index.html"),
                "text/html; charset=utf-8",
                cache=False,
            )
            return
        if route == "/assets/uPlot.iife.min.js":
            self._send_static(
                os.path.join(SCRIPT_DIR, "uPlot.iife.min.js"),
                "application/javascript",
            )
            return
        if route == "/assets/uPlot.min.css":
            self._send_static(
                os.path.join(SCRIPT_DIR, "uPlot.min.css"),
                "text/css",
            )
            return
        if route == "/api/timeseries":
            qs = urllib.parse.parse_qs(url.query)
            since = None
            if "since" in qs:
                try:
                    since = float(qs["since"][0])
                except ValueError:
                    self.send_error(400, "bad 'since'")
                    return
            now, nodes, series, status = self.history.snapshot(since=since)
            self._send_json({
                "now": now,
                "stale_after": STALE_AFTER,
                "interval": INTERVAL,
                "retention": RETENTION_SEC,
                "nodes": nodes,
                "series": series,
                "status": status,
            })
            return
        self.send_error(404)


# # #
# MAIN


def main():
    history = History()
    Handler.history = history

    poller = threading.Thread(
        target=poll_loop, args=(history,), daemon=True, name="poll-loop",
    )
    poller.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT_LISTEN), Handler)
    print(f"tpu-dashboard listening on 0.0.0.0:{PORT_LISTEN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
