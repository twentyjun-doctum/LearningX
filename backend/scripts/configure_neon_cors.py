"""Explicitly configure PRIVATE Neon S3 CORS for LearningX browser presigned PUT.

Run only in trusted operator environment with branch-scoped S3 credentials.
Never expose S3 keys to the Vercel static frontend.
"""
import os
from learningx.private_store import require_store


def main():
    store=require_store()
    allowed=os.getenv("NIT_CORS_ORIGINS","").split(",")
    allowed=[origin.strip() for origin in allowed if origin.strip()]
    if not allowed or any(not x.startswith("https://") or "*" in x or "/" in x[8:]
                          for x in allowed):
        raise SystemExit("Set explicit HTTPS NIT_CORS_ORIGINS without wildcards or paths")
    config={"CORSRules":[{
        "AllowedOrigins":allowed,
        "AllowedMethods":["PUT"],
        "AllowedHeaders":["Content-Type","x-amz-*"],
        "ExposeHeaders":["ETag"],
        "MaxAgeSeconds":600,
    }]}
    store.client.put_bucket_cors(Bucket=store.bucket,CORSConfiguration=config)
    actual=store.client.get_bucket_cors(Bucket=store.bucket)
    rules=actual.get("CORSRules",[])
    if not rules or set(rules[0].get("AllowedOrigins",[]))!=set(allowed):
        raise RuntimeError("Neon bucket CORS readback did not match requested origins")
    print("Private Neon bucket CORS verified for "+", ".join(allowed))


if __name__=="__main__":
    main()
