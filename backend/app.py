"""
app.py — HTTP transport. FastAPI adapter over service.py + jobs.py.

Deliberately thin: this file contains no analysis and no policy. If a
rule about tiers, quotas or contract shape appears here, it is in the
wrong place — it belongs in service.py, where it can be tested without
a web client.

    pip install fastapi uvicorn python-multipart
    uvicorn app:api --reload

AUTH is stubbed at `resolve_account`. It returns a development account
so the service is runnable, and it is the single seam where a real
identity provider plugs in. It must be replaced before deployment; the
stub is loud about that rather than silently permissive.
"""

from __future__ import annotations

import os
import tempfile

try:
    from fastapi import (FastAPI, File, Form, HTTPException, Header,
                         UploadFile)
    from fastapi.responses import JSONResponse, PlainTextResponse
except ImportError as exc:                            # pragma: no cover
    raise SystemExit(
        "FastAPI is not installed. This adapter is optional; the service "
        "layer (service.py) and job layer (jobs.py) have no web "
        "dependencies and can be driven from any framework.\n"
        "  pip install fastapi uvicorn python-multipart") from exc

import cost as COST
import exports as EXP
import schemas as SCH
import jobs as JOBS
import service as SVC

ERR = {"model": SCH.ErrorResponse}

api = FastAPI(
    title="Signal Audit API",
    version=SVC.CONTRACT_VERSION,
    description=(
        "Measures how many independent signals a set of metrics actually "
        "carries, and which columns can be removed without losing "
        "information.\n\n"
        "**Headline figures use first-differenced series.** Metrics that all "
        "grow over time correlate from shared trend alone; the raw view is "
        "returned alongside so the gap is visible.\n\n"
        "**Executable exports are gated.** The engine proves statistical "
        "redundancy; it cannot see that a metric is the sole condition on a "
        "paging monitor. A completed blast-radius worksheet is required "
        "before Terraform or exclusion plans are returned.\n\n"
        "**No cost figure is synthesised.** Column redundancy and billing "
        "cardinality differ by orders of magnitude, so savings estimates "
        "require customer-supplied unit cost and, ideally, a cardinality map."
    ),
    openapi_tags=[
        {"name": "audits", "description": "Run and retrieve audits."},
        {"name": "exports", "description":
            "Cleanup artefacts. Nothing generated is destructive: "
            "Terraform collapses cardinality via tag configuration and is "
            "reversed by restoring tags."},
        {"name": "billing", "description": "Tiers, quota and consumption."},
        {"name": "system", "description": "Health and metadata."},
    ])
RUNNER = JOBS.AuditRunner(max_workers=2)

MAX_UPLOAD_BYTES = 256 * 1024 * 1024


class Account:
    def __init__(self, account_id: str, tier: str):
        self.account_id, self.tier = account_id, tier


def resolve_account(authorization: str | None) -> Account:
    """STUB — replace with real auth before deployment.

    Returning a fixed development account keeps the service runnable
    end-to-end. It also means anyone who reaches this endpoint is billed
    as 'dev' and granted Pro limits, which is why this must not ship.
    """
    if os.environ.get("SIGNAL_AUDIT_REQUIRE_AUTH") == "1":
        if not authorization:
            raise HTTPException(401, "missing credentials")
        raise HTTPException(501, "auth provider not configured")
    return Account("dev-account", "pro")


async def _spool(upload: UploadFile) -> str:
    """Stream the upload to a temp file, enforcing a hard size cap.

    Streamed rather than read into memory: an unbounded read is a
    trivial denial-of-service, and the engine wants a path anyway.
    """
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="sa_upload_")
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(1 << 20):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
                out.write(chunk)
    except Exception:
        os.unlink(path)
        raise
    return path


def _err(exc: Exception, status: int) -> JSONResponse:
    payload = exc.to_dict() if hasattr(exc, "to_dict") else {
        "error": "internal_error"}
    return JSONResponse(status_code=status, content=payload)


