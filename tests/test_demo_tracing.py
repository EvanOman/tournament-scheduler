"""End-to-end coverage for the demo API's OTLP tracing.

These tests drive the real exporter -- queue, worker thread, JSON encoding --
with only the HTTP transport replaced, so a payload that a collector would
reject fails here too.

The load-bearing properties, in order of how badly a regression would hurt:

1. A valid inbound ``traceparent`` continues the edge's trace.
2. A missing or malformed one starts a fresh trace instead of erroring.
3. Nothing telemetry does can fail a user request -- including a collector that
   is down, hanging, or returning 500.
4. Nothing recorded identifies a person or a tournament.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from demo.api import tracing

VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
VALID_SPAN_ID = "00f067aa0ba902b7"
VALID_TRACEPARENT = f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"

SOLVE_BODY = {
    "days": 1,
    "divisions": [
        {
            "name": "Wildcats U12",
            "teams": 4,
            "game_minutes": 40,
            "games_per_team": 2,
            "min_rest_minutes": 30,
        }
    ],
    "fields": [{"name": "Riverside Park", "open": "08:00", "close": "18:00"}],
}


class _Collector:
    """Stands in for the OTLP receiver; records every exported span."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.status = 200
        self.error: Exception | None = None

    # -- fake urlopen --

    def urlopen(self, request: Any, timeout: float | None = None) -> Any:
        if self.error is not None:
            raise self.error
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(self.status)

    # -- assertions --

    @property
    def spans(self) -> list[dict[str, Any]]:
        return [
            span
            for payload in self.payloads
            for resource_span in payload["resourceSpans"]
            for scope_span in resource_span["scopeSpans"]
            for span in scope_span["spans"]
        ]

    def resource_attrs(self) -> dict[str, Any]:
        return _flatten(self.payloads[0]["resourceSpans"][0]["resource"]["attributes"])

    def wait(self, count: int = 1, timeout: float = 5.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(self.spans) < count:
            time.sleep(0.02)
        return self.spans

    def named(self, name: str) -> dict[str, Any] | None:
        return next((span for span in self.spans if span["name"] == name), None)

    def await_named(self, name: str, timeout: float = 5.0) -> dict[str, Any]:
        """Block until a span with this name is exported. Spans arrive in
        batches, so "the first span I see" is not a safe way to find one."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            span = self.named(name)
            if span is not None:
                return span
            time.sleep(0.02)
        raise AssertionError(f"no {name!r} span exported; saw {[s['name'] for s in self.spans]}")


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _flatten(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    """OTLP attribute list -> plain dict, unwrapping the typed value envelope."""
    out: dict[str, Any] = {}
    for attribute in attributes:
        ((kind, value),) = attribute["value"].items()
        out[attribute["key"]] = int(value) if kind == "intValue" else value
    return out


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    return _flatten(span.get("attributes", []))


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Collector]:
    """Point the process-wide exporter at an in-memory collector."""
    original_tracer = tracing._TRACER
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example/otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_AUTHORIZATION", "Basic dGVzdA==")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    sink = _Collector()
    monkeypatch.setattr(tracing.urllib.request, "urlopen", sink.urlopen)
    # Own the tracer rather than relying on another test to import demo.api.main.
    # This makes direct selections of the ASGI middleware tests representative.
    tracing.reset_for_tests()
    tracing.configure("tourneydesk")
    try:
        yield sink
    finally:
        tracing.flush(timeout=1)
        tracing._TRACER = original_tracer


@pytest.fixture
def client() -> TestClient:
    from demo.api.main import app

    return TestClient(app)


# --- W3C traceparent parsing -------------------------------------------------


def test_parse_traceparent_accepts_a_valid_header() -> None:
    context = tracing.parse_traceparent(VALID_TRACEPARENT)

    assert context is not None
    assert context.trace_id == VALID_TRACE_ID
    assert context.span_id == VALID_SPAN_ID
    assert context.sampled is True


def test_parse_traceparent_is_case_insensitive() -> None:
    context = tracing.parse_traceparent(VALID_TRACEPARENT.upper())

    assert context is not None
    assert context.trace_id == VALID_TRACE_ID


def test_parse_traceparent_reads_the_unsampled_flag() -> None:
    context = tracing.parse_traceparent(f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-00")

    assert context is not None
    assert context.sampled is False


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("missing", None),
        ("empty", ""),
        ("whitespace", "   "),
        ("no separators", "not-a-traceparent"),
        ("too few fields", f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}"),
        ("all-zero trace id", f"00-{'0' * 32}-{VALID_SPAN_ID}-01"),
        ("all-zero span id", f"00-{VALID_TRACE_ID}-{'0' * 16}-01"),
        ("short trace id", f"00-{'a' * 31}-{VALID_SPAN_ID}-01"),
        ("long trace id", f"00-{'a' * 33}-{VALID_SPAN_ID}-01"),
        ("non-hex trace id", f"00-{'z' * 32}-{VALID_SPAN_ID}-01"),
        ("short span id", f"00-{VALID_TRACE_ID}-{'a' * 15}-01"),
        ("non-hex flags", f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-zz"),
        ("forbidden version ff", f"ff-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"),
        ("v00 with extra field", f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01-extra"),
        ("future version, empty field", f"01-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01-"),
        ("oversized", "00-" + "a" * 300),
    ],
)
def test_parse_traceparent_rejects_invalid_headers(label: str, value: str | None) -> None:
    assert tracing.parse_traceparent(value) is None, label


def test_parse_traceparent_accepts_a_future_version_with_extra_fields() -> None:
    """Forward compatibility: a later version may append non-empty fields."""
    context = tracing.parse_traceparent(f"01-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01-xyz")

    assert context is not None
    assert context.trace_id == VALID_TRACE_ID


# --- Trace continuation ------------------------------------------------------


def test_valid_context_makes_the_server_span_a_child_of_the_edge_span(
    collector: _Collector, client: TestClient
) -> None:
    response = client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    assert response.status_code == 200
    server = collector.await_named("GET /health")
    assert server["traceId"] == VALID_TRACE_ID
    assert server["parentSpanId"] == VALID_SPAN_ID
    assert server["kind"] == tracing.KIND_SERVER


def test_missing_context_starts_a_new_root_trace(collector: _Collector, client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    server = collector.await_named("GET /health")
    assert "parentSpanId" not in server
    assert len(server["traceId"]) == 32
    assert server["traceId"] != VALID_TRACE_ID


def test_invalid_context_starts_a_new_root_trace(collector: _Collector, client: TestClient) -> None:
    response = client.get("/health", headers={"traceparent": "00-garbage-nonsense-01"})

    assert response.status_code == 200
    server = collector.await_named("GET /health")
    assert "parentSpanId" not in server
    assert len(server["traceId"]) == 32


def test_unsampled_parent_suppresses_export(collector: _Collector, client: TestClient) -> None:
    """An upstream sampling decision of "no" is honoured, not overridden."""
    response = client.get("/health", headers={"traceparent": f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-00"})

    assert response.status_code == 200
    time.sleep(0.4)  # long enough for a batch to have been scheduled and sent
    assert collector.spans == []


# --- Fail-open ---------------------------------------------------------------


def test_export_thread_start_failure_never_reaches_a_request(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    original_tracer = tracing._TRACER
    original_start = tracing.threading.Thread.start

    def fail_exporter_only(thread: threading.Thread) -> None:
        if thread.name == "otlp-span-exporter":
            raise RuntimeError("thread unavailable")
        original_start(thread)

    try:
        tracing.reset_for_tests()
        tracing.configure("tourneydesk")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example/otlp")
        monkeypatch.setattr(tracing.threading.Thread, "start", fail_exporter_only)

        response = client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        tracing._TRACER = original_tracer


def test_enqueue_and_span_end_failures_are_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = tracing.SpanExporter("test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example/otlp")
    monkeypatch.setattr(exporter, "_ensure_worker", lambda: None)

    def fail_enqueue(_: dict[str, Any]) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(exporter._queue, "put_nowait", fail_enqueue)
    exporter.submit({"name": "safe"})

    class _BrokenExporter:
        def submit(self, _: dict[str, Any]) -> None:
            raise RuntimeError("submit unavailable")

    span = tracing.Span(
        "safe",
        tracing.KIND_INTERNAL,
        tracing.SpanContext(VALID_TRACE_ID, VALID_SPAN_ID),
        None,
        True,
        _BrokenExporter(),  # type: ignore[arg-type]
        time.time_ns(),
    )
    span.end()


def test_exporter_construction_failure_installs_an_inert_tracer(monkeypatch: pytest.MonkeyPatch) -> None:
    original_tracer = tracing._TRACER

    class _BrokenExporter:
        def __init__(self, _: str) -> None:
            raise RuntimeError("queue unavailable")

    try:
        tracing.reset_for_tests()
        monkeypatch.setattr(tracing, "SpanExporter", _BrokenExporter)
        tracer = tracing.configure("tourneydesk")
        assert tracer.exporter is None
    finally:
        tracing._TRACER = original_tracer


def test_flush_waits_for_inflight_post_and_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = tracing.SpanExporter("test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example/otlp")
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(_: list[dict[str, Any]]) -> None:
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(exporter, "_send", blocking_send)
    exporter.submit({"name": "in-flight"})
    assert entered.wait(1)

    assert exporter.flush(timeout=0.02) is False
    release.set()
    assert exporter.flush(timeout=1) is True


def test_collector_connection_failure_never_reaches_the_user(collector: _Collector, client: TestClient) -> None:
    collector.error = OSError("connection refused")

    response = client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert collector.payloads == []


def test_collector_rejection_never_reaches_the_user(collector: _Collector, client: TestClient) -> None:
    collector.status = 500

    response = client.post("/solve", json=SOLVE_BODY)

    assert response.status_code == 200
    assert response.json()["result"] == "solved"


def test_unconfigured_endpoint_exports_nothing_and_still_serves(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    sink = _Collector()
    monkeypatch.setattr(tracing.urllib.request, "urlopen", sink.urlopen)

    assert client.get("/health").status_code == 200
    time.sleep(0.4)
    assert sink.payloads == []


def test_sdk_disabled_switch_exports_nothing(
    monkeypatch: pytest.MonkeyPatch, collector: _Collector, client: TestClient
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert client.get("/health").status_code == 200
    time.sleep(0.4)
    assert collector.payloads == []


def test_non_http_endpoint_is_refused(
    monkeypatch: pytest.MonkeyPatch, collector: _Collector, client: TestClient
) -> None:
    """A file:// endpoint must never turn telemetry into a local-file write."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "file:///tmp/otlp")

    assert client.get("/health").status_code == 200
    time.sleep(0.4)
    assert collector.payloads == []


# --- Request span content ----------------------------------------------------


def test_server_span_carries_bounded_http_attributes(collector: _Collector, client: TestClient) -> None:
    client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    attributes = _attrs(collector.await_named("GET /health"))
    assert attributes["http.request.method"] == "GET"
    assert attributes["http.route"] == "/health"
    assert attributes["http.response.status_code"] == 200
    assert attributes["request.outcome"] == "success"


def test_unknown_methods_collapse_to_one_bounded_value(collector: _Collector, client: TestClient) -> None:
    response = client.request("BREW", "/health", headers={"traceparent": VALID_TRACEPARENT})

    assert response.status_code == 405
    span = collector.await_named("_OTHER /health")
    assert _attrs(span)["http.request.method"] == "_OTHER"


def test_unknown_routes_collapse_to_a_single_bounded_name(collector: _Collector, client: TestClient) -> None:
    client.get("/definitely-not-a-route", headers={"traceparent": VALID_TRACEPARENT})

    span = collector.await_named("GET /*")
    assert _attrs(span)["http.route"] == "/*"
    assert _attrs(span)["request.outcome"] == "not_found"


def test_warmup_requests_are_labelled(collector: _Collector, client: TestClient) -> None:
    client.get(
        "/health",
        headers={"traceparent": VALID_TRACEPARENT, "x-request-purpose": "warmup"},
    )

    assert _attrs(collector.await_named("GET /health"))["request.purpose"] == "warmup"


def test_a_valid_correlation_id_is_kept_and_a_forged_one_is_dropped(collector: _Collector, client: TestClient) -> None:
    correlation_id = "0af7651916cd43dd8448eb211c80319c"
    client.get(
        "/health",
        headers={"traceparent": VALID_TRACEPARENT, "x-correlation-id": correlation_id},
    )
    assert _attrs(collector.await_named("GET /health"))["request.correlation_id"] == correlation_id

    collector.payloads.clear()
    tracing.reset_cold_start_for_tests()
    client.get(
        "/health",
        headers={"traceparent": VALID_TRACEPARENT, "x-correlation-id": "note-to-self: leak me"},
    )
    assert "request.correlation_id" not in _attrs(collector.await_named("GET /health"))


def test_server_errors_are_reported_as_error_status(collector: _Collector, client: TestClient) -> None:
    from demo.api.main import app

    @app.get("/boom-for-tests")
    def _boom() -> dict[str, str]:  # pragma: no cover -- raises before returning
        raise RuntimeError("kaboom")

    try:
        with pytest.raises(RuntimeError):
            client.get("/boom-for-tests", headers={"traceparent": VALID_TRACEPARENT})

        span = collector.await_named("GET /*")
        assert span["status"]["code"] == tracing.STATUS_ERROR
        attributes = _attrs(span)
        assert attributes["request.outcome"] == "internal_error"
        assert attributes["error.type"] == "RuntimeError"
        # The exception is recorded by type only -- never its message.
        assert "kaboom" not in json.dumps(span)
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/boom-for-tests"]


def test_client_error_status_is_an_outcome_not_an_error(collector: _Collector, client: TestClient) -> None:
    """A 422 is the API working correctly; it must not look like an incident."""
    response = client.post("/solve", json={"days": 1, "divisions": [], "fields": []})

    assert response.status_code == 422
    span = collector.await_named("POST /solve")
    assert span["status"]["code"] == tracing.STATUS_UNSET
    assert _attrs(span)["request.outcome"] == "invalid_request"


# --- Cold start --------------------------------------------------------------


def test_first_request_reports_a_cold_start_with_an_init_span(collector: _Collector, client: TestClient) -> None:
    client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    server = collector.await_named("GET /health")
    init = collector.await_named("container.init")

    assert _attrs(server)["faas.coldstart"] is True
    assert _attrs(server)["container.reused"] is False
    assert _attrs(server)["container.reuse_bucket"] == "first"
    assert init["traceId"] == VALID_TRACE_ID
    assert init["parentSpanId"] == server["spanId"]
    # The init window ends exactly where the request begins.
    assert init["endTimeUnixNano"] == server["startTimeUnixNano"]
    assert int(init["startTimeUnixNano"]) <= int(init["endTimeUnixNano"])


def test_later_requests_report_a_warm_container(collector: _Collector, client: TestClient) -> None:
    client.get("/health", headers={"traceparent": VALID_TRACEPARENT})
    # Both spans of the first request must land before the collector is cleared:
    # they are submitted separately and can arrive in different batches.
    collector.await_named("container.init")
    collector.await_named("GET /health")
    collector.payloads.clear()

    client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

    server = collector.await_named("GET /health")
    assert _attrs(server)["faas.coldstart"] is False
    assert _attrs(server)["container.reused"] is True
    assert _attrs(server)["container.reuse_bucket"] == "2-5"
    assert all(span["name"] != "container.init" for span in collector.spans)


def test_cold_start_marker_is_claimed_exactly_once() -> None:
    tracing.reset_cold_start_for_tests()

    first, first_bucket, elapsed = tracing.request_container_state()
    second, second_bucket, no_elapsed = tracing.request_container_state()

    assert (first, second) == (True, False)
    assert (first_bucket, second_bucket) == ("first", "2-5")
    assert elapsed is not None
    assert no_elapsed is None


def test_instance_id_changes_after_a_snapshot_restore_identity_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing.socket, "gethostname", lambda: "restored-container-a")
    first = tracing.instance_id()
    assert tracing.instance_id() == first

    monkeypatch.setattr(tracing.socket, "gethostname", lambda: "restored-container-b")
    assert tracing.instance_id() != first


