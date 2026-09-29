"""
Universal business cost & efficiency impact analysis.

This module is intentionally industry-agnostic. It reads a meeting
transcript and extracts:
    - current business/process pain points
    - cost/time/resource drivers
    - automation opportunities
    - numeric facts actually mentioned in the meeting
    - missing inputs needed for client-specific calculations
    - conservative industry-benchmark assumptions when client data is missing
    - safe example calculations for business conversations

It never assumes a particular industry, workflow, salary, price, saving,
or ROI value.

Only values explicitly supported by the meeting are treated as known.
Missing values remain missing. Calculations are performed in Python using a
small safe arithmetic evaluator; model-generated Python/code is never
executed.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import (
    SARVAM_API_KEY,
    SARVAM_CHAT_URL,
    SARVAM_CHAT_MODEL,
)


# ---------------------------------------------------------------------------
# Universal extraction prompt
# ---------------------------------------------------------------------------

BUSINESS_IMPACT_PROMPT = """
You are a senior business-process analyst.

Analyze the COMPLETE customer meeting transcript and identify business cost,
time, capacity, resource-utilization, operational-efficiency, revenue-leakage,
error/rework, travel/dispatch, inventory, communication, reporting, or other
measurable impact opportunities that are actually supported by the meeting.

   

SOURCE RULES:
1. Use only information actually present in the transcript.
2. A customer statement about a current problem/process can establish a
   current-state fact.
3. A requested automation can establish a potential improvement area, but it
   does NOT establish a guaranteed saving.
4. Never invent salary, hourly rates, transaction volumes, percentages,
   savings percentages, revenue, margins, prices, employee counts, or ROI.
5. Extract numeric values mentioned in the meeting and preserve their meaning.
6. Distinguish known meeting facts from values that are missing.
7. If a calculation cannot be performed because an input is missing, report
   the missing input instead of guessing it.
8. You may propose a useful calculation formula, but every variable must be
   explicitly named and every known value must have a source/evidence.
9. Do not claim that an automation WILL save a particular amount unless the
   meeting provides sufficient values to calculate it.
10. Do not use generic industry benchmarks as if they came from the client.
11. Do not create opportunities merely because they sound commercially useful.
12. If no measurable business-impact opportunity can be supported, return an
   empty opportunities array and explain why in analysis_notes.

For each opportunity, identify:
- what the business does today
- what creates cost/time/resource consumption
- what requested/proposed automation changes
- what should be measured
- what calculation can be performed
- which values are known from the meeting
- which values are missing
- whether the result is a meeting-supported calculation, an illustrative
  scenario, or not calculable yet

Allowed impact_type examples (not exhaustive):
labour_time, admin_time, processing_time, reporting_time, travel_cost,
resource_utilization, capacity_recovery, error_rework, inventory_loss,
communication_cost, payment_delay, revenue_leakage, customer_handling,
management_overhead, other.

Return ONLY valid JSON in this exact shape:

{
  "business_profile": {
    "industry": "only if supported by the transcript",
    "business_summary": "concise summary supported by transcript"
  },
  "current_processes": [
    {
      "area": "business/process area",
      "current_state": "what happens today",
      "pain_point": "what consumes time/cost/resources",
      "evidence": "short supporting statement from meeting"
    }
  ],
  "opportunities": [
    {
      "id": "BI-01",
      "area": "process area",
      "current_cost_driver": "time/cost/resource driver",
      "current_state": "current process",
      "automation_opportunity": "requested or discussed automation",
      "impact_type": "one allowed impact type",
      "metric": "what should be measured",
      "unit": "hours/month, currency/month, requests/month, etc.",
      "formula": "safe arithmetic formula using variable names, or empty string",
      "variables": [
        {
          "name": "machine_safe_variable_name",
          "label": "human-readable input",
          "value": null,
          "low_value": null,
          "high_value": null,
          "unit": "unit",
          "source": "MEETING | INDUSTRY_BENCHMARK_ESTIMATE | MISSING",
          "evidence": "supporting statement or benchmark assumption"
        }
      ],
      "result_type": "MEETING_CALCULATED | ILLUSTRATIVE_ONLY | MISSING_INPUTS",
      "benchmark_basis": "why this benchmark is appropriate",
      "assumption_note": "clearly state that benchmark values are illustrative",
      "example_calculation": "human-readable example calculation with numbers and units",
      "calculation_note": "what the result means and any limitation"
    }
  ],
  "explicit_numbers": [
    {
      "label": "what the number refers to",
      "value": 0,
      "unit": "currency, count, percentage, time, etc.",
      "evidence": "supporting meeting statement"
    }
  ],
  "missing_inputs": [
    {
      "label": "input needed",
      "why_needed": "why it is required for calculation",
      "unit": "unit"
    }
  ],
  "analysis_notes": [
    "important limitation or interpretation"
  ]
}