# ----------------------------------------------------------------------
@api.get("/v1/health", tags=["system"], summary="Liveness and versions")
def health():
    return {"status": "ok", "contract_version": SVC.CONTRACT_VERSION,
            "engine_version": SVC.ENGINE_VERSION}


@api.get("/v1/tiers", tags=["billing"], summary="Published subscription limits")
def tiers():
    return {"tiers": [
        {"name": t.name, "max_metrics": t.max_metrics,
         "max_rows": t.max_rows, "max_cells": t.max_cells,
         "max_audits_per_month": t.max_audits_per_month,
         "retention_days": t.retention_days,
         "async_above_cells": t.async_only_above_cells}
        for t in SVC.TIERS.values()]}


@api.post("/v1/audits/preflight", tags=["audits"],
          response_model=SCH.PreflightResponse,
          responses={422: ERR},
          summary="Shape, quota verdict and execution mode without analysing")
async def preflight(file: UploadFile = File(...),
                    ignore: str = Form(""),
                    authorization: str | None = Header(None)):
    """Shape, quota verdict and execution mode WITHOUT running the
    analysis. Lets the client show limits and set expectations before
    committing to a long job."""
    acct = resolve_account(authorization)
    path = await _spool(file)
    try:
        ig = [s for s in ignore.split(",") if s]
        metrics, rows, cells = SVC.measure(path, ignore=ig)
        tier = SVC.TIERS[acct.tier]
        try:
            SVC.enforce(tier, metrics, rows)
            within = True
            quota = None
        except SVC.QuotaExceeded as exc:
            within, quota = False, exc.to_dict()
        return {"metrics": metrics, "rows": rows, "cells": cells,
                "rows_per_metric": round(rows / metrics, 1) if metrics else 0,
                "within_quota": within, "quota_error": quota,
                "recommended_mode":
                    "async" if SVC.should_run_async(acct.tier, metrics, rows)
                    else "sync",
                "tier": acct.tier}
    except SVC.InvalidInput as exc:
        return _err(exc, 422)
    except ValueError as exc:
        return _err(SVC.InvalidInput(str(exc)), 422)
    finally:
        os.unlink(path)


@api.post("/v1/audits", tags=["audits"],
          responses={200: {"model": SCH.SyncAuditResponse},
                     202: {"model": SCH.AsyncAuditResponse},
                     402: ERR, 422: ERR},
          summary="Run an audit (inline when small, queued when large)")
async def create_audit(file: UploadFile = File(...),
                       ignore: str = Form(""),
                       mode: str = Form("auto"),
                       scale_by: str = Form(""),
                       scale_exempt: str = Form(""),
                       basis: str = Form(""),
                       require_basis: bool = Form(False),
                       authorization: str | None = Header(None)):
    """Run an audit.

    mode=auto (default) runs inline when small and queues when large,
    per the tier's threshold. The response always states which happened,
    so a client never has to infer it from the shape of the body.

    Basis declaration. `basis` names which lens the headline reports:
    raw, differenced, or ratio:COL. `scale_by` is a comma-separated list
    of denominators, each adding one ratio basis; `scale_exempt` names
    columns that are ALREADY scale-free and must not be divided —
    dividing a rate by a size injects the very confound the ratio basis
    removes. All optional: omitting them reproduces the previous
    response exactly, and the contract reports `basis.declared: false`
    so a client can enforce what the server does not.

    `require_basis=true` makes the server refuse an undeclared request.
    It is opt-in rather than default because defaulting it on would
    break every existing caller.
    """
    acct = resolve_account(authorization)
    path = await _spool(file)
    ig = [s for s in ignore.split(",") if s]
    sb = [s.strip() for s in scale_by.split(",") if s.strip()]
    sx = [s.strip() for s in scale_exempt.split(",") if s.strip()]
    bs = basis.strip() or None

    try:
        metrics, rows, _ = SVC.measure(path, ignore=ig)
    except (ValueError, FileNotFoundError) as exc:
        os.unlink(path)
        return _err(SVC.InvalidInput(str(exc)), 422)

    go_async = (mode == "async" or
                (mode == "auto" and
                 SVC.should_run_async(acct.tier, metrics, rows)))

    if go_async:
        try:
            job = RUNNER.submit(path, account_id=acct.account_id,
                                tier=acct.tier, ignore=ig,
                                scale_by=sb, scale_exempt=sx, basis=bs,
                                require_basis=require_basis)
        except SVC.QuotaExceeded as exc:
            os.unlink(path)
            return _err(exc, 402)          # payment required
        except SVC.InvalidInput as exc:
            os.unlink(path)
            return _err(exc, 422)
        return JSONResponse(status_code=202, content={
            "mode": "async", "job_id": job.id, "status": job.status,
            "poll": f"/v1/audits/{job.id}"})

    try:
        result = SVC.run_audit(path, tier=acct.tier, ignore=ig,
                               scale_by=sb, scale_exempt=sx, basis=bs,
                               require_basis=require_basis)
        return {"mode": "sync", "status": "succeeded", "result": result}
    except SVC.QuotaExceeded as exc:
        return _err(exc, 402)
    except SVC.InvalidInput as exc:
        return _err(exc, 422)
    finally:
        if os.path.exists(path):
            os.unlink(path)


