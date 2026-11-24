"""Generate a Word document documenting Cross-Casting APIs."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


OUT = Path(__file__).with_name("Cross-Casting-APIs.docx")
NAVY = RGBColor(0x1B, 0x3A, 0x4B)
ACCENT = RGBColor(0x0F, 0x76, 0x68)
MUTED = RGBColor(0x4B, 0x55, 0x63)


def set_run_font(run, *, size=12, bold=False, color=None, name="Calibri"):
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color is not None:
        run.font.color.rgb = color


def set_rtl(paragraph, align=WD_ALIGN_PARAGRAPH.RIGHT):
    paragraph.alignment = align
    pPr = paragraph._p.get_or_add_pPr()
    bidi = pPr.find(qn("w:bidi"))
    if bidi is None:
        bidi = OxmlElement("w:bidi")
        pPr.append(bidi)
    bidi.set(qn("w:val"), "1")


def add_heading_ar(doc, text, level=1):
    p = doc.add_paragraph()
    set_rtl(p, WD_ALIGN_PARAGRAPH.RIGHT)
    run = p.add_run(text)
    size = {1: 22, 2: 16, 3: 13}.get(level, 12)
    set_run_font(run, size=size, bold=True, color=NAVY if level == 1 else ACCENT)
    p.paragraph_format.space_before = Pt(16 if level == 1 else 12)
    p.paragraph_format.space_after = Pt(6)
    return p


def add_body(doc, text):
    p = doc.add_paragraph()
    set_rtl(p)
    run = p.add_run(text)
    set_run_font(run, size=12, color=MUTED)
    p.paragraph_format.space_after = Pt(6)
    return p


def add_ltr_code(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.left_indent = Cm(0.5)
    run = p.add_run(text)
    set_run_font(run, size=10, name="Consolas", color=NAVY)
    p.paragraph_format.space_after = Pt(4)
    return p


def shade_cell(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def add_table(doc, headers, rows, rtl=True):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        if rtl:
            set_rtl(p, WD_ALIGN_PARAGRAPH.CENTER)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(header)
        set_run_font(run, size=11, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        shade_cell(cell, "1B3A4B")
    for r_idx, row in enumerate(rows):
        fill = "F4F7F7" if r_idx % 2 == 0 else "FFFFFF"
        for c_idx, value in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = ""
            p = cell.paragraphs[0]
            if rtl:
                set_rtl(p, WD_ALIGN_PARAGRAPH.RIGHT)
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(str(value))
            set_run_font(run, size=10, color=MUTED)
            shade_cell(cell, fill)
    doc.add_paragraph()
    return table


def build():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)

    title = doc.add_paragraph()
    set_rtl(title, WD_ALIGN_PARAGRAPH.CENTER)
    run = title.add_run("Cross-Casting")
    set_run_font(run, size=28, bold=True, color=NAVY)

    subtitle = doc.add_paragraph()
    set_rtl(subtitle, WD_ALIGN_PARAGRAPH.CENTER)
    run = subtitle.add_run("توثيق واجهات البرمجة (APIs)")
    set_run_font(run, size=18, bold=True, color=ACCENT)

    meta = doc.add_paragraph()
    set_rtl(meta, WD_ALIGN_PARAGRAPH.CENTER)
    run = meta.add_run("محرك تحقق حسابي مستقل من الجداول المالية — الإصدار 1.0.0")
    set_run_font(run, size=11, color=MUTED)

    add_body(
        doc,
        "يغطي هذا الملف واجهة الخدمة التي يعرّضها المشروع (FastAPI)، والخدمات الخارجية "
        "التي يستهلكها المحرك لاستخراج الجداول وتفسير الأخطاء. الحساب نفسه يتم داخل "
        "Python بـ Decimal ولا يعتمد على أي API خارجي.",
    )

    add_heading_ar(doc, "1. واجهة الخدمة (FastAPI)", 1)
    add_body(doc, "التشغيل المحلي:")
    add_ltr_code(doc, "uvicorn api.routes:app --reload")
    add_body(doc, "العنوان الافتراضي: http://localhost:8000")
    add_body(doc, "التوثيق التفاعلي: http://localhost:8000/docs")

    add_heading_ar(doc, "1.1 المسارات", 2)
    add_table(
        doc,
        ["المسار", "الطريقة", "الوصف"],
        [
            ["/analyze", "POST", "رفع ملف وتحليل الجداول المالية"],
            ["/health", "GET", "فحص أن الخدمة تعمل"],
            ["/docs", "GET", "توثيق Swagger التلقائي"],
            ["/openapi.json", "GET", "مواصفات OpenAPI"],
        ],
    )

    add_heading_ar(doc, "1.2 POST /analyze", 2)
    add_body(doc, "يرفع ملفاً واحداً (multipart/form-data) ويشغّل خط المعالجة ثم يعيد تقرير تدقيق JSON.")
    add_table(
        doc,
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
    )
    add_body(doc, "مثال استدعاء:")
    add_ltr_code(doc, 'curl -X POST http://localhost:8000/analyze \\')
    add_ltr_code(doc, '  -F "file=@financial_statements.xlsx"')

    add_heading_ar(doc, "1.3 GET /health", 2)
    add_body(doc, "يعيد حالة الخدمة دون تشغيل المحرك.")
    add_ltr_code(doc, '{"status": "ok"}')

    add_heading_ar(doc, "1.4 نموذج الاستجابة AuditReportOut", 2)
    add_table(
        doc,
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
    )

    add_heading_ar(doc, "ValidationResultOut", 3)
    add_table(
        doc,
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
    )

    add_heading_ar(doc, "CastingErrorOut", 3)
    add_table(
        doc,
        ["الحقل", "النوع", "الوصف"],
        [
            ["table", "string", "عنوان الجدول"],
            ["axis", "row | column", "محور الخطأ"],
            ["label", "string", "تسمية الصف أو العمود"],
            ["declared", "decimal | null", "القيمة المعلنة في المستند"],
            ["computed", "decimal", "القيمة المحسوبة بـ Python"],
            ["delta", "decimal", "الفرق"],
        ],
    )

    add_heading_ar(doc, "CrossCastingErrorOut", 3)
    add_table(
        doc,
        ["الحقل", "النوع", "الوصف"],
        [
            ["table", "string", "عنوان الجدول"],
            ["description", "string", "وصف عدم الاتساق"],
            ["row_total", "decimal", "مجموع الصفوف"],
            ["col_total", "decimal", "مجموع الأعمدة"],
            ["grand_total", "decimal | null", "الإجمالي الكلي إن وُجد"],
            ["delta", "decimal", "الفرق"],
        ],
    )

    add_heading_ar(doc, "FindingOut", 3)
    add_table(
        doc,
        ["الحقل", "النوع", "الوصف"],
        [
            ["severity", "string", "critical / high / medium / low"],
            ["finding_type", "string", "نوع الملاحظة"],
            ["description_ar", "string", "الوصف بالعربية"],
            ["description_en", "string", "الوصف بالإنجليزية"],
            ["table", "string", "الجدول المرتبط"],
            ["details", "string", "تفاصيل إضافية"],
        ],
    )

    add_heading_ar(doc, "2. الخدمات الخارجية التي يستهلكها المحرك", 1)
    add_body(
        doc,
        "تُختار الخلفيات من ملف .env. الحساب لا يمر عبر هذه الخدمات؛ تُستخدم فقط لاستخراج "
        "الجداول من الصور/PDF ولتفسير الأخطاء بلغة المدقق.",
    )

    add_heading_ar(doc, "2.1 VLM — استخراج الجداول", 2)
    add_body(doc, "المتغير الحاكم: VLM_BACKEND = stub | local | anthropic")
    add_table(
        doc,
        ["الخلفية", "المكتبة", "البروتوكول / العنوان", "الاستخدام"],
        [
            [
                "local",
                "openai (SDK)",
                "OpenAI-compatible Chat Completions — افتراضي http://localhost:11434/v1",
                "استخراج جداول من صور الصفحات عبر نموذج بصري (مثل qwen2.5vl:7b على Ollama)",
            ],
            [
                "anthropic",
                "anthropic (SDK)",
                "Anthropic Messages API — api.anthropic.com",
                "استخراج جداول عبر Claude (افتراضي claude-haiku-5-5) مع أداة report_tables",
            ],
            [
                "stub",
                "بدون شبكة",
                "—",
                "اختبار محلي بلا استدعاء خارجي",
            ],
        ],
    )

    add_heading_ar(doc, "إعدادات VLM المحلي", 3)
    add_table(
        doc,
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
    )
    add_body(doc, "الاستدعاء: chat.completions.create مع صورة الصفحة (base64) ونص استخراج الجدول.")
    add_body(doc, "يمكن توجيه VLM_BASE_URL إلى Ollama أو vLLM أو OpenAI الرسمي.")

    add_heading_ar(doc, "إعدادات Anthropic", 3)
    add_table(
        doc,
        ["المتغير", "الوصف", "الافتراضي"],
        [
            ["ANTHROPIC_API_KEY", "مفتاح Claude (مطلوب إذا VLM_BACKEND=anthropic)", "فارغ"],
            ["ANTHROPIC_MODEL", "اسم النموذج", "claude-haiku-5-5"],
        ],
    )
    add_body(doc, "الاستدعاء: messages.create مع صورة base64 وأداة إجبارية اسمها report_tables.")

    add_heading_ar(doc, "2.2 LLM — تفسير أخطاء التدقيق (اختياري)", 2)
    add_body(doc, "المتغير الحاكم: LLM_BACKEND = none | local")
    add_table(
        doc,
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
    )
    add_body(
        doc,
        "الاستدعاء: chat.completions.create بنص الأخطاء JSON. إذا فشل الطلب يُستخدم تفسير احتياطي محلي. "
        "عند LLM_BACKEND=none لا يُستدعى أي نموذج.",
    )

    add_heading_ar(doc, "2.3 OCR — تحقق على مستوى الخلية", 2)
    add_body(doc, "ليس API سحابياً. يعمل محلياً عبر Tesseract.")
    add_table(
        doc,
        ["الخلفية", "الوصف"],
        [
            ["tesseract", "pytesseract محلياً — OCR_LANGS=ara+eng"],
            ["stub", "نتائج وهمية للاختبار"],
            ["none", "تعطيل التحقق على مستوى الخلية"],
        ],
    )

    add_heading_ar(doc, "3. مكتبات الاتصال", 1)
    add_table(
        doc,
        ["المكتبة", "الغرض"],
        [
            ["fastapi / uvicorn", "واجهة HTTP للخدمة"],
            ["openai >= 1.30", "VLM المحلي وLLM عبر بروتوكول OpenAI"],
            ["anthropic >= 0.28", "VLM عبر Claude"],
            ["pydantic / pydantic-settings", "نماذج الطلب/الاستجابة وإعدادات .env"],
            ["pytesseract", "OCR محلي"],
            ["pypdfium2 / Pillow / openpyxl", "عرض PDF وقراءة Excel — ليست APIs شبكية"],
        ],
    )

    add_heading_ar(doc, "4. ملخص سريع", 1)
    add_table(
        doc,
        ["النوع", "ماذا يُستخدم"],
        [
            ["API معرّضة للعميل", "FastAPI: POST /analyze و GET /health"],
            ["VLM", "Ollama/OpenAI-compatible أو Anthropic Claude"],
            ["LLM", "Ollama/OpenAI-compatible اختياري لتفسير الأخطاء"],
            ["OCR", "Tesseract محلي"],
            ["الحساب", "Python Decimal داخل domain/ — بلا API"],
        ],
    )

    add_body(
        doc,
        "الملفات المرجعية في المشروع: api/routes.py، schemas/api.py، adapters/vlm_openai.py، "
        "adapters/vlm_anthropic.py، adapters/llm_interpreter.py، config/settings.py، .env.example.",
    )

    doc.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
