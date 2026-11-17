"""Regression gates for PPTX media, session 1, Vision state and arrow glyph.

Synthetic cases are regression coverage, NOT the unavailable 32.9MB/13-audio
original lecture deck. Never label these tests as real-document acceptance.
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"nit_engine"/"scripts"
sys.path.insert(0,str(ROOT))
from ingest import pptx_media_coverage, ingest
from lesson_boundary import detect
from pptx_layout_resolver import is_media_control
from text_normalization import normalize_bullets, clean_compare
from quality_gate import evaluate


class SourceRegressionTests(unittest.TestCase):
    def test_thirteen_embedded_audio_assets_are_counted_without_transcription(self):
        with tempfile.TemporaryDirectory() as folder:
            pptx=Path(folder)/"13_audio.pptx"
            with zipfile.ZipFile(pptx,"w") as zf:
                for index in range(13):
                    zf.writestr(f"ppt/media/audio{index:02d}.mp3",b"ID3"+bytes([index]))
            coverage=pptx_media_coverage(pptx)
            self.assertEqual(coverage["embedded_audio_assets"],13)
            self.assertEqual(coverage["audio_status"],"NOT_ANALYZED")
            self.assertIn("not transcribed",coverage["warning"])

    def test_first_session_lecture_title_is_detected(self):
        pages=[{"page":1,"content_role":"lecture_cover","blocks":[
            {"id":"b1","type":"text","text":"제1차시 집단극단화의 이해"}]}]
        result=detect(pages,"실습교안.pptx")
        self.assertTrue(result)
        self.assertEqual(result[0]["week"],1)
        self.assertEqual(result[0]["start_page"],1)

    def test_private_use_directional_arrow_is_not_silently_bulleted(self):
        marker="\uf0e0"
        example="집단 토론 "+marker+" 극단화"
        self.assertIn(marker,normalize_bullets(example))
        self.assertIn(marker,clean_compare(example))
        self.assertFalse(evaluate(example)["eligible_for_coaching"])

    def test_powerpoint_playback_control_is_not_educational_image(self):
        class Elem:
            xml='<a:hlinkClick action="ppaction://media" />'
        class Fake:
            element=Elem()
        self.assertTrue(is_media_control(Fake()))

    def test_actual_native_pptx_ingest_discloses_vision_not_executed(self):
        try:from pptx import Presentation
        except ImportError:self.skipTest("python-pptx unavailable")
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"01차시_교안.pptx"
            prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text="1차시 집단극단화"
            slide.placeholders[1].text="이론과 사례를 비교합니다."
            prs.save(p)
            out=Path(folder)/"out"
            result=ingest(p,out,vision_limit=0)
            doc=json.loads((out/"document.json").read_text())
            manifest=json.loads((out/"manifest.json").read_text())
            self.assertEqual(doc["vision_execution"]["status"],"NOT_RUN")
            self.assertEqual(doc["vision_execution"]["completed_jobs"],0)
            self.assertFalse(doc["vision_execution"]["visual_semantics_verified"])
            self.assertEqual(manifest["vision_execution"]["status"],"NOT_RUN")
            self.assertTrue((out/"parser_audit_report.json").exists())


if __name__=="__main__":
    unittest.main()
