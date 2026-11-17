"""Vercel queue publish contract: job IDs only, at-least-once idempotency."""
import os
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from learningx import ingest_queue


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_one_idempotent_message(self):
        with patch.dict(os.environ,{"NIT_QUEUE_ENABLED":"1"}):
            with patch("vercel.queue.send",new_callable=AsyncMock) as send:
                await ingest_queue._dispatch_job("a"*32,2)
                send.assert_awaited_once_with(
                    "learningx-ingest",{"job_id":"a"*32},
                    idempotency_key="learningx-"+"a"*32+"-attempt-2")

    async def test_no_queue_on_serverless_fails_closed(self):
        with patch.dict(os.environ,{"NIT_SERVERLESS_RUNTIME":"1","NIT_QUEUE_ENABLED":"0"}):
            with self.assertRaises(HTTPException) as error:
                await ingest_queue._dispatch_job("b"*32,0)
            self.assertEqual(error.exception.status_code,503)

    async def test_dispatch_error_does_not_appear_as_success(self):
        with patch.dict(os.environ,{"NIT_QUEUE_ENABLED":"1"}):
            with patch("vercel.queue.send",new_callable=AsyncMock,side_effect=RuntimeError("offline")):
                with self.assertRaises(HTTPException) as error:
                    await ingest_queue._dispatch_job("c"*32,0)
                self.assertEqual(error.exception.status_code,503)
                self.assertNotIn("offline",error.exception.detail)

    async def test_queue_subscriber_does_not_access_file_payload(self):
        from learningx.queue_subscriber import process_lesson_job
        with patch("learningx.queue_subscriber.process_next_job",
                   return_value={"result":"ALREADY_READY","job_id":"d"*32}) as worker:
            await process_lesson_job({"job_id":"d"*32})
            worker.assert_called_once_with("d"*32)