@api.get("/v1/audits/{job_id}", tags=["audits"],
         response_model=SCH.JobResponse, responses={404: ERR},
         summary="Job status, and the contract once complete")
def get_audit(job_id: str, authorization: str | None = Header(None)):
    acct = resolve_account(authorization)
    job = RUNNER.store.get(job_id)
    if job is None or job.account_id != acct.account_id:
        # same response for missing and not-yours: job ids should not be
        # probeable across accounts
        raise HTTPException(404, "job not found")
    return job.to_dict(include_result=job.status == JOBS.SUCCEEDED)


@api.get("/v1/audits", tags=["audits"], summary="List this account's audits")
def list_audits(limit: int = 50,
                authorization: str | None = Header(None)):
    acct = resolve_account(authorization)
    return {"jobs": [j.to_dict(include_result=False)
                     for j in RUNNER.store.list_for_account(
                         acct.account_id, limit=limit)]}


@api.get("/v1/usage", tags=["billing"], summary="Consumption against the tier allowance")
def usage(authorization: str | None = Header(None)):
    """Consumption for the current account. Sums the metering unit
    across completed audits; a real deployment reads this from the
    billing store rather than from job memory."""
    acct = resolve_account(authorization)
    js = RUNNER.store.list_for_account(acct.account_id, limit=10_000)
    done = [j for j in js if j.status == JOBS.SUCCEEDED and j.result]
    cells = sum(j.result["usage"]["billable_cells"] for j in done)
    tier = SVC.TIERS[acct.tier]
    return {"tier": acct.tier, "audits_completed": len(done),
            "audits_allowed": tier.max_audits_per_month,
            "billable_cells": cells,
            "audits_failed": sum(1 for j in js if j.status == JOBS.FAILED)}


# ----------------------------------------------------------------------
# exports
# ----------------------------------------------------------------------
def _result_for(job_id: str, acct: Account) -> dict:
    job = RUNNER.store.get(job_id)
    if job is None or job.account_id != acct.account_id:
        raise HTTPException(404, "job not found")
    if job.status != JOBS.SUCCEEDED or not job.result:
        raise HTTPException(409, f"job is {job.status}, not succeeded")
    return job.result


@api.get("/v1/audits/{job_id}/exports", tags=["exports"],
         response_model=SCH.ExportAvailability, responses={404: ERR},
         summary="What can be downloaded now, and what is gated")
def list_exports(job_id: str, authorization: str | None = Header(None)):
    """What can be downloaded now, and what is still gated behind the
    blast-radius attestation."""
    acct = resolve_account(authorization)
    return EXP.available_exports(_result_for(job_id, acct))


@api.get("/v1/audits/{job_id}/exports/review-worksheet", tags=["exports"],
         response_class=PlainTextResponse, responses={404: ERR, 409: ERR},
         summary="Blast-radius worksheet (CSV) — request this first")
