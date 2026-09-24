from jinja2 import Environment


def render_hover_card(**context):
    import os

    env = Environment(autoescape=False)
    tpl_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "meshwiki",
        "templates",
        "partials",
        "hover_card.html",
    )
    with open(tpl_path) as f:
        tpl = env.from_string(f.read())
    return tpl.render(**context)


def test_hover_card_has_id():
    result = render_hover_card(
        exists=True, page_name="Test_Page", excerpt="Test excerpt"
    )
    assert 'id="wiki-hover-card"' in result


def test_hover_card_renders_excerpt_when_exists():
    result = render_hover_card(
        exists=True, page_name="Test_Page", excerpt="Test excerpt"
    )
    assert "Test excerpt" in result
    assert "Test Page" in result


def test_hover_card_renders_not_found_when_not_exists():
    result = render_hover_card(
        exists=False, page_name="Test_Page", excerpt="Test excerpt"
    )
    assert "Page not found" in result
