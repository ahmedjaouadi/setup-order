from __future__ import annotations

import unittest
from pathlib import Path

TEMPLATE = Path("app/gui/templates/opportunity_radar.html")
HUB_PAGES_JS = Path("app/gui/static/js/hub-pages.js")


class DetectionTechniquesPanelTests(unittest.TestCase):
    """The Radar page must expose the technique library between Scanner and shortlist."""

    def test_template_has_techniques_panel_between_scanner_and_shortlist(self) -> None:
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="detection-techniques"', html)
        self.assertIn('id="detection-techniques-table"', html)
        # Panel sits between the scanner controls and the opportunity pipeline.
        self.assertLess(html.index('id="scanner-control"'), html.index('id="detection-techniques"'))
        self.assertLess(
            html.index('id="detection-techniques"'), html.index('id="opportunity-pipeline"')
        )

    def test_hub_pages_js_renders_panel_and_detected_by_column(self) -> None:
        source = HUB_PAGES_JS.read_text(encoding="utf-8")
        self.assertIn("renderDetectionTechniquesPanel", source)
        self.assertIn("/api/techniques", source)
        # The shortlist gains a "Detected by" column fed by detected_by.
        self.assertIn('["detected_by", "Detecte par"]', source)


if __name__ == "__main__":
    unittest.main()
