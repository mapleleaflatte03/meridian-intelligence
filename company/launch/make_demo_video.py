#!/usr/bin/env python3
"""Build a 2-3 minute Meridian demo video from storyboard slides."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = ASSETS_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_VIDEO = ASSETS_DIR / "meridian_demo_2m20s.mp4"
CONCAT_FILE = ASSETS_DIR / "meridian_demo_frames.ffconcat"

WIDTH = 1280
HEIGHT = 720
BG_TOP = (3, 6, 12)
BG_BOTTOM = (8, 16, 28)
ACCENT = (135, 216, 255)
TEXT = (232, 238, 246)
DIM = (150, 165, 185)

SLIDES = [
    (
        "Meridian Loom",
        "Governed local agent runtime\nwith inspectable receipts and proof surfaces.",
        16,
    ),
    (
        "Install in one command",
        "curl -fsSL https://raw.githubusercontent.com/mapleleaflatte03/meridian-loom/main/scripts/install.sh | bash",
        14,
    ),
    (
        "Create + run first agent",
        "loom new-agent --name my-assistant\nloom run-agent my-assistant --once",
        14,
    ),
    (
        "Connect lifecycle (operator-grade)",
        "loom connect scaffold --name desktop_ops --transport desktop --action-schema meridian.runtime.v1\nloom connect validate|enable|test|health|scorecard",
        18,
    ),
    (
        "Failure semantics",
        "Rate-limit, malformed payload, reconnect storm,\nand sanction path are explicit acceptance lanes.",
        16,
    ),
    (
        "Proof dashboard",
        "Public viewer: /proofs\nRuntime state + queue pressure + proof boundary + realtime event stream.",
        16,
    ),
    (
        "Workflow growth surface",
        "Public /workflows page shows first-party lanes\nand live USDC operating snapshot.",
        16,
    ),
    (
        "Architecture boundary",
        "Loom = runtime\nKernel = authority/treasury/court core\nWorkflows = proof workloads",
        14,
    ),
    (
        "Live links",
        "app.welliam.codes/demo\napp.welliam.codes/proofs\napp.welliam.codes/workflows",
        18,
    ),
    (
        "Request for feedback",
        "Which adapter/failure mode still blocks production trust?\nWhich KPI is required before adoption?",
        18,
    ),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient_background() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOTTOM[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOTTOM[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOTTOM[2] * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    return img


def _draw_slide(index: int, title: str, body: str) -> Path:
    frame_path = FRAMES_DIR / f"slide_{index:02d}.png"
    img = _gradient_background()
    draw = ImageDraw.Draw(img)
    title_font = _font(56, bold=True)
    body_font = _font(30, bold=False)
    mono_font = _font(20, bold=False)

    draw.rounded_rectangle([(72, 62), (1208, 658)], radius=22, outline=(52, 84, 110), width=2, fill=(8, 14, 24))
    draw.text((110, 120), title, fill=TEXT, font=title_font)
    draw.text((110, 220), body, fill=TEXT, font=body_font, spacing=14)
    draw.text((110, 610), "Meridian Loom · Governed Agent Runtime", fill=ACCENT, font=mono_font)
    draw.text((1110, 610), f"{index + 1:02d}/{len(SLIDES):02d}", fill=DIM, font=mono_font)
    img.save(frame_path)
    return frame_path


def _render_frames() -> list[tuple[Path, int]]:
    slides: list[tuple[Path, int]] = []
    for idx, (title, body, duration) in enumerate(SLIDES):
        slides.append((_draw_slide(idx, title, body), duration))
    return slides


def _write_concat(slides: list[tuple[Path, int]]) -> None:
    lines = ["ffconcat version 1.0"]
    for frame, duration in slides:
        lines.append(f"file '{frame}'")
        lines.append(f"duration {duration}")
    # Repeat final frame per ffconcat rules.
    lines.append(f"file '{slides[-1][0]}'")
    CONCAT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_video() -> None:
    ffmpeg = subprocess.run(["bash", "-lc", "command -v ffmpeg"], capture_output=True, text=True)
    if ffmpeg.returncode != 0 or not ffmpeg.stdout.strip():
        raise SystemExit("ffmpeg is required. Install it and rerun: sudo apt update && sudo apt install -y ffmpeg")
    command = [
        "ffmpeg",
        "-y",
        "-safe",
        "0",
        "-f",
        "concat",
        "-i",
        str(CONCAT_FILE),
        "-vf",
        "format=yuv420p,fps=30",
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        str(OUTPUT_VIDEO),
    ]
    subprocess.run(command, check=True)


def main() -> None:
    slides = _render_frames()
    _write_concat(slides)
    _build_video()
    total_seconds = sum(duration for _, duration in slides)
    print(f"Built {OUTPUT_VIDEO} ({total_seconds}s)")


if __name__ == "__main__":
    main()
