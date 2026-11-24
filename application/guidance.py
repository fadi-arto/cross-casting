"""
Layer: Application — bilingual wording that tells a reviewer how to resolve a finding.

Pure text + small data-driven hints; no I/O.  Every entry is an (en, ar) pair so
the frontend never composes sentences.  The arithmetic itself is never done here —
hints only interpret numbers the engine already produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

Pair = tuple[str, str]  # (en, ar)


@dataclass(frozen=True)
class Guidance:
    title: Pair
    explanation: Pair
    causes: tuple[Pair, ...] = ()
    steps: tuple[Pair, ...] = ()


# ── Shared steps ──────────────────────────────────────────────────────────────

_S_OPEN_SOURCE = (
    "Open the source document at the location shown and read the figure yourself "
    "(PDF/image: the page and row; Excel: the cell).",
    "افتح المستند الأصلي عند الموضع الموضّح واقرأ الرقم بنفسك "
    "(PDF/صورة: الصفحة والصف، Excel: الخلية).",
)
_S_CONFIRM_READ = (
    "Confirm the extracted figure matches the document. If the extraction misread it "
    "(digit, bracket for a negative, thousands separator), mark the finding as "
    "'resolved' and re-run the job after correcting the source file or scan quality.",
    "تأكد أن الرقم المستخرج مطابق للمستند. إذا أخطأت القراءة (رقم، قوسان للسالب، "
    "فاصل الآلاف) فاجعل الحالة «resolved» وأعد تشغيل المهمة بعد تصحيح الملف أو جودة المسح.",
)
_S_FALSE_POSITIVE = (
    "If the figures are right and this line is not meant to be a sum of the lines "
    "shown (summary or partial table, a line from a different section, columns of "
    "different years), mark the finding 'acknowledged' with that reason in your notes.",
    "إذا كانت الأرقام صحيحة وهذا البند ليس مقصوداً به أن يكون مجموع البنود الظاهرة "
    "(جدول ملخّص أو جزئي، بند من قسم آخر، أعمدة لسنوات مختلفة) فاجعل الحالة "
    "«acknowledged» مع ذكر السبب في ملاحظاتك.",
)
_S_RERUN = (
    "After correcting the source, re-run the job (POST /jobs/{id}/retry) and confirm "
    "the finding no longer appears.",
    "بعد تصحيح المصدر أعد تشغيل المهمة (POST /jobs/{id}/retry) وتأكد أن الخطأ لم يعد ظاهراً.",
)

# ── Catalog ───────────────────────────────────────────────────────────────────

CATALOG: dict[str, Guidance] = {
    "vertical": Guidance(
        title=("Total does not equal the sum of its lines",
               "الإجمالي لا يساوي مجموع بنوده"),
        explanation=(
            "The declared total in this column differs from the total recomputed from "
            "the lines that make it up (the 'relation' shows which lines).",
            "الإجمالي المعلن في هذا العمود يختلف عن الإجمالي المحسوب من البنود المكوِّنة له "
            "(حقل «العلاقة» يوضّح البنود).",
        ),
        causes=(
            ("A component line is missing, duplicated or has the wrong amount.",
             "بند من المكوّنات ناقص أو مكرر أو مبلغه خاطئ."),
            ("The total itself was typed or carried forward incorrectly.",
             "الإجمالي نفسه كُتب أو رُحِّل بشكل خاطئ."),
            ("A deduction was entered as an addition (or the reverse).",
             "بند مخصوم أُدخل كإضافة (أو العكس)."),
            ("The table extraction misread a figure.",
             "قراءة الجدول أخطأت في أحد الأرقام."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Re-add the lines named in the relation and compare with the computed value.",
             "أعد جمع البنود المذكورة في العلاقة وقارنها بالقيمة المحسوبة."),
            _S_CONFIRM_READ,
            ("If a line is wrong or missing, correct that line; if the lines are right, "
             "correct the total. Do not change both to force a match.",
             "إذا كان بند خاطئاً أو ناقصاً فصحّحه؛ وإذا كانت البنود صحيحة فصحّح الإجمالي. "
             "لا تعدّل الاثنين لإجبار التطابق."),
            _S_FALSE_POSITIVE,
            _S_RERUN,
        ),
    ),
    "horizontal": Guidance(
        title=("Row total does not equal the sum of its columns",
               "إجمالي الصف لا يساوي مجموع أعمدته"),
        explanation=(
            "In this row, the total column differs from the sum of the period/value "
            "columns (or from their difference, when the column is a movement).",
            "في هذا الصف يختلف عمود الإجمالي عن مجموع أعمدة الفترات/القيم "
            "(أو عن الفرق بينها إذا كان العمود حركة).",
        ),
        causes=(
            ("One period/value cell in the row is wrong or missing.",
             "إحدى خلايا الفترات/القيم في الصف خاطئة أو ناقصة."),
            ("The row total was not updated after a figure changed.",
             "إجمالي الصف لم يُحدَّث بعد تغيّر أحد الأرقام."),
            ("The column is a derived 'movement' (e.g. 2025 − 2024) and was read as a sum.",
             "العمود «حركة» مشتقة (مثل 2025 − 2024) وقُرئ كمجموع."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Add the period/value cells of this row and compare with the row total.",
             "اجمع خلايا الفترات/القيم في هذا الصف وقارنها بإجمالي الصف."),
            _S_CONFIRM_READ,
            ("Correct the cell that is actually wrong (the one that disagrees with its "
             "own supporting schedule), not the whole row.",
             "صحّح الخلية الخاطئة فعلاً (التي تخالف جدولها المساند) وليس الصف كله."),
            _S_FALSE_POSITIVE,
            _S_RERUN,
        ),
    ),
    "trial_balance": Guidance(
        title=("Trial balance does not balance", "ميزان المراجعة غير متوازن"),
        explanation=(
            "Total debits and total credits in this trial balance are not equal.",
            "إجمالي المدين وإجمالي الدائن في ميزان المراجعة هذا غير متساويين.",
        ),
        causes=(
            ("A one-sided entry, or an account missing from the list.",
             "قيد من طرف واحد، أو حساب ناقص من القائمة."),
            ("A balance posted to the wrong side (debit vs credit).",
             "رصيد رُحِّل إلى الجهة الخاطئة (مدين/دائن)."),
            ("A transposed or misread figure.",
             "رقم مقلوب الخانات أو مقروء خطأً."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Compare each account's side and amount with the ledger; start with the "
             "largest accounts and any account that moved this period.",
             "قارن جهة ومبلغ كل حساب بدفتر الأستاذ، وابدأ بأكبر الحسابات وبأي حساب تحرّك في الفترة."),
            _S_CONFIRM_READ,
            _S_RERUN,
        ),
    ),
    "row_vs_col": Guidance(
        title=("Row totals and column totals do not agree",
               "إجماليات الصفوف لا تطابق إجماليات الأعمدة"),
        explanation=(
            "Cross-casting: the sum of the component rows' totals should equal the sum "
            "of this total row across the periods. They differ, so at least one figure "
            "in the block is wrong.",
            "التدقيق المتقاطع: مجموع إجماليات الصفوف المكوِّنة يجب أن يساوي مجموع صف الإجمالي "
            "عبر الفترات. هما مختلفان، إذن رقم واحد على الأقل في هذه المجموعة خاطئ.",
        ),
        causes=(
            ("A single wrong cell breaks both directions at once.",
             "خلية واحدة خاطئة تكسر الاتجاهين معاً."),
            ("A component row is missing from the block or counted in the wrong block.",
             "صف مكوِّن ناقص من المجموعة أو محسوب في مجموعة غير صحيحة."),
        ),
        steps=(
            ("Fix the casting findings in this table first — a cross-casting difference "
             "usually disappears once the wrong cell is corrected.",
             "عالج أخطاء التدقيق (casting) في هذا الجدول أولاً — فرق التقاطع يزول عادةً "
             "بعد تصحيح الخلية الخاطئة."),
            _S_OPEN_SOURCE,
            ("Compare the sum of the row totals with the sum of the period totals; the "
             "difference equals the amount to look for.",
             "قارن مجموع إجماليات الصفوف بمجموع إجماليات الفترات؛ الفرق هو المبلغ الذي تبحث عنه."),
            _S_FALSE_POSITIVE,
            _S_RERUN,
        ),
    ),
    "row_vs_grand": Guidance(
        title=("Sum of row totals does not equal the grand total",
               "مجموع إجماليات الصفوف لا يساوي الإجمالي الكلي"),
        explanation=(
            "The grand total in the corner cell differs from the sum of the component "
            "rows' totals.",
            "الإجمالي الكلي في الخلية الركنية يختلف عن مجموع إجماليات الصفوف المكوِّنة.",
        ),
        causes=(
            ("The grand total is wrong, or a row total feeding it is wrong.",
             "الإجمالي الكلي خاطئ، أو أحد إجماليات الصفوف المغذّية له خاطئ."),
            ("A row was added to or left out of the grand total.",
             "صف أُضيف إلى الإجمالي الكلي أو استُبعد منه."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Re-add the row totals and compare with the grand total.",
             "أعد جمع إجماليات الصفوف وقارنها بالإجمالي الكلي."),
            _S_CONFIRM_READ,
            _S_FALSE_POSITIVE,
            _S_RERUN,
        ),
    ),
    "col_vs_grand": Guidance(
        title=("Sum of column totals does not equal the grand total",
               "مجموع إجماليات الأعمدة لا يساوي الإجمالي الكلي"),
        explanation=(
            "The grand total in the corner cell differs from the sum of this total "
            "row across the period columns.",
            "الإجمالي الكلي في الخلية الركنية يختلف عن مجموع صف الإجمالي عبر أعمدة الفترات.",
        ),
        causes=(
            ("One period total in the row is wrong.",
             "أحد إجماليات الفترات في الصف خاطئ."),
            ("The grand total was not updated after a period figure changed.",
             "الإجمالي الكلي لم يُحدَّث بعد تغيّر رقم فترة."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Re-add the period totals of this row and compare with the grand total.",
             "أعد جمع إجماليات الفترات في هذا الصف وقارنها بالإجمالي الكلي."),
            _S_CONFIRM_READ,
            _S_FALSE_POSITIVE,
            _S_RERUN,
        ),
    ),
    "ocr_vlm": Guidance(
        title=("Two readers disagree on this figure",
               "قارئان مختلفان لا يتفقان على هذا الرقم"),
        explanation=(
            "The table reader (VLM) and the OCR engine read different values for the "
            "same cell, so the figure is not reliable until a person checks it.",
            "قارئ الجداول (VLM) ومحرك OCR قرآ قيمتين مختلفتين للخلية نفسها، لذلك لا يمكن "
            "الوثوق بالرقم حتى يراجعه شخص.",
        ),
        causes=(
            ("Poor scan quality, stamps or handwriting over the figure.",
             "جودة مسح ضعيفة أو ختم أو كتابة يدوية فوق الرقم."),
            ("Similar-looking characters (0/O, 1/7, 5/6) or lost thousands separators.",
             "أحرف متشابهة (0/O، 1/7، 5/6) أو فواصل آلاف ضائعة."),
        ),
        steps=(
            _S_OPEN_SOURCE,
            ("Pick the value that matches the document; if neither does, take the "
             "document's value.",
             "اختر القيمة المطابقة للمستند؛ وإن لم تطابق أيٌّ منهما فخذ قيمة المستند."),
            ("If the correct value changes a total, re-run the job to re-check the arithmetic.",
             "إذا كانت القيمة الصحيحة تغيّر إجمالياً فأعد تشغيل المهمة لإعادة فحص الحساب."),
        ),
    ),
    "other": Guidance(
        title=("Finding needs a manual review", "ملاحظة تحتاج إلى مراجعة يدوية"),
        explanation=(
            "The engine could not classify this finding automatically.",
            "لم يستطع المحرك تصنيف هذه الملاحظة تلقائياً.",
        ),
        steps=(_S_OPEN_SOURCE, _S_RERUN),
    ),
}

HOW_TO_RESOLVE: tuple[Pair, ...] = (
    ("Work through the findings in priority order (1 = first). Fix cross-casting findings "
     "after the casting findings of the same table — they usually clear by themselves.",
     "عالج الملاحظات بترتيب الأولوية (1 = أولاً). عالج ملاحظات التقاطع بعد ملاحظات التدقيق "
     "في الجدول نفسه — فهي تزول عادةً من تلقاء نفسها."),
    ("For each finding open the source at the shown location and decide: the document is "
     "wrong, the extraction is wrong, or the finding is a false alarm.",
     "لكل ملاحظة افتح المصدر عند الموضع الموضّح وقرّر: المستند خاطئ، أو القراءة خاطئة، أو الملاحظة إنذار كاذب."),
    ("Set the status: 'resolved' once fixed, 'acknowledged' when reviewed and accepted "
     "as-is, 'open' otherwise.",
     "حدّد الحالة: «resolved» بعد الإصلاح، «acknowledged» إذا روجعت وقُبلت كما هي، و«open» غير ذلك."),
    ("After correcting the source file, re-run the job to confirm the arithmetic now balances.",
     "بعد تصحيح الملف المصدر أعد تشغيل المهمة للتأكد أن الحساب أصبح متوازناً."),
)

STATUS_LABELS: dict[str, Pair] = {
    "open": ("Open", "مفتوحة"),
    "acknowledged": ("Acknowledged", "تمت المراجعة وقُبلت"),
    "resolved": ("Resolved", "تم الحل"),
}
SEVERITY_LABELS: dict[str, Pair] = {
    "critical": ("Critical", "حرجة"),
    "high": ("High", "عالية"),
    "medium": ("Medium", "متوسطة"),
    "low": ("Low", "منخفضة"),
}
CATEGORY_LABELS: dict[str, Pair] = {
    "casting": ("Casting (totals vs components)", "التدقيق (الإجمالي مقابل المكوّنات)"),
    "cross_casting": ("Cross-casting (rows vs columns)", "التدقيق المتقاطع (الصفوف مقابل الأعمدة)"),
    "verification": ("OCR/VLM verification", "التحقق OCR/VLM"),
    "other": ("Other", "أخرى"),
}

# Labels of the two sides compared on a card, per check.
VALUE_LABELS: dict[str, tuple[Pair, Pair]] = {
    "vertical": (("Declared total", "الإجمالي المعلن"),
                 ("Computed from lines", "المحسوب من البنود")),
    "horizontal": (("Declared row total", "إجمالي الصف المعلن"),
                   ("Computed from columns", "المحسوب من الأعمدة")),
    "trial_balance": (("Total credits", "إجمالي الدائن"),
                      ("Total debits", "إجمالي المدين")),
    "row_vs_col": (("Σ row totals", "مجموع إجماليات الصفوف"),
                   ("Σ column (period) totals", "مجموع إجماليات الأعمدة")),
    "row_vs_grand": (("Σ row totals", "مجموع إجماليات الصفوف"),
                     ("Grand total", "الإجمالي الكلي")),
    "col_vs_grand": (("Σ column (period) totals", "مجموع إجماليات الأعمدة"),
                     ("Grand total", "الإجمالي الكلي")),
    "ocr_vlm": (("OCR value", "قيمة OCR"), ("VLM value", "قيمة VLM")),
    "other": (("Expected", "المتوقع"), ("Actual", "الفعلي")),
}


# ── Data-driven hints ─────────────────────────────────────────────────────────

def hints_for(first: Decimal | None, second: Decimal | None, delta: Decimal | None) -> list[Pair]:
    """Interpret the numbers: rounding, sign slip, digit transposition, scale slip."""
    out: list[Pair] = []
    if delta is None or delta == 0:
        return out
    d = abs(delta)
    if d <= Decimal("1"):
        out.append((
            "The difference is within ±1: probably rounding in the source. If the document "
            "presents rounded figures, acknowledge the finding.",
            "الفرق ضمن ±1: غالباً تقريب في المصدر. إذا كان المستند يعرض أرقاماً مقرّبة "
            "فاجعل الحالة «acknowledged».",
        ))
    biggest = max((abs(x) for x in (first, second) if x is not None), default=Decimal(0))
    if 9 <= d < biggest and d == d.to_integral_value() and int(d) % 9 == 0:
        out.append((
            "The difference is divisible by 9: a classic sign of two transposed digits "
            "(e.g. 54 typed as 45).",
            "الفرق يقبل القسمة على 9: علامة كلاسيكية على قلب رقمين متجاورين (مثل 54 مكتوبة 45).",
        ))
    if first is not None and second is not None and first != 0 and first == -second:
        out.append((
            "The two figures are equal with opposite signs: a sign error (a negative "
            "or bracketed amount read as positive, or vice versa).",
            "الرقمان متساويان بإشارتين متعاكستين: خطأ إشارة (مبلغ سالب أو بين قوسين قُرئ موجباً أو العكس).",
        ))
    if first is not None and second is not None and first != 0 and second != 0:
        ratio = abs(first / second)
        for p in (Decimal(10), Decimal(100), Decimal(1000)):
            if ratio == p or ratio == 1 / p:
                out.append((
                    f"One figure is exactly {int(p)}× the other: check the scale "
                    "(units / thousands) and a missing or extra zero.",
                    f"أحد الرقمين يساوي {int(p)} ضعف الآخر تماماً: افحص الوحدة (آحاد/آلاف) وصفراً ناقصاً أو زائداً.",
                ))
                break
    return out
