"""Tests for clock widget JS and its inclusion in base.html."""

import os

from bs4 import BeautifulSoup


def render_base_html(**context):
    from jinja2 import Environment

    base_html_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "meshwiki",
        "templates",
        "base.html",
    )
    env = Environment(autoescape=False)
    with open(base_html_path) as f:
        tpl = env.from_string(f.read())
    return tpl.render(**context)


class TestClockWidgetScriptTag:
    def test_script_tag_present_in_base_html(self):
        rendered = render_base_html(
            app_title="MeshWiki",
            page_tree=None,
            request=_mock_request(),
        )
        soup = BeautifulSoup(rendered, "html.parser")
        script_tags = soup.find_all("script", src=True)
        srcs = [s["src"] for s in script_tags]
        assert any(
            "clock_widget.js" in src for src in srcs
        ), f"clock_widget.js script tag not found in base.html. Found: {srcs}"


class TestClockWidgetFile:
    def test_clock_widget_js_exists(self):
        clock_js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "clock_widget.js",
        )
        assert os.path.isfile(
            clock_js_path
        ), f"clock_widget.js not found at {clock_js_path}"

    def test_clock_widget_js_has_no_innerHTML(self):
        clock_js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "clock_widget.js",
        )
        with open(clock_js_path) as f:
            content = f.read()
        assert "innerHTML" not in content, "clock_widget.js must not use innerHTML"

    def test_clock_widget_js_has_no_eval(self):
        clock_js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "clock_widget.js",
        )
        with open(clock_js_path) as f:
            content = f.read()
        assert "eval" not in content, "clock_widget.js must not use eval"


class _mock_request:
    class session:
        @staticmethod
        def get(_):
            return None