def export_worksheet(job_id: str,
                     authorization: str | None = Header(None)):
    """The artefact that should be requested first: statistical evidence
    pre-filled, operational columns blank for the operator."""
    acct = resolve_account(authorization)
    csv_text = EXP.review_worksheet(_result_for(job_id, acct))
    return PlainTextResponse(csv_text, media_type="text/csv", headers={
        "Content-Disposition":
            f'attachment; filename="signal_audit_review_{job_id}.csv"'})


@api.get("/v1/audits/{job_id}/exports/column-manifest", tags=["exports"],
         response_class=PlainTextResponse, responses={404: ERR, 409: ERR},
         summary="Keep/drop decision per column (CSV), ungated")
def export_manifest(job_id: str,
                    authorization: str | None = Header(None)):
    """Keep/drop per column. A document, not a change — ungated."""
    acct = resolve_account(authorization)
    csv_text = EXP.column_manifest(_result_for(job_id, acct))
    return PlainTextResponse(csv_text, media_type="text/csv", headers={
        "Content-Disposition":
            f'attachment; filename="column_manifest_{job_id}.csv"'})


@api.post("/v1/audits/{job_id}/exports/terraform", tags=["exports"],
          response_class=PlainTextResponse,
          responses={404: ERR, 409: ERR, 428: ERR},
          summary="Reversible Terraform; requires a completed worksheet")
async def export_terraform(job_id: str,
                           worksheet: UploadFile = File(...),
                           namespace: str = Form(""),
                           authorization: str | None = Header(None)):
    """Reversible Terraform, unlocked by a completed review worksheet.

    Requires the worksheet as an upload rather than a checkbox: the
    attestation is per metric, and a single 'I confirm' control cannot
    represent that a reviewer checked eleven metrics against four
    systems each.
    """
    acct = resolve_account(authorization)
    result = _result_for(job_id, acct)
    text = (await worksheet.read()).decode("utf-8-sig", errors="replace")
    try:
        atts = EXP.parse_worksheet(text)
        hcl = EXP.datadog_terraform(result, atts, namespace=namespace)
    except EXP.ExportGated as exc:
        return JSONResponse(status_code=428, content=exc.to_dict())
    return PlainTextResponse(hcl, media_type="text/plain", headers={
        "Content-Disposition":
            f'attachment; filename="signal_audit_{job_id}.tf"'})


@api.post("/v1/audits/{job_id}/exports/datadog-exclusions", tags=["exports"],
          responses={404: ERR, 409: ERR, 428: ERR},
          summary="Reversible exclusion plan (JSON); requires a worksheet")
async def export_exclusions(job_id: str,
                            worksheet: UploadFile = File(...),
                            namespace: str = Form(""),
                            authorization: str | None = Header(None)):
    acct = resolve_account(authorization)
    result = _result_for(job_id, acct)
    text = (await worksheet.read()).decode("utf-8-sig", errors="replace")
    try:
        atts = EXP.parse_worksheet(text)
        return EXP.datadog_exclusion_json(result, atts, namespace=namespace)
    except EXP.ExportGated as exc:
        return JSONResponse(status_code=428, content=exc.to_dict())


@api.post("/v1/audits/{job_id}/cost-estimate", tags=["billing"],
          response_model=SCH.CostEstimate, responses={404: ERR, 422: ERR},
          summary="Re-price an audit against your own unit cost")
def cost_estimate(job_id: str, model: SCH.CostModelIn,
                  authorization: str | None = Header(None)):
    """Re-price an existing audit against customer-supplied parameters.

    Separate from the audit itself so a customer can adjust unit cost and
    cardinality without paying for a re-analysis.
    """
    acct = resolve_account(authorization)
    result = _result_for(job_id, acct)
    try:
        cm = COST.model_from_request(model.model_dump())
    except ValueError as exc:
        return JSONResponse(status_code=422, content={
            "error": "invalid_input", "detail": str(exc)})
    return COST.estimate(result, cm)


@api.on_event("shutdown")
def _shutdown():
    RUNNER.stop()
