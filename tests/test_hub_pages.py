from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.api import routes_logs, routes_v2_pages


class HubPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_observability_page_uses_expected_template_and_page_name(self) -> None:
        captured: dict[str, object] = {}

        class Templates:
            def TemplateResponse(self, request, template_name, context):  # noqa: N802
                captured["request"] = request
                captured["template"] = template_name
                captured["context"] = context
                return {"template": template_name, "context": context}

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(templates=Templates())))

        result = await routes_v2_pages.observability_page(request)

        self.assertEqual(result["template"], "observability.html")
        self.assertEqual(captured["context"]["page"], "observability")

    async def test_research_page_uses_expected_template_and_page_name(self) -> None:
        captured: dict[str, object] = {}

        class Templates:
            def TemplateResponse(self, request, template_name, context):  # noqa: N802
                captured["request"] = request
                captured["template"] = template_name
                captured["context"] = context
                return {"template": template_name, "context": context}

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(templates=Templates())))

        result = await routes_v2_pages.research_page(request)

        self.assertEqual(result["template"], "research.html")
        self.assertEqual(captured["context"]["page"], "research")

    async def test_opportunities_page_redirects_to_radar_pipeline(self) -> None:
        response = await routes_v2_pages.opportunities_page(SimpleNamespace())

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/opportunity-radar#opportunity-pipeline")

    async def test_scanner_page_redirects_to_radar_scanner_section(self) -> None:
        response = await routes_v2_pages.scanner_page(SimpleNamespace())

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/opportunity-radar#scanner-control")

    async def test_logs_page_redirects_to_observability_events(self) -> None:
        response = await routes_logs.logs_page(SimpleNamespace())

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/observability#event-stream")

    async def test_forecast_stack_page_redirects_to_research_provider_stack(self) -> None:
        response = await routes_v2_pages.forecasting_stack_page(SimpleNamespace())

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/research#provider-stack")


if __name__ == "__main__":
    unittest.main()
