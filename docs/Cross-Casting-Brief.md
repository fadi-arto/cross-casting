# Cross-Casting — Quick Brief (للنقل إلى مشروع آخر)

تحقق حسابي مستقل من الجداول المالية (PDF / Excel / صور). **الحساب كله Python `Decimal`؛ الـ LLM/VLM للقراءة والتفسير فقط.**

## 1. المنطق الصحيح (Logic)

| الفحص | القاعدة | الملف |
|---|---|---|
| **Vertical casting** | لكل صف إجمالي/فرعي ولكل عمود: `declared == Σ(sign × مكوّناته)` (مكوّناته من `structure` وليس "جمع كل العمود") | `domain/casting.py` |
| **Horizontal casting** | عمود الإجمالي أو الفرق (Movement = 2025−2024) : `declared == Σ(sign × أعمدة الصف)` | `domain/casting.py` |
| **Trial balance** | إجمالي المدين == إجمالي الدائن | `domain/casting.py` |
| **Cross-casting** | لكل صف إجمالي له مكوّنات + عمود إجمالي: `Σ row-totals(المكوّنات) == Σ قيم صف الإجمالي عبر الفترات == Grand total` (3 مقارنات: row/col، row/grand، col/grand) | `domain/cross_casting.py` |
| **Tolerance** | `|delta| <= ABS_TOLERANCE` (افتراضي 0.01) | `domain/money.py` |

قواعد مهمة:
- كل إجمالي يُقارن بمكوّناته فقط (Total assets مع بنود الأصول…) فلا يُعدّ شيء مرتين.
- الصيغة غير المؤكدة لا تنتج خطأ، تنتج **warning** فقط.
- Cross-casting يحتاج عمودَي قيم على الأقل + عمود إجمالي.
- إعادة مقارنة الأرقام تتم بـ `Decimal` مع `fold_digits` (أرقام عربية/هندية) وإشارات الأقواس/السالب.
- العدّ: كل مقارنة (ناجحة أو فاشلة) تُحسب في `casting_checks` / `cross_casting_checks`.

### أرقام الصفحات
**لا تتم مقارنة أرقام الصفحات المطبوعة.** رقم الصفحة يُستخدم فقط داخلياً (1) لربط خلية الـ VLM بنتيجة OCR لنفس الصفحة، (2) لدمج جدول مستمر على الصفحة التالية (`application/page_merge.py`: صفحة N+1 مباشرة + نفس الأعمدة + علامة continues/نفس العنوان)، (3) كمرجع للمصدر في الأخطاء. لا يوجد فحص تسلسل ولا تطابق "Page X of Y".

## 2. الـ Pipeline

```
Excel  → ExcelReader ───────────────┐
PDF/IMG→ PdfRenderer → VLM (لكل صفحة)→ page_merge ┐
                     └→ OCR (اختياري) ────────────┤
                                                  ▼
                      analyze_structure → Casting → CrossCasting
                      → consolidate_findings → (OCR vs VLM per cell)
                      → (LLM interpreter اختياري) → AuditReport
```
Job: `QUEUED → PROCESSING → COMPLETED | FAILED`
Stages: `DOCUMENT_LOADING → TABLE_EXTRACTION → NUMBER_NORMALIZATION → OCR_VLM_VERIFICATION → CASTING → CROSS_CASTING → REPORT_GENERATION`

## 3. REST API (FastAPI) — `api/routes.py`

