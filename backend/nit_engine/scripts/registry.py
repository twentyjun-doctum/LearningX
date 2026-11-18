#!/usr/bin/env python3
"""Convenience router; exact operations remain available as individual scripts."""
import argparse,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
choices={'preflight':'preflight.py','ingest':'ingest.py','enrich':'enrich_gemini.py','context':'build_context.py',
         'coaching':'build_coaching_index.py','ocr':'recover_ocr.py','reindex':'reindex.py','sections':'build_section_chunks.py','evaluate':'evaluate_ideas.py','coverage':'coverage.py','promote':'promote_reviewed.py','quality':'quality_gate.py','verify':'verify_artifacts.py','recovery':'recovery_plan.py','review-images':'render_review_samples.py','evalset':'eval_dataset.py','evalmetrics':'eval_metrics.py','benchmark':'engine_benchmark.py','verified-coaching':'verified_coaching.py','parser-audit':'parser_audit.py'}
p=argparse.ArgumentParser();p.add_argument('command',choices=choices);a,other=p.parse_known_args()
raise SystemExit(subprocess.call([sys.executable,str(root/choices[a.command]),*other]))
