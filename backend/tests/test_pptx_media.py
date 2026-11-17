"""PPTX parser-audit and transparent media-coverage regression."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from pptx import Presentation

ROOT=Path(__file__).resolve().parents[1]/"nit_engine"


class PPTXMediaAuditTests(unittest.TestCase):
    def test_pptx_audit_and_unprocessed_narration_are_reported(self):
        self.assertTrue((ROOT/"scripts"/"parser_audit.py").is_file())
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            source=folder/"slides.pptx"
            p=Presentation()
            slide=p.slides.add_slide(p.slide_layouts[1])
            slide.shapes.title.text="개념 학습"
            slide.placeholders[1].text="배경과 전제를 구분하는 학습 내용"
            p.save(source)
            voiced=folder/"narrated.pptx"
            with zipfile.ZipFile(source) as zin,zipfile.ZipFile(voiced,"w") as zout:
                for name in zin.namelist():
                    zout.writestr(name,zin.read(name))
                # Embedded audio is intentionally not decoded or transcribed.
                zout.writestr("ppt/media/slide1.mp3",b"ID3"+b"\x00"*130)
            out=folder/"output"
            run=subprocess.run([sys.executable,str(ROOT/"scripts"/"ingest.py"),str(voiced),
                "--out",str(out)],capture_output=True,text=True,timeout=35)
            self.assertEqual(run.returncode,0,run.stderr[-700:])
            audit=subprocess.run([sys.executable,str(ROOT/"scripts"/"parser_audit.py"),
                "--out",str(out)],capture_output=True,text=True,timeout=10)
            self.assertEqual(audit.returncode,0,audit.stderr[-700:])
            result=json.loads((out/"parser_audit_report.json").read_text())
            self.assertEqual(result["comparison_mode"],"not_applicable_pptx")
            self.assertEqual(result["pages_compared"],0)
            manifest=json.loads((out/"manifest.json").read_text())
            self.assertEqual(manifest["media_coverage"]["audio_status"],"NOT_ANALYZED")
            self.assertEqual(manifest["media_coverage"]["embedded_audio_assets"],1)
            self.assertIn("PPTX narration/audio transcription",manifest["not_done"])
            document=json.loads((out/"document.json").read_text())
            self.assertEqual(document["media_coverage"]["audio_status"],"NOT_ANALYZED")
