"""Vercel Queues push-mode private subscriber.

Only metadata job IDs are sent; original teacher materials remain in private
Neon S3. This subscriber is NOT an Internet-accessible FastAPI endpoint.
"""
import asyncio
import re
from vercel.queue import subscribe

from .ingest_queue import process_next_job

JOB_ID = re.compile(r"^[0-9a-f]{32}$")


@subscribe(topic="learningx-ingest", retry_after=60, max_concurrency=2)
async def process_lesson_job(message: dict[str, str]) -> None:
    job_id = message.get("job_id") if isinstance(message, dict) else None
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid ingestion queue message")
    result = await asyncio.to_thread(process_next_job, job_id)
    if result["result"] == "INFLIGHT":
        # A function died while holding a DB lease; let Queue retry after expiry.
        raise RuntimeError("Ingestion job already leased")
    if result["result"] in ("MISSING", "ERROR"):
        raise RuntimeError("Ingestion job not yet recoverable")
    # READY, FAILED (terminal), ALREADY_READY, ALREADY_FAILED => ack.
