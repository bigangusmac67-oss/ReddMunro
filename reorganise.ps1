# reorganise.ps1 — move the corpora and generated reports out of the
# repo root. Run once, from the repo root, then delete this file.
#
#     powershell -ExecutionPolicy Bypass -File .\reorganise.ps1
#
# WHY. The root currently holds 49 entries. Someone opening the repo
# meets fdic_ratios.csv, nyc_report.html and ten more before reaching
# signal_audit.py. The data is committed on purpose — every scored
# prediction is only checkable if the corpus is here — but it does not
# need to be the first thing anyone sees.
#
# `git mv` rather than `move`, so history follows the files. The code
# that reads these paths already checks data/ first and the repo root
# second, so the build works before and after this runs.

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".git")) {
  Write-Error "Run this from the repository root (no .git here)."
}

New-Item -ItemType Directory -Force -Path "data", "reports" | Out-Null

$corpora = @(
  "act_air_quality.csv", "demo_dashboard.csv",
  "fdic_callreport_2024q1.csv", "fdic_dollars.csv", "fdic_ratios.csv",
  "llm_leaderboard.csv", "mta_subway_otp.csv",
  "nyc_covid_dashboard.csv", "prometheus_infra.csv"
)

$reports = @(
  "act_report.html", "demo_report.html", "demo_dashboard_signal_audit.html",
  "mta_report.html", "nyc_report.html",
  "nyc_covid_dashboard_signal_audit.html", "prom_report.html"
)

foreach ($f in $corpora) {
  if (Test-Path $f) { git mv $f "data/$f"; Write-Host "  data/$f" }
}
foreach ($f in $reports) {
  if (Test-Path $f) { git mv $f "reports/$f"; Write-Host "  reports/$f" }
}

# Scratch files. subset_sum_proto.py is 513 bytes of prototype that the
# shipped detector replaced; triadic_validation.py is the experiment
# behind deferring three-way structure. Kept, because the second is
# referenced as the basis for a decision — just not in the front window.
New-Item -ItemType Directory -Force -Path "notebooks" | Out-Null
foreach ($f in @("subset_sum_proto.py", "triadic_validation.py")) {
  if (Test-Path $f) { git mv $f "notebooks/$f"; Write-Host "  notebooks/$f" }
}

# A Vercel config in a repo that deploys to Cloudflare invites the
# question of what else is stale. wrangler.toml is the live one.
if (Test-Path "demo/vercel.json") {
  git rm -q "demo/vercel.json"
  Write-Host "  removed demo/vercel.json (deploy is Cloudflare; see wrangler.toml)"
}

Write-Host ""
Write-Host "Now verify, then commit:"
Write-Host "  python build_demo.py --check"
Write-Host "  python test_signal_audit.py"
Write-Host "  git add -A; git commit -s -m 'Move corpora to data/, reports to reports/'"
Write-Host ""
Write-Host "Then delete this script: git rm reorganise.ps1"
