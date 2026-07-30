# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""FastAPI HTTP layer — /healthz, /metrics, /run/<check>."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response

from runner import checks, metrics


def build_app(
    *,
    check_argv: dict[str, list[str]],
    check_timeout_s: int,
) -> FastAPI:
    app = FastAPI(title="gli-compliance-runner", version="0.1.0")
    registry = metrics.fresh_registry()
    metrics.preregister(registry)
    app.state.registry = registry
    app.state.check_argv = check_argv
    app.state.check_timeout_s = check_timeout_s

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics_endpoint() -> Response:
        return Response(
            content=metrics.render(registry),
            media_type="text/plain; version=0.0.4",
        )

    @app.post("/run/{name}")
    def run(name: str) -> dict[str, object]:
        argv = check_argv.get(name)
        if argv is None or name not in checks.registry():
            raise HTTPException(status_code=404, detail=f"unknown check: {name}")
        result = checks.run_check(
            name=name,
            argv=argv,
            env={},
            timeout_s=check_timeout_s,
        )
        metrics.record(registry, result)
        return {
            "check": result.name,
            "success": result.success,
            "return_code": result.return_code,
            "duration_s": round(result.duration_s, 3),
            "timed_out": result.timed_out,
        }

    return app
