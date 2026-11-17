"""Independent user-supplied original-PPTX expectations and isolated XML unit tests.

The original 32.9 MB file is NOT mounted in this agent. Golden acceptance of
the real deck is skipped unless NIT_GOLDEN_PPTX_PATH explicitly points to it.
"""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches,Pt

SCRIPTS=Path(__file__).resolve().parents[1]/"nit_engine"/"scripts"
sys.path.insert(0,str(SCRIPTS))
from pptx_layout_resolver import normalized_wingdings_text
from ingest import pptx_pages, pptx_media_coverage, plan_vision
from lesson_boundary import detect
from quality_gate import evaluate


def png(rgb):
    def chunk(kind,payload):
        return struct.pack(">I",len(payload))+kind+payload+struct.pack(">I",zlib.crc32(kind+payload)&0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"
        +chunk(b"IHDR",struct.pack(">2I5B",1,1,8,2,0,0,0))
        +chunk(b"IDAT",zlib.compress(b"\x00"+bytes(rgb)))
        +chunk(b"IEND",b""))


class OriginalGoldenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden=json.loads((Path(__file__).parent/"fixtures"/"golden_jipdan_pptx.json").read_text(encoding="utf-8"))

    def test_golden_independent_counts_are_documented(self):
        g=self.golden
        self.assertEqual(g["audio"]["expected_count"],13)
        self.assertEqual(g["playback_controls"]["expected_count"],13)
        self.assertEqual(g["wingdings_arrows"]["count"],14)
        self.assertEqual(g["educational_visuals"]["vision_candidate_pages"],[8])
        self.assertEqual(len(g["audio"]["slide_pages"]),13)
        self.assertEqual(g["wingdings_arrows"]["use_as_bullets"],0)

    def test_first_session_label_in_middle_of_filename(self):
        name="집단극단화_미디어_1차시_발제.pptx"
        page=[{"page":1,"content_role":"learning_material",
               "blocks":[{"id":"b","type":"text","text":"집단극단화의 원인과 결과를 설명한다"}]}]
        found=detect(page,name)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["week"],1)
        self.assertEqual(found[0]["status"],"filename_candidate_unverified")
        self.assertGreater(found[0]["filename_match_span"][0],0)

    def test_explicit_wingdings_right_arrow_is_normalized_with_source_proof(self):
        prs=Presentation()
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        box=slide.shapes.add_textbox(Inches(1),Inches(1),Inches(9),Inches(1))
        p=box.text_frame.paragraphs[0]
        p.add_run().text="집단 토론 "
        arrow=p.add_run();arrow.text="\uf0e0";arrow.font.name="Wingdings"
        p.add_run().text=" 집단 극단화가 심화되는 원인을 추론한다"
        shown,proof=normalized_wingdings_text(box)
        self.assertIn("토론 → 집단",shown)
        self.assertEqual(proof[0]["source_char"],"U+F0E0")
        self.assertEqual(proof[0]["font"],"Wingdings")
        self.assertNotIn("private_use_characters",evaluate(shown)["reasons"])
        arrow.font.name=None
        untrusted,proof=normalized_wingdings_text(box)
        self.assertIn("\uf0e0",untrusted)
        self.assertEqual(proof,[])

    def test_shared_audio_icon_across_13_slides_excluded_p8_picture_retained(self):
        g=self.golden
        prs=Presentation()
        played=g["playback_controls"]["slide_pages"]
        img=png((32,32,32))
        educational=png((160,220,0))
        for page in range(1,18):
            slide=prs.slides.add_slide(prs.slide_layouts[6])
            if page in played:
                picture=slide.shapes.add_picture(io.BytesIO(img),Inches(1),Inches(1))
                # Match a media-linked PNG from a PPTX slide relationship.
                cnv=picture._element.xpath(".//p:cNvPr")[0]
                link=OxmlElement("a:hlinkClick")
                link.set("action","ppaction://media")
                cnv.append(link)
            if page==8:
                slide.shapes.add_picture(io.BytesIO(educational),Inches(2),Inches(1))
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/"group_polarization_1차시_test.pptx"
            prs.save(source)
            pages=list(pptx_pages(source))
        count=sum(p["visual_stats"].get("media_control_images",0) for p in pages)
        self.assertEqual(count,13)
        candidates=[p["page"] for p in pages if p["visual_stats"].get("images",0)]
        self.assertEqual(candidates,[8])
        self.assertEqual(pages[7]["visual_stats"]["images"],1)

    @unittest.skipUnless(os.getenv("NIT_GOLDEN_PPTX_PATH"),"Original lecture PPTX not supplied")
    def test_real_deck_matches_independent_xml_golden(self):
        source=Path(os.environ["NIT_GOLDEN_PPTX_PATH"])
        self.assertTrue(source.is_file())
        g=self.golden
        cover=pptx_media_coverage(source)
        self.assertEqual(cover["embedded_audio_assets"],g["audio"]["expected_count"])
        pages=list(pptx_pages(source))
        play=sum(p["visual_stats"].get("media_control_images",0) for p in pages)
        self.assertEqual(play,g["playback_controls"]["expected_count"])
        self.assertEqual([p["page"] for p in pages if p["visual_stats"].get("images")],
                         g["educational_visuals"]["slide_pages"])
        boundaries=detect(pages,source.name)
        self.assertTrue(any(x["week"]==1 for x in boundaries))
        arrows=[(p["page"],x) for p in pages for x in p["blocks"]
                for _ in x.get("glyph_normalizations",[])]
        self.assertEqual(len(arrows),g["wingdings_arrows"]["count"])
        self.assertEqual(sorted({p for p,x in arrows}),g["wingdings_arrows"]["slide_pages"])


if __name__=="__main__":
    unittest.main()
