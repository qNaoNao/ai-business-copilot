"""Token-protected, single-workspace metrics API. No paid-model endpoints.

Run locally: python -m uvicorn src.api:create_app --factory --host 127.0.0.1
Requires COPILOT_API_TOKEN (32+ ASCII characters). Not Internet-ready auth/SSO.
"""
from collections import deque
import hmac
import json
import os
import sqlite3
import threading
import time
from uuid import uuid4

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.analysis_store import AnalysisStore
from src.semantic import catalog, compile_query


def create_app(*, token=None, viewer_token=None, store_path=None, requests_per_minute=60):
    analyst = token if token is not None else os.getenv("COPILOT_API_TOKEN", "")
    viewer = viewer_token if viewer_token is not None else os.getenv("COPILOT_VIEWER_TOKEN", "")
    if (not isinstance(analyst, str) or len(analyst) < 32 or not analyst.isascii()
            or not isinstance(viewer, str)
            or (viewer and (len(viewer) < 32 or not viewer.isascii() or viewer == analyst))):
        raise ValueError("Configure distinct 32+ character ASCII API tokens; API is disabled without a token")
    if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 600:
        raise ValueError("requests_per_minute must be between 1 and 600")
    store = AnalysisStore(store_path)
    windows, lock = {"analyst": deque(), "viewer": deque()}, threading.Lock()
    slots = threading.BoundedSemaphore(4)

    def response(data, status=200):
        return JSONResponse(data, status_code=status, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def body(request):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type must be application/json")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16_384:
                raise ValueError("Request exceeds 16 KB")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid JSON") from None
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def operate(request, payload):
        path = request.url.path
        if path == "/v1/catalog":
            return catalog()
        if path == "/v1/query/preview":
            plan = compile_query(payload)
            return {"spec": plan.spec, "sql": plan.sql, "parameters": plan.parameters, "query_id": plan.query_id}
        if path == "/v1/query":
            return store.run(payload)
        if path == "/v1/audit":
            return store.stats()
        if path == "/v1/analyses":
            if request.method == "GET":
                return {"analyses": store.list()}
            if set(payload) != {"title", "query"}:
                raise ValueError("Supply title and query only, not client-generated results")
            if not isinstance(payload["title"], str) or not 1 <= len(payload["title"].strip()) <= 100:
                raise ValueError("Title must contain 1–100 characters")
            result = store.run(payload["query"])
            identifier = store.save(payload["title"], result)
            return store.get(identifier)
        identifier = request.path_params["identifier"]
        if path.endswith("/replay"):
            if payload:
                raise ValueError("Replay accepts an empty object; the saved query is reused")
            return store.replay(identifier)
        if path.endswith("/feedback"):
            if set(payload) - {"verdict", "note"} or "verdict" not in payload:
                raise ValueError("Supply verdict and optional note only")
            store.review(identifier, payload["verdict"], payload.get("note", ""))
            return {"saved": True}
        return store.get(identifier)

    async def endpoint(request: Request):
        authorization = request.headers.get("authorization", "")
        secret = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not secret.isascii():
            secret = ""
        role = "analyst" if hmac.compare_digest(secret, analyst) else (
            "viewer" if viewer and hmac.compare_digest(secret, viewer) else None)
        if role is None:
            return response({"error": "unauthorized"}, 401)
        if role == "viewer" and (request.url.path == "/v1/audit" or (
                request.method == "POST" and request.url.path not in {"/v1/query", "/v1/query/preview"})):
            return response({"error": "forbidden"}, 403)
        with lock:
            now, queue = time.monotonic(), windows[role]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= requests_per_minute:
                return response({"error": "rate_limit_exceeded"}, 429)
            queue.append(now)
        if not slots.acquire(blocking=False):
            return response({"error": "service_busy"}, 503)
        request_id = uuid4().hex
        try:
            payload = await body(request) if request.method == "POST" else {}
            result = await run_in_threadpool(operate, request, payload)
            return response({"request_id": request_id, "data": result})
        except KeyError:
            return response({"request_id": request_id, "error": "not_found"}, 404)
        except ValueError as exc:
            return response({"request_id": request_id, "error": str(exc)}, 400)
        except TimeoutError:
            return response({"request_id": request_id, "error": "query_budget_exceeded"}, 408)
        except (OSError, sqlite3.Error, RuntimeError):
            # No raw SQL, database paths, token values or tracebacks in HTTP errors.
            return response({"request_id": request_id, "error": "data_service_unavailable"}, 503)
        finally:
            slots.release()

    async def health(request):
        return response({"status": "ok", "service": "copilot-metrics", "paid_model_endpoints": False})

    return Starlette(debug=False, routes=[
        Route("/healthz", health),
        Route("/v1/catalog", endpoint),
        Route("/v1/query/preview", endpoint, methods=["POST"]),
        Route("/v1/query", endpoint, methods=["POST"]),
        Route("/v1/analyses", endpoint, methods=["GET", "POST"]),
        Route("/v1/analyses/{identifier}", endpoint),
        Route("/v1/analyses/{identifier}/replay", endpoint, methods=["POST"]),
        Route("/v1/analyses/{identifier}/feedback", endpoint, methods=["POST"]),
        Route("/v1/audit", endpoint),
    ])
