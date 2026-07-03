from __future__ import annotations

from typing import Any


class DecisionExplainer:
    def explain(self, trace: dict[str, Any]) -> str:
        payload = trace.get("trace") if isinstance(trace.get("trace"), dict) else {}
        message = payload.get("human_message")
        if message:
            return str(message)
        decision_type = trace.get("decision_type") or payload.get("decision_type") or "Decision"
        final = trace.get("final_decision") or payload.get("final_decision") or payload.get("decision")
        reasons = payload.get("reason_codes") if isinstance(payload.get("reason_codes"), list) else []
        if reasons:
            return f"{decision_type}: {final} because {', '.join(map(str, reasons))}."
        return f"{decision_type}: {final}."
