# Frontend changes — today

Deltas only, on top of the main prompt in [Frontend-Prompt.md](Frontend-Prompt.md). Everything else in that prompt is still correct.

---

## 1. New field: `coverage.pages_skipped`

Location: `GET /jobs/{id}/review` → `coverage.pages_skipped: int[]`.

List of page numbers the engine did not send to the table reader because OCR saw no financial table on them (cover page, auditor's report, accounting policy text).

Show it next to `coverage` in the review header, e.g.:

> 95 tables, 507 comparisons checked · 22 narrative pages skipped (1, 2, 6, 7, 17 …).

It is always present (empty array when nothing was skipped).

---

## 2. New field: `location.section`

Location: every finding's `location.section: string | null`.

The block the row belongs to inside its table. Needed because the same row label repeats several times in one note — e.g. in the PPE movement note, "Currency translation adjustments" appears under "The cost", under "Accumulated depreciation and impairment", and again under "Balance as on 1 January, 2024". Without `section` the reviewer cannot tell which row is being flagged.

Render it next to `row`:

> Row: Currency translation adjustments · Under: Accumulated depreciation and impairment

Hide the "Under" segment when `section` is `null` or empty.

---

## 3. New severity tier: `medium` — "Structure unresolved / needs review"

Severity `"medium"` can now appear on casting findings when the engine could not confirm the formula on any column that would tell it apart from the alternatives — the difference may come from how the table was read, not from the figures themselves. Typical cases: an unlabelled "bold" row whose only arithmetic match sits in an Eliminations column, or a one-column note whose formula is inferred from structure alone.

- Use the same badge pattern as other severities (text label mandatory, colour optional).
- The strings `labels.severity.medium.en` / `.ar` already carry the display name.
- The explanation starts with "Structure unresolved — needs review", already bilingual. Do **not** write that phrase yourself.
- `medium` has its own filter tab; do not fold it into `high` or `low`.
- The reviewer's recommended action on a `medium` casting finding is **Acknowledge** (mark as a false alarm / needs source check) more often than **Resolve**.

---

## 4. Behaviour change: file download URL is now openable

`GET /jobs/{id}/file` → `presigned_url` now returns a URL signed for the public MinIO address (`localhost:9010` in dev). The URL opens directly in the browser.

- The **Open source** button in the finding card was broken before — it now works.
- For PDFs, append `#page={location.page}` to the URL to jump to the page. Example:
  ```
  window.open(`${presigned_url}#page=${location.page}`, "_blank")
  ```
- No change to the API contract; only the hostname inside the URL changed.

---

## 5. Behaviour change: retry is fast and cheap

`POST /jobs/{id}/retry` on a file that was already processed finishes in under a minute, because the engine caches each page reading.

- The "retry may take several minutes" warning on the retry button can be dropped.
- Results are deterministic: the same file gives the same findings on every retry, so refreshing the review page after a retry is safe (no "the numbers changed" risk).

---

## 6. Rotated source scans

Some pages in scanned statements are stored sideways (landscape scanned 90°). The engine now turns them upright before reading. The **original PDF** served by `/jobs/{id}/file` is unchanged, so clicking "Open source" on a finding on such a page will show a sideways page.

Optional UX hint next to the Open source button when `location.page` belongs to a sideways scan (no API flag; a viewer rotation control is enough).

---

## 7. What has **not** changed

- All endpoints, status codes, filters, and `scope` / `status` / `lang` query params are the same.
- All existing fields keep the same names and shapes; the two new ones (`coverage.pages_skipped`, `location.section`) are additive.
- `summary`, `conclusion`, `is_clean`, categories (`casting` / `cross_casting` / `verification`), and the PATCH flow for status changes are unchanged.
- Bilingual labels in `labels.*` already cover the new `low` severity — no new keys to handle.

---

## Quick checklist for the frontend

- [ ] Show `coverage.pages_skipped` count in the review header.
- [ ] Add a `section` line to the finding card when non-empty.
- [ ] Handle `severity: "low"` in badges and filters.
- [ ] Remove any URL rewriting around the download link; use it verbatim.
- [ ] Append `#page=N` for PDFs when opening source.
- [ ] Drop the "retry is slow" warning.
