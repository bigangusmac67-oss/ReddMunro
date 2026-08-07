"""
make_demo.py — generate a realistic demo dashboard with known structure.

Produces `demo_dashboard.csv`: 18 columns of daily SaaS/ops metrics
spanning 400 days, built from FOUR latent drivers plus planted
pathologies. The point is that the file looks like a real dashboard
someone would defend in a meeting, while the correct answer is known
in advance.

Ground truth built in:
  * 4 latent drivers: demand, infrastructure health, marketing spend,
    seasonal effect
  * 2 definitional identities: success_rate / error_rate (complements),
    latency_ms / latency_sec (unit conversion)
  * 1 near-identity: mrr / arr (annualised, x12 plus rounding noise)
  * shared upward trend on most revenue-side metrics, so the raw view
    overstates redundancy and the differenced view corrects it
  * 1 nonlinear pair: churn responds to the SQUARE of latency deviation,
    so it is uncorrelated with latency but fully dependent on it
  * 2 genuinely independent metrics: support_tickets_misc, nps_noise

Expected verdict: ~18 columns, roughly 4-6 independent signals.

    python make_demo.py
"""

import csv
import os

import numpy as np


def _default_out():
    """Write into data/ when it exists, so regenerating the fixture does
    not scatter a corpus back into the repo root."""
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(here, "data")
    return os.path.join(d if os.path.isdir(d) else here, "demo_dashboard.csv")


def main(path=None, n=400, seed=11):
    path = path or _default_out()
    rng = np.random.default_rng(seed)
    t = np.arange(n)

    # ---- four latent drivers ----------------------------------------
    demand = np.cumsum(rng.standard_normal(n) * 0.8) + 0.02 * t
    infra = np.cumsum(rng.standard_normal(n) * 0.6)
    spend = np.cumsum(rng.standard_normal(n) * 0.5) + 0.01 * t
    season = 3.0 * np.sin(2 * np.pi * t / 91.0)

    def noisy(x, s=0.05):
        return x + s * np.std(x) * rng.standard_normal(n)

    # ---- observable metrics -----------------------------------------
    sessions = 5000 + 400 * noisy(demand) + 120 * season
    signups = 60 + 8 * noisy(demand) + 3 * noisy(spend) + 2 * season
    trials = 0.62 * signups + noisy(signups, 0.08) * 0.1
    mrr = 48000 + 1400 * noisy(demand) + 700 * noisy(spend) + 30 * t
    arr = mrr * 12 + rng.normal(0, 40, n)              # near-identity
    cac = 240 - 6 * noisy(spend) + rng.normal(0, 4, n)

    latency_ms = 180 - 22 * noisy(infra) + 4 * season
    latency_sec = latency_ms / 1000.0                   # exact identity
    p99_latency = latency_ms * 3.4 + rng.normal(0, 12, n)
    cpu_util = 44 - 5.5 * noisy(infra) + rng.normal(0, 2, n)
    error_rate = np.clip(0.02 - 0.004 * noisy(infra), 0.0005, 0.5)
    success_rate = 1.0 - error_rate                     # exact complement
    uptime_pct = 100 * (1 - error_rate * 0.35) + rng.normal(0, 0.02, n)

    # nonlinear: churn tracks the SQUARE of latency deviation, so it is
    # near-uncorrelated with latency but entirely determined by it
    dev = (latency_ms - latency_ms.mean()) / latency_ms.std()
    churn_pct = 2.1 + 0.55 * dev ** 2 + rng.normal(0, 0.06, n)

    active_users = 0.31 * sessions + 40 * season + rng.normal(0, 60, n)
    support_tickets = 90 + rng.standard_normal(n).cumsum() * 1.2  # own driver
    nps = 42 + rng.standard_normal(n) * 3.0                       # noise only

    cols = {
        "date": [f"2024-{1 + (i // 30) % 12:02d}-{1 + i % 28:02d}"
                 for i in range(n)],
        "sessions": sessions,
        "signups": signups,
        "trials_started": trials,
        "active_users": active_users,
        "mrr": mrr,
        "arr": arr,
        "cac": cac,
        "latency_ms": latency_ms,
        "latency_sec": latency_sec,
        "p99_latency_ms": p99_latency,
        "cpu_util_pct": cpu_util,
        "error_rate": error_rate,
        "success_rate": success_rate,
        "uptime_pct": uptime_pct,
        "churn_pct": churn_pct,
        "support_tickets": support_tickets,
        "nps": nps,
    }

    names = list(cols)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(names)
        for i in range(n):
            row = []
            for k in names:
                v = cols[k][i]
                row.append(v if isinstance(v, str) else f"{v:.4f}")
            w.writerow(row)
    print(f"wrote {path}: {n} rows x {len(names)} columns "
          f"({len(names) - 1} metrics + date)")
    print("ground truth: 4 latent drivers, 2 exact identities, "
          "1 near-identity, 1 nonlinear pair, 2 independent metrics")


if __name__ == "__main__":
    main()
