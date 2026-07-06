import pytest

from engine.geometry.primitives import DrawingPlan, LineEntity, RectangleEntity, TextEntity
from engine.validator.engine import ValidationEngine
from export.renderer import render_svg
from templates.library import TEMPLATE_LIBRARY, build_title_block


def test_catalog_keys_match_definition_names():
    for key, definition in TEMPLATE_LIBRARY.items():
        assert key == definition.name
        assert definition.description
        assert definition.width > 0
        assert definition.height > 0


@pytest.mark.parametrize("name", sorted(TEMPLATE_LIBRARY))
def test_every_template_validates_and_renders(name):
    entities = build_title_block(
        name, title="T", drawn_by="D", date="2026-01-01", scale="1:1", sheet_number="1/1"
    )
    plan = DrawingPlan(operations=entities)
    report = ValidationEngine().validate(plan)
    assert not report.errors, f"{name}: {report.errors}"

    svg = render_svg(plan)
    assert "<svg" in svg


def test_bare_title_block_has_only_border_and_grid():
    entities = build_title_block("a4_landscape")
    assert len(entities) == 5  # border + title-block box + 3 divider lines
    assert sum(1 for e in entities if isinstance(e, RectangleEntity)) == 2
    assert sum(1 for e in entities if isinstance(e, LineEntity)) == 3
    assert sum(1 for e in entities if isinstance(e, TextEntity)) == 0


def test_each_optional_field_adds_one_text_entity():
    entities = build_title_block("a4_landscape", title="My Drawing")
    texts = [e for e in entities if isinstance(e, TextEntity)]
    assert len(texts) == 1
    assert texts[0].text == "Title: My Drawing"


def test_all_fields_populated_adds_five_text_entities():
    entities = build_title_block(
        "a4_landscape", title="T", drawn_by="D", date="2026-01-01", scale="1:100", sheet_number="1/3"
    )
    texts = [e for e in entities if isinstance(e, TextEntity)]
    assert len(texts) == 5
    assert {t.text for t in texts} == {
        "Title: T",
        "Drawn by: D",
        "Date: 2026-01-01",
        "Scale: 1:100",
        "Sheet: 1/3",
    }


def test_title_block_sits_in_bottom_right_corner():
    definition = TEMPLATE_LIBRARY["a4_landscape"]
    entities = build_title_block("a4_landscape")
    title_block_box = entities[1]  # second rectangle is the title-block box
    assert isinstance(title_block_box, RectangleEntity)
    assert title_block_box.corner2[0] == pytest.approx(definition.width - 10.0)
    assert title_block_box.corner1[1] == pytest.approx(10.0)


def test_origin_translates_every_entity():
    plain = build_title_block("a4_landscape", title="T")
    shifted = build_title_block("a4_landscape", title="T", origin=(100.0, 50.0, 0.0))
    for plain_entity, shifted_entity in zip(plain, shifted):
        if isinstance(plain_entity, RectangleEntity):
            assert shifted_entity.corner1 == pytest.approx(
                (plain_entity.corner1[0] + 100.0, plain_entity.corner1[1] + 50.0, 0.0)
            )
        elif isinstance(plain_entity, LineEntity):
            assert shifted_entity.start == pytest.approx(
                (plain_entity.start[0] + 100.0, plain_entity.start[1] + 50.0, 0.0)
            )
        elif isinstance(plain_entity, TextEntity):
            assert shifted_entity.position == pytest.approx(
                (plain_entity.position[0] + 100.0, plain_entity.position[1] + 50.0, 0.0)
            )


def test_two_element_origin_defaults_z_to_zero():
    # REST/MCP callers send origin as a plain [x, y] list, unlike Point3
    # fields inside a validated Entity (which pad missing z via a
    # BeforeValidator) — build_title_block must handle that itself.
    entities = build_title_block("a4_landscape", title="T", origin=[100.0, 50.0])
    border = entities[0]
    assert isinstance(border, RectangleEntity)
    assert border.corner1 == pytest.approx((110.0, 60.0, 0.0))


def test_unknown_template_raises_key_error():
    with pytest.raises(KeyError, match="not_a_real_template"):
        build_title_block("not_a_real_template")


def test_unknown_template_not_in_catalog():
    assert "not_a_real_template" not in TEMPLATE_LIBRARY
