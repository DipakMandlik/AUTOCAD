"""Regex/keyword-based fallback command parser.

This is *not* an NLP engine — it is a cheap, fully offline fallback for use
when no LLM-backed planner is configured (or as a last resort if one
fails). It recognizes a small, fixed set of English/Chinese drawing
commands via keyword and regex matching and extracts positional
numeric/coordinate arguments. Ambiguous or novel phrasing will not be
understood: that is an inherent limitation of this approach, not something
to special-case away here. A real `IntentSource` (e.g. LLM-backed) belongs
in `engine/planner/intent.py`, implementing the same interface.

This replaces the original repo's `nlp_processor.py`, fixing: duplicated
command-type detection logic, arc default angles applied inconsistently,
and unifying output field names with `engine.geometry.primitives` so the
planner can construct entities directly without a translation step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Point = Tuple[float, float, float]


@dataclass
class ParsedCommand:
    operation: str
    params: Dict[str, Any] = field(default_factory=dict)
    note: Optional[str] = None


COLOR_INDEX: Dict[str, int] = {
    "红色": 1, "red": 1,
    "黄色": 2, "yellow": 2,
    "绿色": 3, "green": 3,
    "青色": 4, "cyan": 4,
    "蓝色": 5, "blue": 5,
    "洋红色": 6, "magenta": 6,
    "白色": 7, "white": 7,
    "灰色": 8, "gray": 8, "grey": 8,
    "浅灰色": 9, "light gray": 9, "light grey": 9,
    "黑色": 250, "black": 250,
    "棕色": 251, "brown": 251,
    "橙色": 30, "orange": 30,
    "紫色": 200, "purple": 200,
    "粉色": 221, "pink": 221,
}

SHAPE_KEYWORDS: Dict[str, str] = {
    "直线": "line", "线": "line", "line": "line",
    "圆形": "circle", "圆": "circle", "circle": "circle",
    "圆弧": "arc", "弧": "arc", "arc": "arc",
    "椭圆": "ellipse", "ellipse": "ellipse",
    "矩形": "rectangle", "方形": "rectangle", "正方形": "rectangle",
    "rectangle": "rectangle", "square": "rectangle",
    "多段线": "polyline", "折线": "polyline", "polyline": "polyline",
    "文本": "text", "文字": "text", "text": "text",
    "填充": "hatch", "hatch": "hatch",
    "标注": "dimension", "尺寸标注": "dimension", "dimension": "dimension",
}

DRAW_ACTION_WORDS = ("画", "绘制", "创建", "添加", "draw", "create", "add")
SAVE_WORDS = ("保存", "save")
LAYER_WORDS = ("图层", "layer")
LAYER_ACTION_WORDS = ("创建", "新建", "添加", "create", "new", "add")

_COORD_PATTERN = re.compile(r"\(?\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)(?:\s*,\s*(-?\d+\.?\d*))?\s*\)?")
_NUMBER_PATTERN = re.compile(r"-?\d+\.?\d*")


class FallbackParser:
    """Keyword/regex command parser; the pre-LLM baseline intent source."""

    def extract_color(self, text: Optional[str]) -> Optional[int]:
        if not text:
            return None
        try:
            index = int(text)
            if 1 <= index <= 255:
                return index
        except ValueError:
            pass
        lowered = text.lower()
        for name, index in COLOR_INDEX.items():
            if name.lower() in lowered:
                return index
        return None

    def parse(self, text: str) -> ParsedCommand:
        command = text.strip()
        lowered = command.lower()

        if any(w in lowered for w in LAYER_WORDS) and any(w in lowered for w in LAYER_ACTION_WORDS):
            return self._parse_create_layer(command)
        if any(w in lowered for w in SAVE_WORDS):
            return self._parse_save(command)

        shape = self._identify_shape(lowered)
        if shape is None:
            return ParsedCommand("unknown", note="could not identify a recognized drawing command")

        handler = getattr(self, f"_parse_{shape}")
        parsed = handler(command)
        color = self.extract_color(command)
        if color is not None:
            parsed.params.setdefault("color", color)
        return parsed

    def _identify_shape(self, lowered: str) -> Optional[str]:
        is_dimension_command = "标注" in lowered or "dimension" in lowered
        if not any(w in lowered for w in DRAW_ACTION_WORDS) and not is_dimension_command:
            return None
        # Longest keyword first: "圆" (circle) is a substring of "圆弧"
        # (arc), so a shortest-first scan would misclassify every arc
        # command as a circle.
        for keyword in sorted(SHAPE_KEYWORDS, key=len, reverse=True):
            if keyword in lowered:
                return SHAPE_KEYWORDS[keyword]
        return None

    def _coordinates(self, text: str) -> List[Point]:
        points = []
        for match in _COORD_PATTERN.finditer(text):
            x, y, z = match.group(1), match.group(2), match.group(3)
            points.append((float(x), float(y), float(z) if z else 0.0))
        return points

    def _numbers(self, text: str) -> List[float]:
        return [float(n) for n in _NUMBER_PATTERN.findall(text)]

    def _keyword_number(self, text: str, *keywords: str) -> Optional[float]:
        # ASCII keywords get \b boundaries so a single-letter one like "r"
        # (radius shorthand) can't match inside unrelated words such as
        # "draw". Chinese keywords skip \b: Chinese text has no spaces, so
        # a keyword is routinely immediately adjacent to its digits (e.g.
        # "半径10") with no word-boundary transition between them at all.
        parts = [
            rf"\b{re.escape(kw)}\b" if kw.isascii() and kw.isalpha() else re.escape(kw)
            for kw in keywords
        ]
        pattern = r"(?:" + "|".join(parts) + r")[^\d-]*?(-?\d+\.?\d*)"
        match = re.search(pattern, text, re.IGNORECASE)
        return float(match.group(1)) if match else None

    def _parse_line(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        if len(points) >= 2:
            return ParsedCommand("draw_line", {"start": points[0], "end": points[1]})
        return ParsedCommand(
            "draw_line",
            {"start": (0.0, 0.0, 0.0), "end": (100.0, 100.0, 0.0)},
            note="used default coordinates; command did not specify two points",
        )

    def _parse_circle(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        radius = self._keyword_number(text, "半径", "radius", "r")
        if radius is None:
            numbers = self._numbers(text)
            radius = numbers[0] if numbers else 50.0
        center = points[0] if points else (0.0, 0.0, 0.0)
        return ParsedCommand("draw_circle", {"center": center, "radius": radius})

    def _parse_arc(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        center = points[0] if points else (0.0, 0.0, 0.0)
        radius = self._keyword_number(text, "半径", "radius", "r")
        if radius is None:
            numbers = self._numbers(text)
            radius = numbers[0] if numbers else 50.0
        start_angle = self._keyword_number(text, "起始角度", "start angle")
        end_angle = self._keyword_number(text, "结束角度", "end angle")
        return ParsedCommand(
            "draw_arc",
            {
                "center": center,
                "radius": radius,
                "start_angle": start_angle if start_angle is not None else 0.0,
                "end_angle": end_angle if end_angle is not None else 90.0,
            },
        )

    def _parse_ellipse(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        center = points[0] if points else (0.0, 0.0, 0.0)
        major = self._keyword_number(text, "长轴", "major axis", "major")
        minor = self._keyword_number(text, "短轴", "minor axis", "minor")
        rotation = self._keyword_number(text, "旋转", "角度", "rotation")
        numbers = self._numbers(text)
        if major is None:
            major = numbers[0] if numbers else 100.0
        if minor is None:
            minor = numbers[1] if len(numbers) > 1 else major / 2
        return ParsedCommand(
            "draw_ellipse",
            {
                "center": center,
                "major_axis": major,
                "minor_axis": minor,
                "rotation": rotation if rotation is not None else 0.0,
            },
        )

    def _parse_rectangle(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        if len(points) >= 2:
            return ParsedCommand("draw_rectangle", {"corner1": points[0], "corner2": points[1]})
        width = self._keyword_number(text, "宽度", "width") or 100.0
        height = self._keyword_number(text, "高度", "height") or 100.0
        corner1 = points[0] if points else (0.0, 0.0, 0.0)
        corner2 = (corner1[0] + width, corner1[1] + height, corner1[2])
        return ParsedCommand(
            "draw_rectangle",
            {"corner1": corner1, "corner2": corner2},
            note="derived second corner from width/height",
        )

    def _parse_polyline(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        closed = any(w in text for w in ("闭合", "封闭", "closed"))
        if len(points) < 2:
            return ParsedCommand("error", note="drawing a polyline requires at least two coordinate points")
        return ParsedCommand("draw_polyline", {"points": points, "closed": closed})

    def _parse_text(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        quote_match = re.search(r"[\"'](.*?)[\"']", text)
        content = quote_match.group(1) if quote_match else "sample text"
        height = self._keyword_number(text, "高度", "height") or 2.5
        rotation = self._keyword_number(text, "旋转", "角度", "rotation") or 0.0
        position = points[0] if points else (0.0, 0.0, 0.0)
        return ParsedCommand(
            "draw_text",
            {"position": position, "text": content, "height": height, "rotation": rotation},
        )

    def _parse_hatch(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        pattern_match = re.search(r"(?:图案|pattern)[^\w]*?[\"']?(\w+)[\"']?", text, re.IGNORECASE)
        pattern_name = pattern_match.group(1).upper() if pattern_match else "SOLID"
        scale = self._keyword_number(text, "比例", "缩放", "scale") or 1.0
        if len(points) < 3:
            return ParsedCommand("error", note="drawing a hatch requires at least three boundary points")
        return ParsedCommand("draw_hatch", {"points": points, "pattern_name": pattern_name, "scale": scale})

    def _parse_dimension(self, text: str) -> ParsedCommand:
        points = self._coordinates(text)
        if len(points) < 2:
            return ParsedCommand("error", note="adding a dimension requires start and end points")
        text_position = points[2] if len(points) > 2 else None
        textheight = self._keyword_number(text, "文字高度", "text height") or 5.0
        return ParsedCommand(
            "add_dimension",
            {"start": points[0], "end": points[1], "text_position": text_position, "textheight": textheight},
        )

    def _parse_create_layer(self, text: str) -> ParsedCommand:
        match = re.search(r"(?:图层|layer)[^\w]*?[\"']?([\w\-一-龥]+)[\"']?", text, re.IGNORECASE)
        layer_name = match.group(1) if match else None
        if not layer_name:
            return ParsedCommand("error", note="could not determine the layer name to create")
        return ParsedCommand("create_layer", {"layer_name": layer_name})

    def _parse_save(self, text: str) -> ParsedCommand:
        match = re.search(r"[\"'](.*?)[\"']", text)
        if not match:
            return ParsedCommand("error", note="could not determine the file path to save to")
        return ParsedCommand("save", {"file_path": match.group(1)})
