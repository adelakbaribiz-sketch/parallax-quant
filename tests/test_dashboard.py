"""The research console's snapshot must come from the real strategy/risk
code, be deterministic per seed, and serialize to strict JSON; the server
must reject bad seeds and refuse path traversal out of its static dir."""

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from parallax_quant.dashboard.server import ConsoleHandler, SnapshotCache
from parallax_quant.dashboard.snapshot import build_snapshot


@pytest.mark.asyncio
async def test_snapshot_is_deterministic_and_strict_json():
    a = await build_snapshot(seed=8)
    b = await build_snapshot(seed=8)
    for snap in (a, b):
        snap["meta"].pop("generated_at")
        snap["meta"].pop("compute_ms")
    assert a == b
    json.dumps(a, allow_nan=False)  # raises on NaN/inf


@pytest.mark.asyncio
async def test_snapshot_sections_reflect_engine_output():
    snap = await build_snapshot(seed=8)
    assert snap["config"]["mode"] == "paper"
    assert snap["config"]["live_trading_authorized"] is False

    sa = snap["stat_arb"]
    assert sa["engle_granger"]["cointegrated"] and sa["johansen"]["cointegrated"]
    assert sa["steps"] and all(abs(s["zscore"]) >= sa["thresholds"]["entry"] for s in sa["signals"])

    cycle = snap["triangular"]["cycles"][0]
    assert cycle["path"][0] == cycle["path"][-1] and len(cycle["path"]) == 4
    assert cycle["net_edge_pct"] < cycle["gross_edge_pct"]  # fees were netted out

    statuses = {r["symbol"]: r["status"] for r in snap["funding"]["rows"]}
    assert statuses["BNB/USDT"] == "skipped"  # negative funding never reaches evaluate()

    risk = snap["risk"]
    assert risk["var"]["cvar_usd"] >= risk["var"]["var_usd"] > 0
    assert {s["scenario"] for s in risk["stress"]} == set(snap_scenarios())


def snap_scenarios():
    from parallax_quant.config import RiskConfig

    return RiskConfig().stress_test_scenarios


@pytest.fixture
def console_url():
    handler = partial(ConsoleHandler, cache=SnapshotCache("config/config.yaml"), default_seed=8)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_server_serves_page_and_snapshot(console_url):
    status, body = _get(console_url + "/")
    assert status == 200 and b"Parallax Quant" in body
    status, body = _get(console_url + "/api/snapshot?seed=8")
    assert status == 200 and json.loads(body)["meta"]["seed"] == 8


@pytest.mark.parametrize("seed", ["abc", "-1", str(2**32)])
def test_server_rejects_invalid_seed(console_url, seed):
    status, _ = _get(console_url + f"/api/snapshot?seed={seed}")
    assert status == 400


def test_server_refuses_path_traversal(console_url):
    status, _ = _get(console_url + "/..%2F..%2Fconfig.py")
    assert status == 404
    status, _ = _get(console_url + "/../server.py")
    assert status == 404
