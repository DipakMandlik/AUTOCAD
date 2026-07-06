from nlp.fallback import FallbackParser

parser = FallbackParser()


def test_parse_line_english():
    result = parser.parse("draw a line from (0,0) to (100,50)")
    assert result.operation == "draw_line"
    assert result.params["start"] == (0.0, 0.0, 0.0)
    assert result.params["end"] == (100.0, 50.0, 0.0)


def test_parse_circle_chinese_with_color():
    result = parser.parse("画一个圆，圆心(10,10)，半径20，红色")
    assert result.operation == "draw_circle"
    assert result.params["center"] == (10.0, 10.0, 0.0)
    assert result.params["radius"] == 20.0
    assert result.params["color"] == 1


def test_arc_not_misclassified_as_circle():
    # "圆" (circle) is a substring of "圆弧" (arc); a naive shortest-first
    # keyword scan misclassifies this as a circle.
    result = parser.parse("画一个圆弧，中心(0,0)，半径10，起始角度0，结束角度180")
    assert result.operation == "draw_arc"
    assert result.params["radius"] == 10.0
    assert result.params["end_angle"] == 180.0


def test_radius_keyword_not_matched_inside_unrelated_word():
    # the single-letter "r" shorthand must not match the "r" inside "draw"
    result = parser.parse("draw a circle at (5,5) radius 15")
    assert result.params["radius"] == 15.0


def test_radius_shorthand_with_equals():
    result = parser.parse("draw a circle at (5,5) r=15")
    assert result.params["radius"] == 15.0


def test_unknown_command():
    result = parser.parse("do something incomprehensible")
    assert result.operation == "unknown"


def test_create_layer_command():
    result = parser.parse("创建图层 walls")
    assert result.operation == "create_layer"
    assert result.params["layer_name"] == "walls"


def test_save_command_requires_quoted_path():
    result = parser.parse('save the drawing to "output/test.dxf"')
    assert result.operation == "save"
    assert result.params["file_path"] == "output/test.dxf"


def test_polyline_requires_two_points():
    result = parser.parse("draw a polyline")
    assert result.operation == "error"


def test_extract_color_numeric_string():
    assert parser.extract_color("42") == 42


def test_extract_color_unknown_returns_none():
    assert parser.extract_color("mauve") is None


def test_extract_color_name_case_insensitive():
    assert parser.extract_color("Draw it BLUE please") == 5