# --- Solve phase -------------------------------------------------------------


def test_solve_emits_a_child_span_with_outcome_attributes(collector: _Collector, client: TestClient) -> None:
    response = client.post("/solve", json=SOLVE_BODY, headers={"traceparent": VALID_TRACEPARENT})

    assert response.status_code == 200
    solve_span = collector.await_named("solve.tournament")
    server = collector.await_named("POST /solve")
    assert solve_span["traceId"] == VALID_TRACE_ID
    assert solve_span["parentSpanId"] == server["spanId"]

    attributes = _attrs(solve_span)
    assert attributes["solve.result"] == "solved"
    assert attributes["solve.status"] in ("OPTIMAL", "FEASIBLE")
    assert attributes["tournament.teams"] == 4
    assert attributes["tournament.fields"] == 1
    assert attributes["solve.games"] >= 1


def test_no_span_leaks_names_from_the_request(collector: _Collector, client: TestClient) -> None:
    client.post("/solve", json=SOLVE_BODY, headers={"traceparent": VALID_TRACEPARENT})

    collector.await_named("solve.tournament")
    collector.await_named("POST /solve")
    exported = json.dumps(collector.payloads)
    assert "Wildcats" not in exported
    assert "Riverside" not in exported


def test_resource_identifies_the_service_and_container(collector: _Collector, client: TestClient) -> None:
    client.get("/health", headers={"traceparent": VALID_TRACEPARENT})
    collector.await_named("GET /health")

    resource = collector.resource_attrs()
    assert resource["service.name"] == "tourneydesk"
    assert resource["service.instance.id"] == tracing.instance_id()