| Method | Path | الوظيفة |
|---|---|---|
| POST | `/analyze` | رفع ملف (`file`: pdf/xlsx/xls/png/jpg، ≤100MB) → 202 + `job_id` |
| POST | `/analyze/batch` | رفع مجلد/zip (`files` متكرر، ≤500 ملف، zip ≤500MB) → `batch_id` |
| GET | `/batches/{batch_id}` | تقدم الدفعة: QUEUED/PROCESSING/COMPLETED/PARTIALLY_FAILED/FAILED |
| GET | `/jobs` | قائمة (`page,page_size,status,has_errors,batch_id`) |
| GET | `/jobs/{id}` | الحالة + التقرير + `error_message` |
| GET | `/jobs/{id}/results` · `/report` | النتائج/التقرير (409 قبل COMPLETED) |
| GET | `/jobs/{id}/errors` | الجداول التي فيها أخطاء فقط |
| GET | `/jobs/{id}/issues` | فلترة: `issue_type,severity,status,table_id,sort_by,sort_dir` |
| GET | `/jobs/{id}/issues/{issue_id}` | تفاصيل خطأ + `source_reference` (page/bbox أو sheet/cell) |
| GET | `/jobs/{id}/tables` · `/tables/{table_id}` | الجداول المستخرجة + `raw_data` |
| GET | `/jobs/{id}/logs` | سجل المراحل |
| GET | `/jobs/{id}/file` | presigned URL للملف الأصلي |
| GET | `/jobs/{id}/export` | تنزيل التقرير JSON |
| POST | `/jobs/{id}/retry` | إعادة تشغيل FAILED/COMPLETED (409 غير ذلك) |
| DELETE | `/jobs/{id}` | حذف الـ job + الملف (204) |
| GET | `/dashboard/stats` | إحصاءات |
| GET | `/health` | DB + MinIO (200/503) |

Issue types: `CASTING_ERROR | CROSS_CASTING_ERROR | OCR_VLM_DISAGREEMENT | EXTRACTION_ERROR | TABLE_STRUCTURE_ERROR | HUMAN_REVIEW`
شكل الخطأ: `{table, axis, label, declared, computed, delta, row_label, relation, cell_ref, evidence}` — cross: `{row_total, col_total, grand_total, delta}`.

## 4. الخدمات الخارجية المستخدمة

| الخدمة | الاستخدام | الإعداد (.env) |
|---|---|---|
| **Anthropic Messages API** (`anthropic` SDK) | VLM: صورة صفحة → جداول JSON. الافتراضي `claude-haiku-5-5` | `VLM_BACKEND=anthropic`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`, `ANTHROPIC_MAX_RETRIES` |
| **OpenAI-compatible Chat Completions** (Ollama/LM Studio/Gemini proxy) | VLM محلي (`image_url` base64) | `VLM_BACKEND=local`, `VLM_BASE_URL`, `VLM_MODEL`, `VLM_JSON_MODE` |
| **OpenAI-compatible LLM** | تفسير الأخطاء (اختياري، لا يحسب) | `LLM_BACKEND=local`, `LLM_BASE_URL`, `LLM_MODEL` |
| **Tesseract** (`pytesseract`) | OCR للتحقق على مستوى الخلية | `OCR_BACKEND=tesseract`, `OCR_LANGS=ara+eng` |
| **PostgreSQL 16** (SQLAlchemy/Alembic) | jobs, issues, tables, logs | `DB_HOST/PORT/NAME/USER/PASSWORD` |
| **MinIO / S3** | الملف الأصلي + presigned URL | `MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET` |
| pypdfium2 / Pillow / openpyxl | رندر PDF / صور / قراءة Excel | `RENDER_DPI=150`, `MAX_PDF_PAGES=50` |

ملاحظات:
- فشل استدعاء Anthropic = فشل الـ job (لا fallback صامت إلى stub).
- `VLM_BACKEND=stub` يعطي تقريراً فارغاً للـ PDF — يُحذَّر منه في اللوج.
- لا تضع المفتاح في الواجهة ولا في git.

## 5. كيف تنقله لمشروع آخر
1. انسخ `domain/` (stdlib فقط) + `ports/` — هذا هو قلب المنطق.
2. انسخ `adapters/` و`application/` حسب الحاجة (Excel فقط = لا تحتاج VLM/OCR).
3. استخدم مباشرة:
```python
pipeline = build_pipeline(get_settings())
tables = read_excel(bytes_, "file.xlsx")
report = pipeline.process_tables(tables)   # PDF: pipeline.process_pdf(bytes_)
```
4. الـ API/DB/MinIO اختيارية (`api/`, `engine/`, `db/`, `storage/`).
