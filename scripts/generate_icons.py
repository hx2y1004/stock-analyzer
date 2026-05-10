"""PWA 아이콘 생성 스크립트

192x192, 512x512 PNG 아이콘을 static/icons/ 에 생성합니다.
디자인: 어두운 배경 + 상승 캔들스틱 차트 모티브 + '📈' 그라디언트
"""
import os
from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "icons")
os.makedirs(OUT_DIR, exist_ok=True)

BG       = (13, 17, 23)        # #0d1117
ACCENT   = (88, 166, 255)      # 파랑 (라이트차트 라인)
GREEN    = (63, 185, 80)       # 양봉
RED      = (248, 81, 73)       # 음봉
GRID     = (33, 38, 45)


def draw_icon(size: int, out_path: str):
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    # ── 라운드 둥근 사각형 배경 (안전영역 마진 12%) ──
    pad = int(size * 0.06)
    radius = int(size * 0.22)
    d.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=(22, 27, 34),
    )

    # ── 캔들스틱 5개 ──
    # x 좌표: 균등 배치, y: 가운데 기준 위/아래
    n = 5
    chart_left   = int(size * 0.18)
    chart_right  = int(size * 0.82)
    chart_top    = int(size * 0.30)
    chart_bot    = int(size * 0.78)
    chart_h      = chart_bot - chart_top
    cw           = (chart_right - chart_left) / n
    candle_w     = int(cw * 0.55)

    # (open_y%, close_y%) 0=상단, 100=하단. 상승 트렌드.
    candles = [
        (0.85, 0.65, GREEN),
        (0.70, 0.78, RED),
        (0.65, 0.45, GREEN),
        (0.50, 0.35, GREEN),
        (0.40, 0.15, GREEN),
    ]

    for i, (op, cl, color) in enumerate(candles):
        cx = int(chart_left + cw * (i + 0.5))
        # 심지: open/close 의 ±15%
        wick_top = chart_top + int(chart_h * (min(op, cl) - 0.10))
        wick_bot = chart_top + int(chart_h * (max(op, cl) + 0.10))
        wick_top = max(chart_top, wick_top)
        wick_bot = min(chart_bot, wick_bot)
        # 심지
        d.line(
            [(cx, wick_top), (cx, wick_bot)],
            fill=color,
            width=max(2, int(size * 0.012)),
        )
        # 몸통
        body_top = chart_top + int(chart_h * min(op, cl))
        body_bot = chart_top + int(chart_h * max(op, cl))
        d.rounded_rectangle(
            [cx - candle_w // 2, body_top, cx + candle_w // 2, body_bot],
            radius=max(2, int(size * 0.015)),
            fill=color,
        )

    # ── 상승 화살표 라인 (캔들 위로 가로지름) ──
    line_pts = [
        (chart_left + int(cw * 0.3), chart_top + int(chart_h * 0.85)),
        (chart_left + int(cw * 1.5), chart_top + int(chart_h * 0.55)),
        (chart_left + int(cw * 2.5), chart_top + int(chart_h * 0.40)),
        (chart_left + int(cw * 3.5), chart_top + int(chart_h * 0.25)),
        (chart_left + int(cw * 4.5), chart_top + int(chart_h * 0.10)),
    ]
    d.line(line_pts, fill=ACCENT, width=max(3, int(size * 0.018)))

    # 끝점 강조 원
    last = line_pts[-1]
    r = max(4, int(size * 0.025))
    d.ellipse(
        [last[0] - r, last[1] - r, last[0] + r, last[1] + r],
        fill=ACCENT,
        outline=BG,
        width=max(2, int(size * 0.008)),
    )

    img.save(out_path, "PNG", optimize=True)
    print(f"[OK] {out_path} ({size}x{size})")


if __name__ == "__main__":
    draw_icon(192, os.path.join(OUT_DIR, "icon-192.png"))
    draw_icon(512, os.path.join(OUT_DIR, "icon-512.png"))
    # iOS apple-touch-icon (180x180)
    draw_icon(180, os.path.join(OUT_DIR, "icon-180.png"))
    print("Done.")