# --- Chat phase --------------------------------------------------------------


def test_chat_cost_is_computed_only_from_configured_numeric_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    from tourneydesk.providers import pydantic_ai

    monkeypatch.setenv("GPT_INPUT_USD_PER_MILLION_TOKENS", "2")
    monkeypatch.setenv("GPT_OUTPUT_USD_PER_MILLION_TOKENS", "8")
    model: Any = SimpleNamespace(model_name="gpt-test")
    result = SimpleNamespace(usage=lambda: SimpleNamespace(input_tokens=1_000, output_tokens=500))

    fields = pydantic_ai._usage_fields("gpt", model, result)

    assert fields == {
        "model_name": "gpt-test",
        "input_tokens": 1_000,
        "output_tokens": 500,
        "estimated_cost_usd": 0.006,
    }


def test_chat_stream_records_ttft_tokens_and_a_final_outcome(collector: _Collector, client: TestClient) -> None:
    from tests.test_pydantic_ai_provider import _seed_store, _streaming_full_spec_model

    _seed_store("sess-trace", {"glm": _streaming_full_spec_model()})

    response = client.post(
        "/chat/stream",
        json={"session_id": "sess-trace", "message": "build it", "model": "glm"},
        headers={"traceparent": VALID_TRACEPARENT},
    )

    assert response.status_code == 200
    turn = collector.await_named("chat.turn")
    assert turn["traceId"] == VALID_TRACE_ID

    attributes = _attrs(turn)
    assert attributes["chat.outcome"] == "final"
    assert attributes["chat.engine"] == "glm"
    assert attributes["chat.streaming"] is True
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.provider.name"] == "zhipu"
    assert attributes["chat.ttft_ms"] >= 0
    assert attributes["chat.tool_calls"] >= 1
    # The speculative solve hangs off the same turn.
    solve_span = collector.await_named("solve.session")
    assert solve_span["parentSpanId"] == turn["spanId"]


