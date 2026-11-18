"""Operator-only Neon Object Storage CORS check/apply.

No broad "*" origin. This script should run in a secure operator or CI setup
environment and must not be exposed as a public FastAPI endpoint.
"""
import argparse
import os
from .private_store import require_store


def desired(origins):
    urls=[x.strip().rstrip("/") for x in origins.split(",") if x.strip()]
    if not urls:
        raise ValueError("At least one allowed Origin is required")
    for origin in urls:
        if "*" in origin or not (origin.startswith("https://") or origin.startswith("http://localhost:")):
            raise ValueError("Only explicit HTTPS origins or local test origin allowed")
        if "/" in origin.split("://",1)[1]:
            raise ValueError("Only origin, not path, permitted")
    return {"CORSRules":[{
        "AllowedOrigins": sorted(set(urls)),
        "AllowedMethods": ["PUT","HEAD"],
        "AllowedHeaders": ["Content-Type"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]}


def matches(actual,expected):
    rules=actual.get("CORSRules",[])
    if not rules:return False
    expected_rule=expected["CORSRules"][0]
    for rule in rules:
        if (set(expected_rule["AllowedOrigins"]).issubset(set(rule.get("AllowedOrigins",[])))
            and set(expected_rule["AllowedMethods"]).issubset(set(rule.get("AllowedMethods",[])))
            and ("*" in rule.get("AllowedHeaders",[]) or
                 set(expected_rule["AllowedHeaders"]).issubset(set(rule.get("AllowedHeaders",[]))))):
            return True
    return False


def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check",action="store_true")
    group.add_argument("--apply",action="store_true")
    parser.add_argument("--origins",default=os.getenv("NIT_CORS_ORIGINS",""))
    args=parser.parse_args()
    store=require_store()
    config=desired(args.origins)
    if args.apply:
        store.client.put_bucket_cors(Bucket=store.bucket,CORSConfiguration=config)
    actual=store.client.get_bucket_cors(Bucket=store.bucket)
    if not matches(actual,config):
        raise SystemExit("CORS policy missing required origin/method/header")
    print("Private upload bucket CORS verified. Allowed origins: "+
          ", ".join(config["CORSRules"][0]["AllowedOrigins"]))


if __name__=="__main__":
    main()
