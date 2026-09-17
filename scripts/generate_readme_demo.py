"""Generate the sanitized Buddies report demo embedded in README.md."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "assets" / "demo"
GIF_PATH = OUTPUT / "roundtable-buddies.gif"

WIDTH = 1400
HEIGHT = 788
SCALE = 2
FRAME_COUNT = 48
FRAME_MS = 250

BG = "#f5f7fa"
PANEL = "#ffffff"
INK = "#1f2d3d"
MUTED = "#6b7a8d"
LINE = "#dbe2ea"
EDGE = "#7a8ba3"
NAVY = "#22304a"
BLUE = "#1668e3"
PURPLE = "#b07cc6"
GREEN = "#1c7a3e"
PALE = "#eef2f7"
RUNNING = "#dcecff"
HEAT = ("#fee299", "#fdc77c", "#f79b66", "#ee7d56", "#e95a44")


@dataclass(frozen=True)
class Node:
    label: str
    center: tuple[int, int]
    stage: int
    kind: str = "agent"
    heat: int = 0


SOURCE = Node("Review Diff", (70, 550), 1, kind="deterministic")
REVIEWERS = (
    Node("Big-O", (300, 365), 2, heat=3),
    Node("Counter Case", (300, 435), 2, heat=0),
    Node("North Star", (300, 505), 2, heat=2),
    Node("Red-Green", (300, 575), 2, heat=4),
    Node("Smell Check", (300, 645), 2, heat=1),
    Node("Taint Check", (300, 715), 2, heat=0),
)
INDEX = Node("Finding Index", (540, 470), 3, kind="deterministic")
CLAIMS = Node("Reviewer Claims", (540, 630), 3, kind="deterministic")
JUDGE = Node("Judge", (760, 550), 3, heat=2)
INPUTS = Node("Remediation Inputs", (970, 550), 4, kind="deterministic")
REMEDY = Node("Remedy Scout", (1180, 550), 4, heat=1)
VERDICT = Node("Verdict", (1340, 550), 5, kind="deterministic")
NODES = (SOURCE, *REVIEWERS, INPUTS, INDEX, CLAIMS, REMEDY, JUDGE, VERDICT)
STAGE_START = (0, 5, 10, 27, 35, 43)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = "C:\\Windows\\Fonts\\seguisb.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf"
    linux = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    for name in (windows, linux):
        if Path(name).is_file():
            return ImageFont.truetype(name, size * SCALE)
    return ImageFont.load_default(size=size * SCALE)


BODY = _font(12)
SMALL = _font(10)
LABEL = _font(11)
CARD_TITLE = _font(10, bold=True)
VALUE = _font(12, bold=True)
HEADER = _font(15, bold=True)


def _xy(values: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(value * SCALE for value in values)


def _stage(frame: int) -> int:
    return max(index for index, start in enumerate(STAGE_START) if frame >= start)


def _card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    rows: tuple[tuple[str, str], ...],
) -> None:
    draw.rounded_rectangle(_xy(box), radius=8 * SCALE, fill=PANEL, outline=LINE, width=SCALE)
    draw.text(_xy((box[0] + 14, box[1] + 12)), title.upper(), fill=MUTED, font=CARD_TITLE)
    y = box[1] + 34
    for key, value in rows:
        draw.text(_xy((box[0] + 14, y)), key, fill=MUTED, font=SMALL)
        draw.text(_xy((box[0] + 145, y)), value, fill=INK, font=SMALL)
        y += 22


def _overview(draw: ImageDraw.ImageDraw, stage: int) -> None:
    verdict = "APPROVE WITH SUGGESTIONS" if stage == 5 else "RUNNING"
    findings = "3 non-blocking" if stage == 5 else "collecting..."
    _card(
        draw,
        (20, 65, 350, 245),
        "Subject",
        (
            ("Repo", "sample-service"),
            ("Mode", "pr"),
            ("PR", "#42 Harden retry handling"),
            ("Branch", "feature/retries → main"),
            ("Commit", "a1b2c3d4 .. e5f6a7b8"),
            ("Diff", "8 files  +146 / -31"),
            ("Reviewer", "roundtable demo"),
        ),
    )
    _card(
        draw,
        (365, 65, 695, 245),
        "Session",
        (
            ("Verdict", verdict),
            ("Findings", findings),
            ("Agents", "13"),
            ("Elapsed (wall clock)", "2m 18.2s  (2.7× concurrency)"),
            ("Compute (summed API)", "6m 11.4s"),
            ("AI Credits consumed", "38.4200"),
        ),
    )
    _card(
        draw,
        (710, 65, 1040, 245),
        "Slowest (wall clock)",
        (
            ("redgreen", "1m 04.0s"),
            ("bigoh", "52.1s"),
            ("Judge", "41.9s"),
            ("RemedyScout", "36.7s"),
            ("Validation tax", "retried: none"),
        ),
    )
    _card(
        draw,
        (1055, 65, 1380, 245),
        "Tool usage (47 calls)",
        (
            ("Builtin", "view 21 · rg 15 · shell 9"),
            ("MCP", "repository context 2"),
            ("MCP servers", "repository · ready"),
            ("Busiest agent", "RemedyScout · 11 calls"),
            ("Retries", "none"),
        ),
    )


def _curve(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    active: bool,
    dotted: bool = False,
    color: str = EDGE,
) -> None:
    x1, y1 = start
    x2, y2 = end
    points = []
    for step in range(25):
        t = step / 24
        x = (1 - t) ** 3 * x1 + 3 * (1 - t) ** 2 * t * (x1 + x2) / 2
        x += 3 * (1 - t) * t**2 * (x1 + x2) / 2 + t**3 * x2
        y = (1 - t) * y1 + t * y2
        points.append((int(x * SCALE), int(y * SCALE)))
    edge_color = color if active else "#b8c5d6"
    width = (3 if color == PURPLE else 2) * SCALE
    if dotted:
        for index in range(0, len(points) - 1, 3):
            draw.line(points[index : index + 2], fill=edge_color, width=width)
    else:
        draw.line(points, fill=edge_color, width=width)


def _edges(draw: ImageDraw.ImageDraw, stage: int) -> None:
    for reviewer in REVIEWERS:
        _curve(draw, (100, 550), (270, reviewer.center[1]), active=stage >= 2, dotted=True)
    for node, producers in (
        (INPUTS, "6 producers"),
        (INDEX, "6 producers"),
        (CLAIMS, "6 producers"),
    ):
        end_x = node.center[0] - 40
        start_x = end_x - 110
        _curve(
            draw,
            (start_x, node.center[1]),
            (end_x, node.center[1]),
            active=stage >= node.stage,
            color=PURPLE,
        )
        draw.text(
            _xy(((start_x + end_x) // 2 - 35, node.center[1] - 18)),
            producers,
            fill=PURPLE,
            font=SMALL,
        )
    _curve(draw, (580, 470), (720, 550), active=stage >= 3)
    _curve(draw, (580, 630), (720, 550), active=stage >= 3)
    _curve(draw, (800, 550), (930, 550), active=stage >= 4)
    _curve(draw, (1010, 550), (1140, 550), active=stage >= 4)
    _curve(draw, (1220, 550), (1300, 550), active=stage >= 5, dotted=True)
    _curve(draw, (800, 550), (1300, 550), active=stage >= 5, dotted=True)


def _node_state(node: Node, frame: int) -> str:
    start = STAGE_START[node.stage]
    if frame < start:
        return "waiting"
    next_start = STAGE_START[node.stage + 1] if node.stage < 5 else FRAME_COUNT
    return "active" if frame < next_start else "complete"


def _circle(draw: ImageDraw.ImageDraw, node: Node, frame: int, state: str) -> None:
    x, y = node.center
    fill = PALE if state == "waiting" else HEAT[node.heat]
    outline = BLUE if state == "active" else "#38506e"
    width = 3 if state == "active" else 2
    if state == "active":
        pulse = 4 + round(2 * math.sin(frame * math.pi / 2))
        draw.ellipse(
            _xy((x - 32 - pulse, y - 32 - pulse, x + 32 + pulse, y + 32 + pulse)),
            outline="#a8c9f7",
            width=2 * SCALE,
        )
    draw.ellipse(
        _xy((x - 28, y - 28, x + 28, y + 28)), fill=fill, outline=outline, width=width * SCALE
    )


def _diamond(draw: ImageDraw.ImageDraw, node: Node, state: str) -> None:
    x, y = node.center
    fill = RUNNING if state == "active" else PALE
    outline = BLUE if state == "active" else "#38506e"
    points = _xy((x, y - 28, x + 28, y, x, y + 28, x - 28, y))
    draw.polygon([(points[i], points[i + 1]) for i in range(0, 8, 2)], fill=fill, outline=outline)
    if node is VERDICT and state == "complete":
        draw.polygon(
            [
                (x * SCALE, (y - 28) * SCALE),
                ((x + 28) * SCALE, y * SCALE),
                (x * SCALE, (y + 28) * SCALE),
                ((x - 28) * SCALE, y * SCALE),
            ],
            fill="#d6f5df",
            outline=GREEN,
        )


def _node_shape(draw: ImageDraw.ImageDraw, node: Node, frame: int) -> None:
    state = _node_state(node, frame)
    if node.kind == "agent":
        _circle(draw, node, frame, state)
    else:
        _diamond(draw, node, state)


def _node_label(draw: ImageDraw.ImageDraw, node: Node, frame: int) -> None:
    state = _node_state(node, frame)
    x, y = node.center
    label_color = GREEN if node is VERDICT and state == "complete" else INK
    if node in REVIEWERS:
        draw.text(_xy((x + 40, y)), node.label, fill=label_color, font=LABEL, anchor="lm")
        return
    draw.text(_xy((x, y + 30)), node.label, fill=label_color, font=LABEL, anchor="mt")


def _legend(draw: ImageDraw.ImageDraw) -> None:
    items = (
        ("LLM agent", INK),
        ("deterministic", INK),
        ("required", MUTED),
        ("inferred", MUTED),
        ("dossier bundle", PURPLE),
        ("heat = wall-clock", MUTED),
    )
    x = 28
    for text, color in items:
        draw.text(_xy((x, 275)), text, fill=color, font=BODY)
        x += 190


def _frame(frame: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle(_xy((0, 0, WIDTH, 48)), fill=NAVY)
    draw.text(_xy((20, 15)), "buddies report — synthetic demo", fill="white", font=HEADER)
    stage = _stage(frame)
    _overview(draw, stage)
    _legend(draw)
    _edges(draw, stage)
    for node in NODES:
        _node_shape(draw, node, frame)
    for node in NODES:
        _node_label(draw, node, frame)
    draw.text(
        _xy((1375, 765)),
        "Synthetic report · no repository, PR, user, or customer data",
        fill=MUTED,
        font=SMALL,
        anchor="rs",
    )
    return image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frames = [_frame(index) for index in range(FRAME_COUNT)]
    frames[0].save(
        GIF_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )
    print(f"generated {GIF_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
