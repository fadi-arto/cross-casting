# Cross-Casting Audit Engine

تحقق حسابي مستقل من الجداول المالية (PDF / Excel) باستخدام تقنيات VLM + OCR + LLM.

---

## البنية المعمارية — Hexagonal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  domain/          ← Pure Python. Zero external deps.            │
│  ┌────────────┐   ┌────────────┐   ┌──────────────────────────┐ │
│  │  models.py │   │ casting.py │   │  cross_casting.py        │ │
│  │  money.py  │   │ crosscheck │   │  normalize.py            │ │
│  └────────────┘   └────────────┘   └──────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  ports/           ← Protocol interfaces only (no implementations)│
│  renderer · ocr · reader (VLM) · interpreter (LLM)              │
├─────────────────────────────────────────────────────────────────┤
│  adapters/        ← All external library code lives here        │
│  pdf_renderer · excel_reader · ocr_tesseract · vlm_openai       │
│  vlm_anthropic · llm_interpreter                                │
├─────────────────────────────────────────────────────────────────┤
│  application/     ← Orchestration only (wires ports together)   │
│  pipeline · factory                                             │
├─────────────────────────────────────────────────────────────────┤
│  api/ · schemas/ · config/ · prompts/                           │
└─────────────────────────────────────────────────────────────────┘
```

**لماذا هذه البنية أفضل؟**

| المبدأ | التطبيق |
|--------|---------|
| **Zero-dep domain** | `domain/` تستورد من stdlib فقط → اختبارها بلا أي API |
| **Ports & Adapters** | يمكن استبدال Tesseract بـ AWS Textract دون لمس الـ domain |
| **Single responsibility** | كل محرك يفعل شيئاً واحداً (casting vs cross-casting vs crosscheck) |
| **LLM لا يحسب** | الحساب كله بـ Python Decimal، LLM فقط يفسّر الأخطاء |

---

## تدفق المعالجة

```
PDF / Excel
    │
    ▼
[PDF]  PdfRenderer → صور الصفحات
    ├── OCR (Tesseract) → كلمات + إحداثيات
    └── VLM (Claude/Ollama) → جداول منظَّمة
    │
[Excel] ExcelReader → جداول مباشرة بلا VLM
    │
    ▼
CastingEngine     → تحقق عمودي/أفقي (مجموع صفوف/أعمدة)
    │
    ▼
CrossCastingEngine → تحقق التقاطع (مجموع صفوف = مجموع أعمدة = إجمالي كلي)
    │
    ▼
[اختياري] CrossCheck OCR+VLM → تحقق على مستوى الخلية
    │
    ▼
[اختياري] LLM Interpreter → تفسير الأخطاء بلغة المدقق
    │
    ▼
AuditReport (JSON)
```

---

## التثبيت

```bash
# نسخ المجلد إلى أي مكان
cp -r Cross-Casting /path/to/new/location
cd /path/to/new/location/Cross-Casting

# إنشاء بيئة افتراضية
python3 -m venv .venv
source .venv/bin/activate

# تثبيت المتطلبات
pip install -r requirements.txt

# إعداد المتغيرات
cp .env.example .env
# عدّل .env حسب بيئتك
```

---

## الاستخدام

### CLI

```bash
# Excel (لا يحتاج VLM)
python main.py financial_statements.xlsx

# PDF مع VLM محلي (Ollama)
VLM_BACKEND=local VLM_MODEL=qwen2.5vl:7b python main.py report.pdf

# PDF مع Claude
VLM_BACKEND=anthropic ANTHROPIC_API_KEY=sk-... python main.py report.pdf

# حفظ النتيجة
python main.py report.xlsx --output results.json
```

### API

```bash
uvicorn api.routes:app --reload
# POST http://localhost:8000/analyze  (multipart file upload)
```

### Python

```python
from adapters.excel_reader import read_excel
from application import build_pipeline
from config.settings import get_settings

settings = get_settings()
pipeline = build_pipeline(settings)

tables = read_excel(Path("financial.xlsx").read_bytes(), "financial.xlsx")
report = pipeline.process_tables(tables)

print(report.summary_en)
# Found 2 casting error(s) and 1 cross-casting error(s) across 3 table(s).
```

---

## الاختبارات

```bash
# اختبارات الـ domain (بلا أي API)
pytest tests/unit/domain/ -v

# اختبارات محوّل Excel
pytest tests/unit/adapters/ -v
```

---

## إعدادات .env

| المتغير | الوصف | القيمة الافتراضية |
|---------|-------|-----------------|
| `VLM_BACKEND` | `stub` / `local` / `anthropic` | `stub` |
| `VLM_BASE_URL` | رابط خادم Ollama أو OpenAI-compat | `http://localhost:11434/v1` |
| `VLM_MODEL` | اسم النموذج البصري | `qwen2.5vl:7b` |
| `ANTHROPIC_API_KEY` | مفتاح API لـ Claude | — |
| `OCR_BACKEND` | `none` / `stub` / `tesseract` | `stub` |
| `LLM_BACKEND` | `none` / `local` | `none` |
| `ABS_TOLERANCE` | التسامح في الحساب | `0.01` |

---

## المتطلبات

- Python 3.11+
- Tesseract (اختياري، لـ OCR): `apt install tesseract-ocr tesseract-ocr-ara`
- Ollama (اختياري، للنموذج المحلي): `ollama pull qwen2.5vl:7b`
