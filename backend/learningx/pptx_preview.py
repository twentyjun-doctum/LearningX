"""Generate an optional presentation PDF in the background ingestion worker."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import pymupdf


def generate_preview(original: Path, parsed: Path, page_count: int) -> bool:
    # The canonical numbering includes hidden slides. Access is restricted by
    # the original manifest when the API serves individual slides.
    with TemporaryDirectory(prefix="learningx-pptx-preview-") as tmp:
        root = Path(tmp)
        exported = root / original.with_suffix(".pdf").name
        try:
            subprocess.run([
                "soffice", f"-env:UserInstallation={(root / 'profile').as_uri()}",
                "--headless", "--convert-to",
                'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}',
                "--outdir", tmp, str(original),
            ], env={"PATH": os.defpath, "HOME": tmp, "LANG": "C.UTF-8"}, timeout=45, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if exported.stat().st_size > 40 * 1024 * 1024:
                return False
            with pymupdf.open(exported) as doc:
                if len(doc) != page_count:
                    return False
            exported.replace(parsed / "live-preview.pdf")
        except (OSError, subprocess.SubprocessError, pymupdf.FileDataError):
            return False
    return True