def test_chat_spans_never_carry_the_message_or_reply(collector: _Collector, client: TestClient) -> None:
    from tests.test_pydantic_ai_provider import _seed_store, _streaming_full_spec_model

    _seed_store("sess-privacy", {"glm": _streaming_full_spec_model()})

    client.post(
        "/chat/stream",
        json={"session_id": "sess-privacy", "message": "Coach Danielle needs Saturday", "model": "glm"},
        headers={"traceparent": VALID_TRACEPARENT},
    )

    collector.await_named("chat.turn")
    exported = json.dumps(collector.payloads)
    assert "Danielle" not in exported
    assert "Saturday" not in exported


def _stream_scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/health",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"traceparent", VALID_TRACEPARENT.encode())],
    }


def test_asgi_24_streaming_client_disconnect_preserves_sent_status(collector: _Collector) -> None:
    """Exercise Starlette's real OSError -> ClientDisconnect production path."""

    async def body() -> Any:
        yield b"first"

    async def streaming_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await StreamingResponse(body(), status_code=206)(scope, receive, send)

    middleware = tracing.TracingMiddleware(streaming_app, routes=frozenset({"/health"}))
    started = False

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal started
        if message["type"] == "http.response.start":
            started = True
            return
        if started:
            raise OSError("socket closed")

    with pytest.raises(ClientDisconnect):
        asyncio.run(middleware(_stream_scope("2.4"), receive, send))

    span = collector.await_named("GET /health")
    assert span["status"]["code"] == tracing.STATUS_UNSET
    assert _attrs(span)["request.outcome"] == "cancelled"
    assert _attrs(span)["http.response.status_code"] == 206
    assert "error.type" not in _attrs(span)


