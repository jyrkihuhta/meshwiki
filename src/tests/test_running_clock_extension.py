"""Unit tests for the RunningClock macro."""

from meshwiki.core.parser import parse_wiki_content


class TestRunningClockMacro:
    def test_running_clock_default_timezone(self):
        """<<RunningClock>> renders data-timezone="UTC"."""
        html = parse_wiki_content("<<RunningClock>>")
        assert 'data-timezone="UTC"' in html

    def test_running_clock_paris_timezone(self):
        """<<RunningClock timezone="Europe/Paris">> renders the correct timezone."""
        html = parse_wiki_content('<<RunningClock timezone="Europe/Paris">>')
        assert 'data-timezone="Europe/Paris"' in html

    def test_running_clock_invalid_timezone(self):
        """Invalid timezone renders .macro-error span."""
        html = parse_wiki_content('<<RunningClock timezone="Mars/Olympus">>')
        assert "macro-error" in html
        assert "Mars/Olympus" in html
        assert "running-clock" not in html

    def test_running_clock_has_required_attributes(self):
        """Rendered HTML contains the running-clock class and data-clock attribute."""
        html = parse_wiki_content("<<RunningClock>>")
        assert 'class="running-clock"' in html
        assert "data-clock" in html

    def test_running_clock_in_paragraph(self):
        """Macro works when embedded in a paragraph."""
        html = parse_wiki_content("The time is: <<RunningClock>> right now.")
        assert 'data-timezone="UTC"' in html

    def test_running_clock_not_in_code_block(self):
        """Macro inside ``` should be literal text."""
        content = "```\n<<RunningClock>>\n```"
        html = parse_wiki_content(content)
        assert "running-clock" not in html
        assert "data-clock" not in html

    def test_running_clock_not_in_tilde_code(self):
        """Macro inside ~~~ should be literal text."""
        content = "~~~\n<<RunningClock>>\n~~~"
        html = parse_wiki_content(content)
        assert "running-clock" not in html
        assert "data-clock" not in html

    def test_running_clock_new_york_timezone(self):
        """<<RunningClock timezone="America/New_York">> renders correctly."""
        html = parse_wiki_content('<<RunningClock timezone="America/New_York">>')
        assert 'data-timezone="America/New_York"' in html
        assert 'class="running-clock"' in html
        assert "data-clock" in html