Important:
- Use null for unknown numeric values.
- Do not turn a price quoted for the software into a business saving.
- Do not treat implementation cost as an operational benefit.
- Do not calculate ROI/payback unless both a valid benefit and a valid project
  cost are explicitly available and the calculation is clearly labelled.
"""


# ---------------------------------------------------------------------------
# Sarvam helpers
# ---------------------------------------------------------------------------


def _extract_chat_content(data: dict) -> str:
    choices = data.get("choices")
    if not choices:
        raise RuntimeError("Sarvam returned no choices in the chat response.")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Sarvam chat response has no valid message.")

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or ""
                if value:
                    parts.append(str(value))
        combined = "\n".join(parts).strip()
        if combined:
            return combined

    raise RuntimeError("Sarvam returned an empty response.")


def _extract_json(text: str) -> dict:
    cleaned = (text or "").strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(cleaned[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        "Sarvam returned invalid business-impact JSON.\n"
        f"Response: {text[:2000]}"
    )


def _call_sarvam(system_prompt: str, user_prompt: str) -> str:
    if not SARVAM_API_KEY:
        raise RuntimeError(
            "SARVAM_API_KEY is not set. Check your .env file."
        )

    payload = {
        "model": SARVAM_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "reasoning_effort": None,
        "max_tokens": 7000,
        "temperature": 0.0,
    }

    request = Request(
        SARVAM_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Sarvam business-impact API failed with HTTP {exc.code}: {body[:1000]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            "Could not connect to the Sarvam chat API for business-impact analysis."
        ) from exc

    if not 200 <= status < 300:
        raise RuntimeError(
            f"Sarvam business-impact API failed with HTTP {status}: {body[:1000]}"
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sarvam returned invalid API JSON.") from exc

    return _extract_chat_content(data)


# ---------------------------------------------------------------------------
# Safe arithmetic evaluator
# ---------------------------------------------------------------------------

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST, variables: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, variables)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ValueError(f"Unknown variable: {node.id}")
        return float(variables[node.id])

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _safe_eval(node.left, variables)
        right = _safe_eval(node.right, variables)
        return float(_BINARY_OPERATORS[type(node.op)](left, right))

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        value = _safe_eval(node.operand, variables)
        return float(_UNARY_OPERATORS[type(node.op)](value))

    raise ValueError("Unsupported expression element.")


def _calculate_formula(formula: str, variables: list[dict[str, Any]]) -> tuple[float | None, float | None, str]:
    if not formula or not formula.strip():
        return None, None, "No formula was supplied."

    low_values = {}
    high_values = {}
    missing = []
    has_benchmark = False

    for variable in variables:
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("name", "")).strip()
        value = variable.get("value")
        low_value = variable.get("low_value")
        high_value = variable.get("high_value")
        source = str(variable.get("source", "")).upper()
        if not name:
            continue
        if source == "MEETING" and isinstance(value, (int, float)) and not isinstance(value, bool):
            low_values[name] = float(value)
            high_values[name] = float(value)
        elif source == "INDUSTRY_BENCHMARK_ESTIMATE":
            low = low_value if isinstance(low_value, (int, float)) else value
            high = high_value if isinstance(high_value, (int, float)) else value
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                low_values[name] = float(min(low, high))
                high_values[name] = float(max(low, high))
                has_benchmark = True
            else:
                missing.append(name)
        else:
            missing.append(name)

    if missing:
        return None, None, f"Missing inputs: {', '.join(missing)}"

    try:
        tree = ast.parse(formula, mode="eval")
        low_result = _safe_eval(tree, low_values)
        high_result = _safe_eval(tree, high_values)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return None, None, f"Formula could not be safely calculated: {exc}"

    if has_benchmark:
        return min(low_result, high_result), max(low_result, high_result), "Illustrative range calculated from industry benchmark estimates; validate assumptions with the client."
    return low_result, high_result, "Calculated only from values explicitly supported by the meeting."


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_analysis(data: dict) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("Business-impact analysis is not a JSON object.")

    profile = data.get("business_profile", {})
    if not isinstance(profile, dict):
        profile = {}

    processes = data.get("current_processes", [])
    opportunities = data.get("opportunities", [])
    explicit_numbers = data.get("explicit_numbers", [])
    missing_inputs = data.get("missing_inputs", [])
    notes = data.get("analysis_notes", [])

    if not isinstance(processes, list):
        processes = []
    if not isinstance(opportunities, list):
        opportunities = []
    if not isinstance(explicit_numbers, list):
        explicit_numbers = []
    if not isinstance(missing_inputs, list):
        missing_inputs = []
    if not isinstance(notes, list):
        notes = []

    normalized_opportunities = []

    for index, item in enumerate(opportunities, start=1):
        if not isinstance(item, dict):
            continue

        variables = item.get("variables", [])
        if not isinstance(variables, list):
            variables = []

        formula = str(item.get("formula", "")).strip()
        low_result, high_result, calculation_note = _calculate_formula(formula, variables)

        result_type = str(item.get("result_type", "")).strip().upper()

        has_benchmark = any(isinstance(v, dict) and str(v.get("source", "")).upper() == "INDUSTRY_BENCHMARK_ESTIMATE" for v in variables)

        if low_result is not None:
            result_type = "ILLUSTRATIVE_ONLY" if has_benchmark else "MEETING_CALCULATED"
        elif result_type not in {"MISSING_INPUTS", "ILLUSTRATIVE_ONLY"}:
            result_type = "MISSING_INPUTS"

        normalized_opportunities.append({
            "id": str(item.get("id") or f"BI-{index:02d}"),
            "area": str(item.get("area", "")).strip(),
            "current_cost_driver": str(item.get("current_cost_driver", "")).strip(),
            "current_state": str(item.get("current_state", "")).strip(),
            "automation_opportunity": str(item.get("automation_opportunity", "")).strip(),
            "impact_type": str(item.get("impact_type", "other")).strip(),
            "metric": str(item.get("metric", "")).strip(),
            "unit": str(item.get("unit", "")).strip(),
            "formula": formula,
            "variables": variables,
            "result_type": result_type,
            "calculated_value": low_result,
            "calculated_low": low_result,
            "calculated_high": high_result,
            "benchmark_basis": str(item.get("benchmark_basis", "")).strip(),
            "assumption_note": str(item.get("assumption_note", "")).strip(),
            "example_calculation": str(item.get("example_calculation", "")).strip(),
            "calculation_note": calculation_note,
            "model_calculation_note": str(item.get("calculation_note", "")).strip(),
        })

    return {
        "business_profile": {
            "industry": str(profile.get("industry", "")).strip(),
            "business_summary": str(profile.get("business_summary", "")).strip(),
        },
        "current_processes": processes,
        "opportunities": normalized_opportunities,
        "explicit_numbers": explicit_numbers,
        "missing_inputs": missing_inputs,
        "analysis_notes": [str(note).strip() for note in notes if str(note).strip()],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_business_impact(
    transcript_text: str,
    verified_requirements: list[str] | None = None,
) -> dict:
    """
    Analyze one meeting transcript for business cost/efficiency opportunities.

    verified_requirements is optional. When supplied, it helps the model
    understand which requested capabilities were actually confirmed by the
    requirement-extraction stage, but the transcript remains the source of
    truth for business-impact evidence.
    """

    if not transcript_text or not transcript_text.strip():
        raise RuntimeError("Transcript is empty. Cannot analyze business impact.")

    requirement_context = json.dumps(
        verified_requirements or [],
        ensure_ascii=False,
        indent=2,
    )

    print("Business Impact: analyzing client business and cost drivers...")

    response = _call_sarvam(
        BUSINESS_IMPACT_PROMPT,
        (
            "VERIFIED CUSTOMER REQUIREMENTS (optional context):\n\n"
            f"{requirement_context}\n\n"
            "COMPLETE MEETING TRANSCRIPT:\n\n"
            f"{transcript_text}"
        ),
    )

    analysis = _normalize_analysis(_extract_json(response))

    calculated = sum(
        1
        for item in analysis["opportunities"]
        if item.get("calculated_value") is not None
        and item.get("result_type") == "MEETING_CALCULATED"
    )

    illustrative = sum(
        1
        for item in analysis["opportunities"]
        if item.get("result_type") == "ILLUSTRATIVE_ONLY"
    )

    missing = sum(
        1
        for item in analysis["opportunities"]
        if item.get("calculated_value") is None
    )

    print(
        "Business Impact: "
        f"{len(analysis['opportunities'])} opportunities, "
        f"{calculated} meeting-calculated, "
        f"{illustrative} illustrative benchmark scenarios, "
        f"{missing} requiring inputs."
    )

    return analysis


def build_business_impact_prompt_data(analysis: dict) -> str:
    """Create concise, proposal-safe JSON for the proposal writer."""
    return json.dumps(
        analysis,
        ensure_ascii=False,
        indent=2,
    )


def save_business_impact_report(
    analysis: dict,
    output_path: str,
) -> str:
    """Save the structured analysis for auditing/reuse."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(
            analysis,
            file,
            ensure_ascii=False,
            indent=2,
        )

    return output_path
