# Cross-Casting — دليل الإعداد

مرشد سريع لتشغيل المشروع من الصفر على جهاز جديد. الوقت المتوقع: 15 دقيقة.

---

## 1. المتطلبات

- **Docker Desktop** (مع Colima أو Docker Engine).
- **Git**.
- 8 GB RAM على الأقل، 16 GB إذا كنت ستشغّل موديل محلي بدل Claude.

---

## 2. تنزيل المشروع

```bash
git clone https://github.com/fadi-arto/cross-casting.git
cd cross-casting
```

---

## 3. تجهيز ملف `.env`

انسخ المثال وعدّله:

```bash
cp .env.example .env
```

افتح `.env` واختر وضع تشغيل الموديل: **Claude (أسرع وأدق، مدفوع)** أو **موديل محلي (مجاني، أبطأ)**.

### الوضع الأول: Claude (الموصى به للعمل الجاد)

```bash
VLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-ضع-مفتاحك-هنا
ANTHROPIC_MODEL=claude-haiku-5-5
```

احصل على المفتاح من [console.anthropic.com](https://console.anthropic.com). التكلفة ~ 10 سنت لكل ملف 50 صفحة.

### الوضع الثاني: موديل محلي (Ollama + Qwen)

ثبّت Ollama أولاً من [ollama.com](https://ollama.com)، ثم:

```bash
ollama pull qwen2.5vl:7b
ollama serve
```

في `.env`:

```bash
VLM_BACKEND=local
VLM_BASE_URL=http://host.docker.internal:11434/v1
VLM_MODEL=qwen2.5vl:7b
VLM_API_KEY=not-needed-locally
```

> **تنبيه**: Qwen أبطأ من Claude (10–30 ثانية للصفحة) ودقته في الجداول العربية الممسوحة أقل. مناسب للاختبار والتطوير.

### الإعدادات الباقية

اتركها كما في `.env.example`. المهم أن تغيّر فقط كلمات السر الافتراضية إذا كنت ستنشره على سيرفر إنتاج:

```bash
DB_PASSWORD=غيّرها
MINIO_SECRET_KEY=غيّرها
```

### Frontend

لو الـ frontend على منفذ غير 5173، أضف عنوانه:

```bash
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

ولو ستفتح الملفات من متصفح خارجي:

```bash
MINIO_PUBLIC_ENDPOINT=localhost:9010
```

---

## 4. التشغيل

```bash
docker compose up -d --build
```

انتظر حوالي دقيقة ثم تأكد:

```bash
curl http://localhost:8001/health
# يجب أن يرجع: {"status":"ok","database":"ok","storage":"ok"}
```

---

## 5. العناوين

| الخدمة | العنوان |
|---|---|
| الـ API | http://localhost:8001 |
| الوثائق التفاعلية | http://localhost:8001/docs |
| MinIO (ملفات التخزين) | http://localhost:9011 |

---

## 6. اختبار سريع

ارفع ملفاً وتابع حالته:

```bash
# ارفع ملف
curl -X POST http://localhost:8001/analyze \
  -F "file=@path/to/report.pdf"
# → يرجع: {"job_id":"...","status":"QUEUED"}

# راجع الحالة
curl http://localhost:8001/jobs/JOB_ID

# اقرأ النتائج بعد ما يصير COMPLETED
curl http://localhost:8001/jobs/JOB_ID/review
```

---

## 7. مشاكل شائعة

**`/health` يرجع `database: fail`**
انتظر 30 ثانية إضافية، قاعدة البيانات بطيئة الإقلاع أول مرة.

**`Anthropic authentication failed (401)`**
مفتاح `ANTHROPIC_API_KEY` خطأ أو فارغ.

**`VLM_BACKEND=stub: no tables are read`**
نسيت تغيير `VLM_BACKEND` من الافتراضي `stub` إلى `anthropic` أو `local`.

**`PDF has 66 pages, exceeds limit of 50`**
ارفع `MAX_PDF_PAGES` في `.env` (الحد الأقصى 100).

**الصور المحلية لا تظهر في زر «Open source»**
تأكد أن `MINIO_PUBLIC_ENDPOINT` موجود في `.env` وأن المنفذ 9010 مفتوح.

**Ollama لا يستجيب من داخل Docker**
استخدم `http://host.docker.internal:11434/v1` بدل `localhost`. على Linux أضف `--add-host=host.docker.internal:host-gateway` في docker-compose أو استخدم `http://172.17.0.1:11434/v1`.

---

## 8. التحديث لاحقاً

```bash
git pull
docker compose up -d --build
```

إذا تغيّر `.env.example` قارن معه ملفك `.env` وأضف ما نقص.

---

## 9. إيقاف

```bash
docker compose down            # يوقف الخدمات، يحفظ البيانات
docker compose down -v         # يحذف كل شيء (قاعدة البيانات والملفات)
```

---

## 10. وثائق إضافية داخل المشروع

- [docs/Frontend-Prompt.md](Frontend-Prompt.md): مواصفات واجهة الـ frontend الكاملة.
- [docs/Frontend-Changes-Today.md](Frontend-Changes-Today.md): تعديلات جلسة اليوم.
- [docs/Cross-Casting-Brief.md](Cross-Casting-Brief.md): المرجع السريع بالعربي للمنطق.
- [scripts/eval_against_key.py](../scripts/eval_against_key.py): سكربت تقييم ضد مفاتيح إجابات.