def test_pre_asgi_24_disconnect_listener_marks_cancelled(collector: _Collector) -> None:
    """Exercise Starlette's older task-group listener, which returns normally."""

    async def scenario() -> None:
        response_started = asyncio.Event()
        receive_count = 0

        async def body() -> Any:
            while True:
                yield b"chunk"
                await asyncio.sleep(10)

        async def streaming_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            await StreamingResponse(body(), status_code=207)(scope, receive, send)

        middleware = tracing.TracingMiddleware(streaming_app, routes=frozenset({"/health"}))

        async def receive() -> dict[str, Any]:
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            await response_started.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_started.set()

        await middleware(_stream_scope("2.3"), receive, send)

    asyncio.run(scenario())

    span = collector.await_named("GET /health")
    assert span["status"]["code"] == tracing.STATUS_UNSET
    assert _attrs(span)["request.outcome"] == "cancelled"
    assert _attrs(span)["http.response.status_code"] == 207
    assert "error.type" not in _attrs(span)


def test_lifespan_shutdown_flushes_for_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    flushed: list[float] = []
    incoming = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))

    async def lifespan_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})

    async def receive() -> dict[str, Any]:
        return next(incoming)

    async def send(_: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(tracing, "flush", lambda timeout=3.0: flushed.append(timeout) or True)
    middleware = tracing.TracingMiddleware(lifespan_app, routes=frozenset())
    asyncio.run(middleware({"type": "lifespan"}, receive, send))

    assert flushed == [3.0]
