"""Render the test workbook's statements as a 2-page PDF and a PNG (VLM test fixtures).

Figures are read from Casting_Test_With_Errors.xlsx, so the images carry the same
intentional errors (FY2025 Total assets 1,815,000; Gross profit 645,000; PBT 360,000).
The Balance Sheet is split across pages 1 and 2 to exercise multi-page tables.

    python tests/fixtures/make_vlm_fixtures.py
"""
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1240, 1754                      # A4 at 150 dpi
COLS = [90, 640, 860, 1080]            # label, FY2024, FY2025, Total (right edges for numbers)


def sheet_rows(ws):
    return [[c.value for c in r] for r in ws.iter_rows()]


def draw_table(d, y, title, subtitle, header, rows, f, fb):
    if title:
        d.text((COLS[0], y), title, font=fb, fill="black"); y += 40
    if subtitle:
        d.text((COLS[0], y), subtitle, font=f, fill="black"); y += 46
    d.text((COLS[0], y), header[0], font=fb, fill="black")
    for x, h in zip(COLS[1:], header[1:]):
        d.text((x, y), h, font=fb, fill="black", anchor="ra")
    y += 34
    d.line((COLS[0], y, COLS[3], y), fill="black", width=2); y += 10
    for label, *vals in rows:
        is_total = label.lower().startswith(("total", "gross", "operating profit", "profit"))
        font = fb if is_total else f
        if is_total:
            d.line((COLS[1] - 160, y - 4, COLS[3], y - 4), fill="black", width=1)
        d.text((COLS[0], y), label, font=font, fill="black")
        for x, v in zip(COLS[1:], vals):
            d.text((x, y), f"{v:,.2f}", font=font, fill="black", anchor="ra")
        y += 38
    return y + 30


def main():
    wb = openpyxl.load_workbook(HERE / "Casting_Test_With_Errors.xlsx", data_only=True)
    inc = sheet_rows(wb["Income Statement"])
    bs = sheet_rows(wb["Balance Sheet"])
    f, fb = ImageFont.truetype(FONT, 24), ImageFont.truetype(BOLD, 24)

    p1 = Image.new("RGB", (W, H), "white"); d1 = ImageDraw.Draw(p1)
    y = draw_table(d1, 80, inc[0][0], inc[1][0], inc[2], inc[3:], f, fb)
    assets = bs[3:8]                        # Cash … Total assets
    draw_table(d1, y + 20, bs[0][0], bs[1][0], bs[2], assets, f, fb)

    p2 = Image.new("RGB", (W, H), "white"); d2 = ImageDraw.Draw(p2)
    draw_table(d2, 80, "Statement of Financial Position (continued)", None, bs[2], bs[8:], f, fb)

    p1.save(HERE / "financial_statements_2pages.pdf", save_all=True, append_images=[p2], resolution=150)

    single = Image.new("RGB", (W, 900), "white")
    draw_table(ImageDraw.Draw(single), 60, bs[0][0], bs[1][0], bs[2], bs[3:], f, fb)
    single.save(HERE / "balance_sheet.png")
    print("written:", sorted(p.name for p in HERE.glob("*.p*")))


if __name__ == "__main__":
    main()
