"""
Record the live VLM (Anthropic) extraction of the Meridian PDF so regression
tests can replay exactly what the model returned, without API calls or cost.

    docker compose exec -T -w /app app python tests/fixtures/disclosure_pack/record_vlm_extraction.py
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import adapters.vlm_anthropic as va
from adapters.pdf_renderer import PdfRenderer
from application.factory import _build_reader
from config.settings import get_settings

HERE = Path(__file__).resolve().parent
SRC = HERE / "05_Meridian_Financial_Statements.pdf"
OUT = HERE / "05_Meridian_vlm_extraction.json"

captured: list[dict] = []
_orig = va._parse_tables


def _record(text, page_number):
    captured.append({"page": page_number, "payload": json.loads(text)})
    return _orig(text, page_number)


va._parse_tables = _record
reader = _build_reader(get_settings())
content = SRC.read_bytes()
doc = PdfRenderer(dpi=150).render(content, "application/pdf", fingerprint=hashlib.sha256(content).digest())
for page in doc.pages:
    reader.extract_tables(page)
OUT.write_text(json.dumps({"model": reader.label, "pages": captured}, indent=2, ensure_ascii=False))
print(f"recorded {len(captured)} page(s) → {OUT.name}; {reader.usage.summary()}")
