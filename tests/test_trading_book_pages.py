from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.api import routes_orders, routes_positions


class TradingBookPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_orders_page_uses_expected_template_and_page_name(self) -> None:
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

        result = await routes_orders.orders_page(request)

        self.assertEqual(result["template"], "orders.html")
        self.assertEqual(captured["template"], "orders.html")
        self.assertEqual(captured["context"]["page"], "orders")

    async def test_positions_page_redirects_to_combined_orders_view(self) -> None:
        request = SimpleNamespace()

        response = await routes_positions.positions_page(request)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/orders")


if __name__ == "__main__":
    unittest.main()
