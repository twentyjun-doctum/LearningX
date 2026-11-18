"""Server-side hash, size and upload bucket CORS safety contracts."""
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from learningx.ingest_queue import _download_verified, MAX_UPLOAD_BYTES
from learningx.configure_cors import desired, matches
from test_store import FakeS3
from fastapi import HTTPException


class WorkerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeS3()
        self.bucket="learningx-private-lessons"
        self.key="incoming/"+("a"*32)+"/"+("b"*32)+"/source.pdf"
        self.store=SimpleNamespace(client=self.fake,bucket=self.bucket)

    def test_worker_computes_hash_of_s3_bytes_above_vercel_body_limit(self):
        data=b"%PDF-1.7\n"+b"x"*(6*1024*1024)
        self.fake.put_object(Bucket=self.bucket,Key=self.key,Body=data,ContentType="application/pdf")
        with tempfile.TemporaryDirectory() as d:
            filename=Path(d)/"source.pdf"
            # No client-reported SHA-256 exists on the job: worker is authoritative.
            job=SimpleNamespace(source_key=self.key,expected_size=len(data),
                                file_sha256="0"*64)
            actual=_download_verified(self.store,job,filename)
            self.assertEqual(actual,hashlib.sha256(data).hexdigest())
            self.assertNotEqual(actual,job.file_sha256)
            self.assertEqual(filename.stat().st_size,len(data))

    def test_worker_rejects_post_upload_size_mutation(self):
        data=b"%PDF-1.7\nX"
        self.fake.put_object(Bucket=self.bucket,Key=self.key,Body=data,ContentType="application/pdf")
        job=SimpleNamespace(source_key=self.key,expected_size=len(data)-1)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                _download_verified(self.store,job,Path(d)/"source.pdf")

    def test_browser_cors_restricts_origin_and_put_headers(self):
        config=desired("https://learning-x-one.vercel.app,http://localhost:3000")
        rule=config["CORSRules"][0]
        self.assertEqual(set(rule["AllowedOrigins"]),
                         {"https://learning-x-one.vercel.app","http://localhost:3000"})
        self.assertEqual(set(rule["AllowedMethods"]),{"PUT","HEAD"})
        self.assertIn("Content-Type",rule["AllowedHeaders"])
        self.assertTrue(matches(config,config))

    def test_wildcard_cors_is_not_allowed(self):
        with self.assertRaises(ValueError):
            desired("*")
        with self.assertRaises(ValueError):
            desired("https://*.vercel.app")


if __name__=="__main__":
    unittest.main()
