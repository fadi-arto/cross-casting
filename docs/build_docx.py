"""Build Cross-Casting-APIs.docx with the standard library only."""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).with_name("Cross-Casting-APIs.docx")

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Arial"/>
        <w:sz w:val="24"/>
        <w:szCs w:val="24"/>
        <w:lang w:bidi="ar-SA"/>
      </w:rPr>
    </w:rPrDefault>
    <w:pPrDefault>
      <w:pPr>
        <w:bidi/>
      </w:pPr>
    </w:pPrDefault>
  </w:docDefaults>
</w:styles>
"""


def p(text: str, *, size=24, bold=False, color="4B5563", align="right", space_before=80, space_after=80, font=None) -> str:
    b = "<w:b/><w:bCs/>" if bold else ""
    fonts = f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>' if font else ""
    jc = {"right": "right", "center": "center", "left": "left"}[align]
    return f"""
    <w:p>
      <w:pPr>
        <w:bidi/>
        <w:jc w:val="{jc}"/>
        <w:spacing w:before="{space_before}" w:after="{space_after}"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          {fonts}
          {b}
          <w:color w:val="{color}"/>
          <w:sz w:val="{size}"/>
          <w:szCs w:val="{size}"/>
          <w:rtl/>
        </w:rPr>
        <w:t xml:space="preserve">{escape(text)}</w:t>
      </w:r>
    </w:p>
    """


def code(text: str) -> str:
    return p(text, size=20, color="1B3A4B", align="left", font="Consolas", space_before=40, space_after=40)


def cell(text: str, *, header=False, fill=None) -> str:
    shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    color = "FFFFFF" if header else "374151"
    bold = "<w:b/><w:bCs/>" if header else ""
    return f"""
      <w:tc>
        <w:tcPr>
          {shade}
          <w:tcBorders>
            <w:top w:val="single" w:sz="4" w:color="D1D5DB"/>
            <w:left w:val="single" w:sz="4" w:color="D1D5DB"/>
            <w:bottom w:val="single" w:sz="4" w:color="D1D5DB"/>
            <w:right w:val="single" w:sz="4" w:color="D1D5DB"/>
          </w:tcBorders>
        </w:tcPr>
        <w:p>
          <w:pPr><w:bidi/><w:jc w:val="right"/></w:pPr>
          <w:r>
            <w:rPr>{bold}<w:color w:val="{color}"/><w:sz w:val="20"/><w:szCs w:val="20"/><w:rtl/></w:rPr>
            <w:t xml:space="preserve">{escape(text)}</w:t>
          </w:r>
        </w:p>
      </w:tc>
    """


def table(headers: list[str], rows: list[list[str]]) -> str:
    grid = "".join(f'<w:gridCol w:w="{int(9000 / len(headers))}"/>' for _ in headers)
    header_row = "<w:tr>" + "".join(cell(h, header=True, fill="1B3A4B") for h in headers) + "</w:tr>"
    body = []
    for i, row in enumerate(rows):
        fill = "F3F6F6" if i % 2 == 0 else "FFFFFF"
        body.append("<w:tr>" + "".join(cell(c, fill=fill) for c in row) + "</w:tr>")
    return f"""
    <w:tbl>
      <w:tblPr>
        <w:tblW w:w="9000" w:type="dxa"/>
        <w:bidiVisual w:val="1"/>
        <w:tblLook w:firstRow="1"/>
      </w:tblPr>
      <w:tblGrid>{grid}</w:tblGrid>
      {header_row}
      {"".join(body)}
    </w:tbl>
    <w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>
    """


def document_xml() -> str:
    parts = [
        p("Cross-Casting", size=52, bold=True, color="1B3A4B", align="center", space_before=0, space_after=40),
        p("توثيق واجهات البرمجة (APIs)", size=36, bold=True, color="0F7668", align="center", space_after=80),
        p("محرك تحقق حسابي مستقل من الجداول المالية — الإصدار 1.0.0", size=22, color="6B7280", align="center", space_after=240),
        p(
            "يغطي هذا الملف واجهة الخدمة التي يعرّضها المشروع (FastAPI)، والخدمات الخارجية "
            "التي يستهلكها المحرك لاستخراج الجداول وتفسير الأخطاء. الحساب نفسه يتم داخل "
            "Python بـ Decimal ولا يعتمد على أي API خارجي.",
            size=22,
        ),
        p("1. واجهة الخدمة (FastAPI)", size=36, bold=True, color="1B3A4B", space_before=280),
        p("التشغيل المحلي:", size=22),
        code("uvicorn api.routes:app --reload"),
        p("العنوان الافتراضي: http://localhost:8000", size=22),
        p("التوثيق التفاعلي: http://localhost:8000/docs", size=22),
        p("1.1 المسارات", size=28, bold=True, color="0F7668", space_before=200),
        table(
            ["المسار", "الطريقة", "الوصف"],
            [
                ["/analyze", "POST", "رفع ملف وتحليل الجداول المالية"],
                ["/health", "GET", "فحص أن الخدمة تعمل"],
                ["/docs", "GET", "توثيق Swagger التلقائي"],
                ["/openapi.json", "GET", "مواصفات OpenAPI"],
            ],
        ),
        p("1.2 POST /analyze", size=28, bold=True, color="0F7668", space_before=160),
        p(
            "يرفع ملفاً واحداً (multipart/form-data) ويشغّل خط المعالجة ثم يعيد تقرير تدقيق JSON.",
            size=22,
        ),
        table(
            ["العنصر", "القيمة"],
            [
                ["Content-Type", "multipart/form-data"],
                ["اسم الحقل", "file"],
                ["الأنواع المدعومة", "PDF, Excel (.xlsx / .xls), PNG, JPEG"],
                ["MIME", "application/pdf, image/png, image/jpeg, spreadsheetml.sheet"],
                ["استجابة النجاح", "200 — AuditReportOut"],
                ["ملف فارغ", "400"],
                ["نوع غير مدعوم", "415"],
                ["خطأ تحقق من الإعدادات", "422"],
                ["خطأ داخلي في الخط", "500"],
            ],
        ),
        p("مثال استدعاء:", size=22),
        code('curl -X POST http://localhost:8000/analyze \\'),
        code('  -F "file=@financial_statements.xlsx"'),
        p("1.3 GET /health", size=28, bold=True, color="0F7668", space_before=160),
        p("يعيد حالة الخدمة دون تشغيل المحرك.", size=22),
        code('{"status": "ok"}'),
        p("1.4 نموذج الاستجابة AuditReportOut", size=28, bold=True, color="0F7668", space_before=160),
        table(
            ["الحقل", "النوع", "الوصف"],
            [
                ["source_file", "string", "اسم الملف المصدر"],
                ["tables_processed", "int", "عدد الجداول المعالجة"],
                ["total_casting_errors", "int", "أخطاء الجمع العمودي/الأفقي"],
                ["total_cross_casting_errors", "int", "أخطاء التقاطع"],
                ["is_clean", "bool", "true إذا لم توجد أخطاء"],
                ["summary_ar", "string", "ملخص عربي"],
                ["summary_en", "string", "ملخص إنجليزي"],
                ["results", "list", "نتائج كل جدول"],
                ["findings", "list", "تفسيرات اختيارية من LLM"],
            ],
        ),
        p("ValidationResultOut", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["الحقل", "النوع", "الوصف"],
            [
                ["table_title", "string", "عنوان الجدول"],
                ["page", "int", "رقم الصفحة (PDF)"],
                ["sheet", "string | null", "اسم الورقة (Excel)"],
                ["casting_errors", "list", "أخطاء الصفوف/الأعمدة"],
                ["cross_casting_errors", "list", "أخطاء مجموع الصفوف مقابل الأعمدة"],
                ["disagreements", "int", "اختلافات OCR مقابل VLM"],
                ["total_verifications", "int", "عدد تحققات الخلايا"],
            ],
        ),
        p("CastingErrorOut", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["الحقل", "النوع", "الوصف"],
            [
                ["table", "string", "عنوان الجدول"],
                ["axis", "row | column", "محور الخطأ"],
                ["label", "string", "تسمية الصف أو العمود"],
                ["declared", "decimal | null", "القيمة المعلنة في المستند"],
                ["computed", "decimal", "القيمة المحسوبة بـ Python"],
                ["delta", "decimal", "الفرق"],
            ],
        ),
        p("CrossCastingErrorOut", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["الحقل", "النوع", "الوصف"],
            [
                ["table", "string", "عنوان الجدول"],
                ["description", "string", "وصف عدم الاتساق"],
                ["row_total", "decimal", "مجموع الصفوف"],
                ["col_total", "decimal", "مجموع الأعمدة"],
                ["grand_total", "decimal | null", "الإجمالي الكلي إن وُجد"],
                ["delta", "decimal", "الفرق"],
            ],
        ),
        p("FindingOut", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["الحقل", "النوع", "الوصف"],
            [
                ["severity", "string", "critical / high / medium / low"],
                ["finding_type", "string", "نوع الملاحظة"],
                ["description_ar", "string", "الوصف بالعربية"],
                ["description_en", "string", "الوصف بالإنجليزية"],
                ["table", "string", "الجدول المرتبط"],
                ["details", "string", "تفاصيل إضافية"],
            ],
        ),
        p("2. الخدمات الخارجية التي يستهلكها المحرك", size=36, bold=True, color="1B3A4B", space_before=280),
        p(
            "تُختار الخلفيات من ملف .env. الحساب لا يمر عبر هذه الخدمات؛ تُستخدم فقط لاستخراج "
            "الجداول من الصور/PDF ولتفسير الأخطاء بلغة المدقق.",
            size=22,
        ),
        p("2.1 VLM — استخراج الجداول", size=28, bold=True, color="0F7668", space_before=160),
        p("المتغير الحاكم: VLM_BACKEND = stub | local | anthropic", size=22),
        table(
            ["الخلفية", "المكتبة", "البروتوكول / العنوان", "الاستخدام"],
            [
                [
                    "local",
                    "openai (SDK)",
                    "OpenAI-compatible Chat Completions — http://localhost:11434/v1",
                    "استخراج جداول من صور الصفحات عبر نموذج بصري (مثل qwen2.5vl:7b على Ollama)",
                ],
                [
                    "anthropic",
                    "anthropic (SDK)",
                    "Anthropic Messages API — api.anthropic.com",
                    "استخراج جداول عبر Claude (افتراضي claude-haiku-5-5) مع أداة report_tables",
                ],
                ["stub", "بدون شبكة", "—", "اختبار محلي بلا استدعاء خارجي"],
            ],
        ),
        p("إعدادات VLM المحلي", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["المتغير", "الوصف", "الافتراضي"],
            [
                ["VLM_BACKEND", "اختيار المحرك", "stub"],
                ["VLM_BASE_URL", "رابط خادم OpenAI-compatible", "http://localhost:11434/v1"],
                ["VLM_MODEL", "اسم النموذج البصري", "qwen2.5vl:7b"],
                ["VLM_API_KEY", "مفتاح API (غير مطلوب محلياً)", "not-needed-locally"],
                ["VLM_JSON_MODE", "json_object | json_schema | none", "json_object"],
                ["VLM_TIMEOUT_SECONDS", "مهلة الطلب", "180"],
                ["VLM_MAX_TOKENS", "حد الرموز", "8192"],
                ["VLM_TEMPERATURE", "درجة العشوائية", "0.0"],
            ],
        ),
        p("الاستدعاء: chat.completions.create مع صورة الصفحة (base64) ونص استخراج الجدول.", size=22),
        p("يمكن توجيه VLM_BASE_URL إلى Ollama أو vLLM أو OpenAI الرسمي.", size=22),
        p("إعدادات Anthropic", size=24, bold=True, color="0F7668", space_before=120),
        table(
            ["المتغير", "الوصف", "الافتراضي"],
            [
                ["ANTHROPIC_API_KEY", "مفتاح Claude (مطلوب إذا VLM_BACKEND=anthropic)", "فارغ"],
                ["ANTHROPIC_MODEL", "اسم النموذج", "claude-haiku-5-5"],
            ],
        ),
        p("الاستدعاء: messages.create مع صورة base64 وأداة إجبارية اسمها report_tables.", size=22),
        p("2.2 LLM — تفسير أخطاء التدقيق (اختياري)", size=28, bold=True, color="0F7668", space_before=160),
        p("المتغير الحاكم: LLM_BACKEND = none | local", size=22),
        table(
            ["المتغير", "الوصف", "الافتراضي"],
            [
                ["LLM_BACKEND", "تفعيل المفسّر", "none"],
                ["LLM_BASE_URL", "رابط OpenAI-compatible", "http://localhost:11434/v1"],
                ["LLM_MODEL", "نموذج نصي", "qwen2.5:7b"],
                ["LLM_API_KEY", "مفتاح API", "not-needed-locally"],
                ["LLM_JSON_MODE", "json_object | none", "json_object"],
                ["LLM_TIMEOUT_SECONDS", "مهلة الطلب", "120"],
                ["LLM_MAX_TOKENS", "حد الرموز", "4096"],
            ],
        ),
        p(
            "الاستدعاء: chat.completions.create بنص الأخطاء JSON. إذا فشل الطلب يُستخدم تفسير احتياطي محلي. "
            "عند LLM_BACKEND=none لا يُستدعى أي نموذج.",
            size=22,
        ),
        p("2.3 OCR — تحقق على مستوى الخلية", size=28, bold=True, color="0F7668", space_before=160),
        p("ليس API سحابياً. يعمل محلياً عبر Tesseract.", size=22),
        table(
            ["الخلفية", "الوصف"],
            [
                ["tesseract", "pytesseract محلياً — OCR_LANGS=ara+eng"],
                ["stub", "نتائج وهمية للاختبار"],
                ["none", "تعطيل التحقق على مستوى الخلية"],
            ],
        ),
        p("3. مكتبات الاتصال", size=36, bold=True, color="1B3A4B", space_before=280),
        table(
            ["المكتبة", "الغرض"],
            [
                ["fastapi / uvicorn", "واجهة HTTP للخدمة"],
                ["openai >= 1.30", "VLM المحلي وLLM عبر بروتوكول OpenAI"],
                ["anthropic >= 0.28", "VLM عبر Claude"],
                ["pydantic / pydantic-settings", "نماذج الطلب/الاستجابة وإعدادات .env"],
                ["pytesseract", "OCR محلي"],
                ["pypdfium2 / Pillow / openpyxl", "عرض PDF وقراءة Excel — ليست APIs شبكية"],
            ],
        ),
        p("4. ملخص سريع", size=36, bold=True, color="1B3A4B", space_before=280),
        table(
            ["النوع", "ماذا يُستخدم"],
            [
                ["API معرّضة للعميل", "FastAPI: POST /analyze و GET /health"],
                ["VLM", "Ollama/OpenAI-compatible أو Anthropic Claude"],
                ["LLM", "Ollama/OpenAI-compatible اختياري لتفسير الأخطاء"],
                ["OCR", "Tesseract محلي"],
                ["الحساب", "Python Decimal داخل domain/ — بلا API"],
            ],
        ),
        p(
            "الملفات المرجعية في المشروع: api/routes.py، schemas/api.py، adapters/vlm_openai.py، "
            "adapters/vlm_anthropic.py، adapters/llm_interpreter.py، config/settings.py، .env.example.",
            size=22,
            space_before=160,
        ),
    ]

    body = "".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>
      <w:bidi/>
    </w:sectPr>
  </w:body>
</w:document>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/_rels/document.xml.rels", DOC_RELS)
        zf.writestr("word/styles.xml", STYLES)
        zf.writestr("word/document.xml", document_xml())
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
