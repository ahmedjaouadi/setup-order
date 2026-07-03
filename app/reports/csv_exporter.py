from __future__ import annotations

import csv
from io import StringIO


class CsvExporter:
    def opportunities(self, rows: list[dict]) -> str:
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["opportunity_id", "symbol", "opportunity_type", "score", "status"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
