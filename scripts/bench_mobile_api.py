"""
Repeatable latency benchmark for POST /mobile/analyze.

Measures the full request path EXCEPT the actual vision-provider network
call (Claude vision API) — that leg requires a real ANTHROPIC_API_KEY and
network access, and its latency is dominated by Anthropic's infrastructure,
not this codebase.  The vision provider is stubbed with a fixed-latency
double so the reported numbers isolate what this project controls: image
validation, JSON parsing/state validation, the vision-bridge translation,
and the full equity -> EV -> decision engine.

To benchmark the real end-to-end path including the live vision API, run
this same request against a running server with curl/hey/wrk and a real
screenshot — that is an infrastructure/network measurement, not something
this script can honestly simulate.

Usage:
    python scripts/bench_mobile_api.py [--n 50] [--vision-latency-ms 900]
"""
from __future__ import annotations

import argparse
import io
import statistics
import sys
import time

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

from PIL import Image

import routes.mobile as mobile_route
from vision.validator import ValidationResult


def _png_bytes(size=(1080, 1920)) -> bytes:
    """A screenshot-scale solid PNG — representative of upload payload size,
    not of decode complexity (a real screenshot has more detail/entropy)."""
    buf = io.BytesIO()
    Image.new("RGB", size, color=(30, 40, 50)).save(buf, format="PNG")
    return buf.getvalue()


class _LatentStubAnalyzer:
    """Stands in for the real Claude vision call with a fixed artificial
    delay, so the benchmark's vision_ms column is explicit and controlled
    rather than silently zero (which would understate real latency) or
    dependent on network access this environment doesn't have."""

    def __init__(self, result: ValidationResult, latency_s: float):
        self._result  = result
        self._latency = latency_s

    def analyze(self, image_data: bytes, mime_type: str) -> ValidationResult:
        time.sleep(self._latency)
        return self._result


_SCENARIOS = [
    ("preflop_open",  {"hero_cards": ["Ah", "As"], "board": [], "pot": 1.5,
                       "call_amount": 0.0, "hero_stack": 100.0,
                       "hero_position": "UTG", "player_count": 6}),
    ("flop_cbet",     {"hero_cards": ["Kh", "Kd"], "board": ["9h", "4c", "2s"], "pot": 20.0,
                       "call_amount": 0.0, "hero_stack": 180.0,
                       "hero_position": "BTN", "player_count": 2}),
    ("river_fold",    {"hero_cards": ["7h", "2d"], "board": ["Kc", "9s", "4h", "Jd", "3c"],
                       "pot": 200.0, "call_amount": 180.0, "hero_stack": 400.0,
                       "hero_position": "BB", "player_count": 2}),
    ("river_allin",   {"hero_cards": ["Ah", "Ks"], "board": ["Ad", "7c", "2h", "9s", "3d"],
                       "pot": 100.0, "call_amount": 100.0, "hero_stack": 100.0,
                       "hero_position": "BTN", "player_count": 2}),
]


def _percentile(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def run_benchmark(n: int, vision_latency_ms: float) -> None:
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    image_bytes = _png_bytes()
    totals: list = []
    per_stage: dict = {"image_prep_ms": [], "vision_ms": [], "bridge_ms": [], "engine_ms": []}

    mobile_route._DEBUG_TIMING = True
    orig_analyzer = mobile_route._analyzer
    try:
        for i in range(n):
            name, fields = _SCENARIOS[i % len(_SCENARIOS)]
            state = {
                "hero_cards_confidence": 0.95, "overall_confidence": 0.9,
                "board_confidence": 0.9, **fields,
            }
            mobile_route._analyzer = _LatentStubAnalyzer(
                ValidationResult(valid=True, data=state), vision_latency_ms / 1000.0,
            )

            # The route is rate-limited (10/min per IP) by design — real
            # traffic is many distinct phones, not one client hammering the
            # endpoint, so spread requests across synthetic source IPs the
            # same way a real fleet of devices would present.
            t0 = time.perf_counter()
            r = client.post(
                "/mobile/analyze",
                data={"image": (io.BytesIO(image_bytes), "table.png")},
                content_type="multipart/form-data",
                environ_base={"REMOTE_ADDR": f"10.{(i // 8) % 250}.{i % 250}.1"},
            )
            wall_ms = (time.perf_counter() - t0) * 1000.0

            if r.status_code != 200:
                print(f"  [{i}] {name}: FAILED status={r.status_code} body={r.get_json()}")
                continue

            totals.append(wall_ms)
            timings = r.get_json().get("_timings", {})
            for k in per_stage:
                if k in timings:
                    per_stage[k].append(timings[k])
    finally:
        mobile_route._analyzer = orig_analyzer
        mobile_route._DEBUG_TIMING = False

    if not totals:
        print("No successful requests — nothing to report.")
        return

    totals_sorted = sorted(totals)
    print(f"\nPOST /mobile/analyze — {len(totals)} requests "
          f"(vision provider stubbed at {vision_latency_ms:.0f} ms fixed latency)\n")
    print(f"{'metric':<12}{'avg':>10}{'median':>10}{'p95':>10}{'p99':>10}")
    print(f"{'total_ms':<12}"
          f"{statistics.mean(totals_sorted):>10.1f}"
          f"{statistics.median(totals_sorted):>10.1f}"
          f"{_percentile(totals_sorted, 0.95):>10.1f}"
          f"{_percentile(totals_sorted, 0.99):>10.1f}")
    for stage, vals in per_stage.items():
        if not vals:
            continue
        vs = sorted(vals)
        print(f"{stage:<12}"
              f"{statistics.mean(vs):>10.1f}"
              f"{statistics.median(vs):>10.1f}"
              f"{_percentile(vs, 0.95):>10.1f}"
              f"{_percentile(vs, 0.99):>10.1f}")

    print(
        "\nNote: vision_ms above is the artificial stub delay, not a "
        "measurement of the real Claude vision API. That leg requires a "
        "live ANTHROPIC_API_KEY + network call and must be benchmarked "
        "against a running server, not simulated here. engine_ms is the "
        "real equity->EV->decision computation (quick mode)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=50, help="number of requests to sample")
    parser.add_argument("--vision-latency-ms", type=float, default=900.0,
                        help="fixed stub latency standing in for the real vision API call")
    args = parser.parse_args()
    run_benchmark(args.n, args.vision_latency_ms)
