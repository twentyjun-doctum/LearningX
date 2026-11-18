"""Stand-alone long-running parser; run on isolated GitHub Actions executor.

Never starts FastAPI or calls a Vercel Function. All results are persisted to
Neon; source and signed evidence are held in private Neon Object Storage.
"""
import argparse
import os
from . import ingest_queue

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--job-id",default=None)
    parser.add_argument("--max-jobs",type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.max_jobs<=10:
        parser.error("--max-jobs must be 1..10")
    if not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL missing")
    if not ingest_queue.private_store.available():
        raise SystemExit("Neon private storage not configured")
    completed=[]
    for index in range(args.max_jobs):
        job=ingest_queue.process_next_job(args.job_id if index==0 else None)
        # Never print URLs or source content.
        print(f"worker_result={job.get('result')}")
        completed.append(job.get("result"))
        if args.job_id or job.get("result") in ("EMPTY","INFLIGHT","MISSING"):
            break
    if any(x=="FAILED" for x in completed):
        raise SystemExit(1)

if __name__=="__main__":
    main()
