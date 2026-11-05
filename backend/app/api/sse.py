"""Durable SSE replay. Subscribe before reading; the database is the cursor authority."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from app.api.deps import TokenDep, run
from app.graph import runner as graph_runner
from app.store import db
from app.store import queries as store_queries
from app.store import settings as store_settings

router = APIRouter(tags=["sse"], dependencies=[TokenDep])


def _frame(query_id: str, seq: int, event_type: str, payload: dict) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str)
    head = f"id: {query_id}:{seq}\n" if seq >= 1 else ""
    return f"{head}data: {data}\n\n"


def _parse_last_event_id(header: str | None, query_id: str | None = None) -> int:
    if not header or ":" not in header:
        return 0
    owner, value = header.rsplit(":", 1)
    if query_id is not None and owner != query_id:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


@router.get("/queries/{query_id}/stream")
async def stream(query_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    after_seq = _parse_last_event_id(last_event_id, query_id)

    def _validate() -> int:
        with db.get_conn() as conn:
            store_queries.get_query(conn, query_id)
            return max(1, int(store_settings.get_setting(conn, "sse.heartbeat_s")))

    heartbeat_s = await run(_validate)  # 404 before response headers are sent.

    def _read(cursor: int) -> tuple:
        with db.get_conn() as conn:
            conn.execute("BEGIN")  # Events, status and preview from one SQLite snapshot.
            events = store_queries.query_events(conn, query_id, after_seq=cursor)
            status = store_queries.get_query(conn, query_id).status
            preview = store_queries.get_preview(conn, query_id)
            return events, status, preview

    async def _gen() -> AsyncIterator[str]:
        queue = graph_runner.subscribe(query_id)  # Must precede the first replay read.
        cursor = after_seq
        preview_sent = False
        first = True
        try:
            while True:
                events, status, preview = await run(_read, cursor)
                if first and after_seq > 0:
                    yield "event: stream_resumed\ndata: {}\n\n"
                first = False
                for ev in events:
                    # A replayed done must not close the browser before rows are restored.
                    if preview is not None and ev.type in {"result_meta", "done"}:
                        yield _frame(
                            query_id, 0, "result", {"columns": preview.columns, "rows": preview.rows}
                        )
                        preview_sent = True
                    cursor = ev.seq
                    yield _frame(query_id, ev.seq, ev.type, ev.payload)
                if status in store_queries.TERMINAL_STATUS:
                    if preview is not None and not preview_sent:
                        yield _frame(
                            query_id, 0, "result", {"columns": preview.columns, "rows": preview.rows}
                        )
                    return
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield ": hb\n\n"
                    continue  # Re-read durable status/events, including a missed final done.
                if int(frame.get("seq") or 0) <= 0:
                    yield _frame(query_id, 0, str(frame["type"]), frame["payload"])
                # Persisted queue frames are wakeups only. Replay fills gaps after overflow.
        finally:
            graph_runner.unsubscribe(query_id, queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
