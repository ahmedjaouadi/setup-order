from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.api import routes_opportunity_radar


class OpportunityRadarPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_route_uses_expected_template_and_page_name(self) -> None:
        captured: dict[str, object] = {}

        class Templates:
            def TemplateResponse(self, request, template_name, context):  # noqa: N802
                captured["request"] = request
                captured["template"] = template_name
                captured["context"] = context
                return {"template": template_name, "context": context}

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(templates=Templates())
            )
        )

        result = await routes_opportunity_radar.opportunity_radar_page(request)

        self.assertEqual(result["template"], "opportunity_radar.html")
        self.assertEqual(captured["template"], "opportunity_radar.html")
        self.assertEqual(captured["context"]["page"], "opportunity-radar")


if __name__ == "__main__":
    unittest.main()
