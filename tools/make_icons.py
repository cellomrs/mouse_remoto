"""Gera os ícones do PWA em web/icons/. Requer Pillow (só para desenvolvimento).

    python tools/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
BG = (17, 19, 24, 255)
PAD = (37, 41, 51, 255)
ACCENT = (91, 140, 255, 255)
WHITE = (240, 242, 247, 255)
S = 1024  # desenha grande e reduz (antialiasing)


def draw(scale_content: float, rounded: bool) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=S * 0.22, fill=BG)
    else:
        d.rectangle((0, 0, S, S), fill=BG)  # maskable: o sistema recorta

    def p(v: float) -> float:  # coordenada relativa ao centro, escalada
        return S / 2 + (v - 0.5) * S * scale_content

    # Touchpad
    d.rounded_rectangle(
        (p(0.14), p(0.22), p(0.86), p(0.78)), radius=S * 0.07 * scale_content,
        fill=PAD, outline=ACCENT, width=int(S * 0.025 * scale_content),
    )
    # Cursor (seta)
    arrow = [(0.42, 0.33), (0.42, 0.66), (0.50, 0.58), (0.555, 0.70),
             (0.60, 0.68), (0.545, 0.565), (0.65, 0.565)]
    d.polygon([(p(x), p(y)) for x, y in arrow], fill=WHITE)
    return img


def save(img: Image.Image, name: str, size: int) -> None:
    img.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    normal = draw(1.0, rounded=True)
    maskable = draw(0.72, rounded=False)  # conteúdo dentro da zona segura (80%)
    save(normal, "icon-192.png", 192)
    save(normal, "icon-512.png", 512)
    save(maskable, "icon-maskable-512.png", 512)
    save(draw(1.0, rounded=False), "apple-touch-icon.png", 180)
    print("ícones gerados em", OUT)


if __name__ == "__main__":
    main()
