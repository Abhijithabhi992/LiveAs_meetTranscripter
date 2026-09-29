"""
Proposal generation pipeline.

Pipeline:
    transcript
        -> Stage 1: extract genuine client requirements
        -> Stage 2: classify each requirement against bos_schema.json
        -> Stage 3: write proposal from verified supported requirements
        -> branded English DOCX
        -> optional PDF

The generated document contains:
    1. English proposal

Malayalam translation code is kept below but commented out.
"""

import os
import re
import json
import difflib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime

from docx import Document  # type: ignore[reportMissingImports]
from docx.shared import Pt, Inches, RGBColor  # type: ignore[reportMissingImports]
from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore[reportMissingImports]

from config import (
    SARVAM_API_KEY,
    SARVAM_CHAT_URL,
    SARVAM_CHAT_MODEL,

    # -------------------------------------------------------
    # Malayalam translation configuration
    # Kept intentionally commented out.
    # -------------------------------------------------------
    # SARVAM_TRANSLATE_URL,
    # SARVAM_TRANSLATE_MODEL,
    # SOURCE_LANGUAGE_CODE,
    # TARGET_LANGUAGE_CODE,
    # TRANSLATE_CHUNK_CHARS,

    COMPANY_NAME,
    COMPANY_TAGLINE,
    COMPANY_EMAIL,
    COMPANY_PHONE,
    COMPANY_WEBSITE,
    COMPANY_LOGO_PATH,
    OUTPUT_DIR,
    EXPORT_PDF,
)




from business_impact import (
    analyze_business_impact,
    save_business_impact_report,
)


# ============================================================
# EXTRACT CHAT RESPONSE
# ============================================================

def _extract_chat_content(data):
    """
    Safely extract assistant content from the Sarvam chat response.
    """

    if not isinstance(data, dict):
        raise RuntimeError(
            "Sarvam returned an unexpected response format."
        )

    choices = data.get("choices")

    if not choices:
        raise RuntimeError(
            "Sarvam returned no choices in the chat response."
        )

    first_choice = choices[0]

    if not isinstance(first_choice, dict):
        raise RuntimeError(
            "Sarvam returned an invalid choice object."
        )

    message = first_choice.get("message")

    if not isinstance(message, dict):
        raise RuntimeError(
            "Sarvam chat response does not contain a valid message."
        )

    content = message.get("content")

    if isinstance(content, str):
        content = content.strip()

        if content:
            return content

    if isinstance(content, list):
        text_parts = []

        for item in content:
            if isinstance(item, str):
                text_parts.append(item)

            elif isinstance(item, dict):
                text_value = (
                    item.get("text")
                    or item.get("content")
                    or ""
                )

                if text_value:
                    text_parts.append(str(text_value))

        combined = "\n".join(text_parts).strip()

        if combined:
            return combined

    finish_reason = first_choice.get(
        "finish_reason",
        "unknown"
    )

    raise RuntimeError(
        "Sarvam returned an empty response. "
        f"finish_reason={finish_reason}."
    )

# ============================================================
# STRUCTURED PROPOSAL ENGINE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOS_SCHEMA_PATH = os.path.join(BASE_DIR, "bos_schema.json")


def _load_bos_schema():
    if not os.path.exists(BOS_SCHEMA_PATH):
        raise RuntimeError(
            "bos_schema.json was not found.\n"
            f"Expected file: {BOS_SCHEMA_PATH}"
        )

    try:
        with open(
            BOS_SCHEMA_PATH,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)
    except Exception as exc:
        raise RuntimeError(
            "Could not read bos_schema.json."
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "bos_schema.json must contain a JSON object."
        )

    return data


def _bos_schema_text():
    return json.dumps(
        _load_bos_schema(),
        ensure_ascii=False,
        indent=2
    )


def _extract_json(text_value: str):
    """
    Extract JSON even if the model accidentally wraps it in
    ```json ... ``` or adds a short sentence around it.
    """
    if not text_value:
        raise RuntimeError(
            "Sarvam returned an empty response."
        )

    cleaned = text_value.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r"\s*```$",
            "",
            cleaned
        ).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    first_object = cleaned.find("{")
    last_object = cleaned.rfind("}")

    if first_object != -1 and last_object > first_object:
        candidate = cleaned[first_object:last_object + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        "Sarvam returned invalid JSON.\n"
        "Response received:\n"
        f"{text_value[:2000]}"
    )


def _call_sarvam(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.1
):
    if not SARVAM_API_KEY:
        raise RuntimeError(
            "SARVAM_API_KEY is not set. "
            "Check your .env file."
        )

    if not user_prompt.strip():
        raise RuntimeError(
            "The request sent to Sarvam was empty."
        )

    payload = {
        "model": SARVAM_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            },
        ],
        "reasoning_effort": None,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    print(
        f"Calling Sarvam chat model: "
        f"{SARVAM_CHAT_MODEL}"
    )

    request = Request(
        SARVAM_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            status_code = response.status
            response_text = response.read().decode(
                "utf-8"
            )
    except HTTPError as exc:
        status_code = exc.code
        response_text = exc.read().decode(
            "utf-8",
            errors="replace"
        )
    except URLError as exc:
        raise RuntimeError(
            "Could not connect to the Sarvam chat API."
        ) from exc

    if not 200 <= status_code < 300:
        try:
            error_data = json.loads(response_text)
        except Exception:
            error_data = response_text

        raise RuntimeError(
            "Sarvam chat API failed.\n"
            f"HTTP status: {status_code}\n"
            f"Response: {error_data}"
        )

    try:
        data = json.loads(response_text)
    except Exception as exc:
        raise RuntimeError(
            "Sarvam returned invalid JSON."
        ) from exc

    return _extract_chat_content(data)


# ============================================================
# STAGE 1 - EXTRACT CLIENT REQUIREMENTS
# ============================================================

REQUIREMENT_EXTRACTION_PROMPT = """
You are a senior business analyst extracting requirements from a meeting
transcript.

Your ONLY job is to identify genuine customer requirements and explicit
commercial/timeline information. Do NOT write a proposal.

SOURCE RULES:
1. Read the COMPLETE transcript.
2. A line spoken by CLIENT is a customer requirement only when it expresses
   a need, requested behavior, requested report, requested workflow,
   requested rule, requested integration, or requested business outcome.
3. A line spoken by YOU is NOT a customer requirement unless the CLIENT
   clearly accepts or confirms the proposed capability.
4. Questions alone are not requirements unless the client clearly confirms
   the desired behavior.
5. Never infer a feature merely because it would be useful.
6. Never invent a WhatsApp chatbot or any other feature.
7. Preserve specific business rules exactly in meaning.
8. Preserve explicit commercial statements, including approximate amounts
   and additional-cost conditions.
9. Preserve explicit timeline statements if any.
10. If the transcript is ambiguous, do not convert the ambiguity into a
    requirement.

Return ONLY valid JSON in this exact structure:

{
  "requirements": [
    {
      "id": 1,
      "requirement": "clear concise requirement",
      "evidence": "short supporting transcript statement",
      "basis": "CLIENT" or "CLIENT_ACCEPTED"
    }
  ],
  "commercial": [
    {
      "item": "price or commercial condition",
      "evidence": "supporting transcript statement"
    }
  ],
  "timeline": [
    {
      "item": "timeline statement",
      "evidence": "supporting transcript statement"
    }
  ]
}

Do not include requirements that are merely suggestions from YOU and were not
accepted by the CLIENT.
"""


def _extract_requirements(transcript_text: str):
    print("Stage 1/3: Extracting customer requirements...")

    response = _call_sarvam(
        REQUIREMENT_EXTRACTION_PROMPT,
        (
            "COMPLETE MEETING TRANSCRIPT:\n\n"
            f"{transcript_text}"
        ),
        max_tokens=5000,
        temperature=0.0
    )

    data = _extract_json(response)

    if not isinstance(data, dict):
        raise RuntimeError(
            "Requirement extraction returned an invalid JSON object."
        )

    requirements = data.get("requirements", [])

    if not isinstance(requirements, list):
        raise RuntimeError(
            "Requirement extraction returned an invalid "
            "'requirements' list."
        )

    normalized = []

    for index, item in enumerate(requirements, start=1):
        if not isinstance(item, dict):
            continue

        requirement = str(
            item.get("requirement", "")
        ).strip()

        evidence = str(
            item.get("evidence", "")
        ).strip()

        basis = str(
            item.get("basis", "")
        ).strip().upper()

        if not requirement:
            continue

        if basis not in {
            "CLIENT",
            "CLIENT_ACCEPTED"
        }:
            continue

        normalized.append(
            {
                "id": index,
                "requirement": requirement,
                "evidence": evidence,
                "basis": basis,
            }
        )

    commercial = data.get("commercial", [])
    timeline = data.get("timeline", [])

    if not isinstance(commercial, list):
        commercial = []

    if not isinstance(timeline, list):
        timeline = []

    print(
        f"Extracted {len(normalized)} customer requirements."
    )

    return {
        "requirements": normalized,
        "commercial": commercial,
        "timeline": timeline,
    }


# ============================================================
# STAGE 2 - MATCH REQUIREMENTS AGAINST BOS
# ============================================================

BOS_MATCH_PROMPT = """
You are a strict BOS feature-matching analyst.

You will receive:
1. A structured list of customer requirements.
2. The complete supplied BOS schema.

Classify EVERY customer requirement exactly once.

IMPORTANT:
- "SUPPORTED" means the same capability is explicitly represented in BOS.
- Do NOT mark something supported merely because BOS has a related feature.
- If a requirement contains both a BOS-supported part and an unsupported
  extension, classify it as "PARTIAL".
- For PARTIAL, separate the supported portion from the unsupported portion.
- "VARIATION" means the requested capability is absent or materially more
  specific than the BOS capability.
- Never invent a variation.
- Never invent a BOS feature.
- Do not use general software knowledge.
- Match semantic meaning, not exact wording.

Example:
BOS: "View service requests and assign them to staff."
Customer: "Automatically choose the best technician using location,
workload, completed jobs and pending jobs."
Result: VARIATION, because ordinary assignment exists but that specific
automatic decision algorithm is not represented.

Return ONLY valid JSON:

{
  "classifications": [
    {
      "id": 1,
      "status": "SUPPORTED",
      "bos_feature_ids": ["SM-04"],
      "client_requirement": "requirement text to use in Client Requirements",
      "variation": ""
    }
  ]
}

Allowed status values:
SUPPORTED
PARTIAL
VARIATION

Rules for output text:
- For SUPPORTED, put ONLY the supported customer requirement in
  "client_requirement" and leave "variation" empty.
- For PARTIAL, put ONLY the BOS-supported part in "client_requirement"
  and ONLY the unsupported part in "variation".
- For VARIATION, leave "client_requirement" empty and put the complete
  unsupported customer-requested capability in "variation".
"""


def _match_requirements(extracted):
    print("Stage 2/3: Matching requirements against BOS schema...")

    requirements_json = json.dumps(
        extracted["requirements"],
        ensure_ascii=False,
        indent=2
    )

    response = _call_sarvam(
        BOS_MATCH_PROMPT,
        (
            "CUSTOMER REQUIREMENTS:\n\n"
            f"{requirements_json}\n\n"
            "COMPLETE BOS SCHEMA:\n\n"
            f"{_bos_schema_text()}"
        ),
        max_tokens=5000,
        temperature=0.0
    )

    data = _extract_json(response)

    if not isinstance(data, dict):
        raise RuntimeError(
            "BOS matching returned an invalid JSON object."
        )

    classifications = data.get(
        "classifications",
        []
    )

    if not isinstance(classifications, list):
        raise RuntimeError(
            "BOS matching returned an invalid classifications list."
        )

    valid_ids = {
        int(item["id"])
        for item in extracted["requirements"]
        if str(item.get("id", "")).isdigit()
    }

    result = []

    for item in classifications:
        if not isinstance(item, dict):
            continue

        try:
            requirement_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        if requirement_id not in valid_ids:
            continue

        status = str(
            item.get("status", "")
        ).strip().upper()

        if status not in {
            "SUPPORTED",
            "PARTIAL",
            "VARIATION"
        }:
            continue

        bos_feature_ids = item.get(
            "bos_feature_ids",
            []
        )

        if not isinstance(bos_feature_ids, list):
            bos_feature_ids = []

        client_requirement = str(
            item.get("client_requirement", "")
        ).strip()

        variation = str(
            item.get("variation", "")
        ).strip()

        # Python enforces the separation rules.
        if status == "SUPPORTED":
            if not client_requirement:
                original = next(
                    (
                        r["requirement"]
                        for r in extracted["requirements"]
                        if int(r["id"]) == requirement_id
                    ),
                    ""
                )
                client_requirement = original

            variation = ""

        elif status == "VARIATION":
            client_requirement = ""

        elif status == "PARTIAL":
            if not client_requirement and not variation:
                continue

        result.append(
            {
                "id": requirement_id,
                "status": status,
                "bos_feature_ids": [
                    str(value)
                    for value in bos_feature_ids
                ],
                "client_requirement": client_requirement,
                "variation": variation,
            }
        )

    # Every extracted requirement must receive exactly one classification.
    classified_ids = {
        item["id"]
        for item in result
    }

    missing_ids = valid_ids - classified_ids

    if missing_ids:
        raise RuntimeError(
            "BOS matching did not classify every customer requirement. "
            f"Missing requirement IDs: {sorted(missing_ids)}"
        )

    print(
        "BOS matching completed."
    )

    return _audit_classifications(
        extracted,
        result,
    )


# ============================================================
# STAGE 2B - AUDIT BOS CLASSIFICATION
# ============================================================

BOS_AUDIT_PROMPT = """
You are the final quality-control reviewer for a business proposal.

You must audit the proposed BOS classification of customer requirements.
The COMPLETE BOS schema is the only authority for whether a capability is
supported.

For EVERY customer requirement, return exactly one classification:
SUPPORTED, PARTIAL, or VARIATION.

STRICT RULES:
1. SUPPORTED means the BOS explicitly provides the complete requested
   capability, including the requested rule, automation, condition, report,
   integration, or decision logic.
2. A related BOS feature is NOT enough.
3. If BOS says staff can be assigned to a service request, that does NOT mean
   BOS supports automatic assignment based on location, workload, distance,
   completed jobs, pending jobs, time period, ratings, or feedback.
4. If BOS supports a normal report but the customer asks for a specific
   graphical report containing additional metrics, the additional specificity
   is a VARIATION or PARTIAL requirement.
5. If a customer asks to exclude staff based on negative feedback, ratings,
   or return history and BOS does not explicitly contain that assignment rule,
   it is a VARIATION.
6. If a customer asks for a WhatsApp chatbot and BOS does not explicitly
   contain a chatbot capability, it is a VARIATION.
7. Do not invent features or infer support from general software knowledge.
8. For VARIATION, use the COMPLETE original customer requirement as the
   "variation" text. Never output a fragment such as "based on location" or
   "to the manager via email" without its subject and context.
9. For SUPPORTED, use a concise complete sentence in "client_requirement".
10. For PARTIAL, clearly separate the complete supported portion and the
    complete unsupported portion.

Return ONLY JSON:
{
  "classifications": [
    {
      "id": 1,
      "status": "SUPPORTED",
      "bos_feature_ids": ["SM-04"],
      "client_requirement": "complete supported requirement",
      "variation": ""
    }
  ]
}
"""


def _audit_classifications(extracted, classifications):
    print("Stage 2B/3: Auditing BOS classifications...")

    requirements_json = json.dumps(
        extracted["requirements"],
        ensure_ascii=False,
        indent=2,
    )

    classifications_json = json.dumps(
        classifications,
        ensure_ascii=False,
        indent=2,
    )

    response = _call_sarvam(
        BOS_AUDIT_PROMPT,
        (
            "CUSTOMER REQUIREMENTS:\n\n"
            f"{requirements_json}\n\n"
            "CURRENT CLASSIFICATIONS TO AUDIT:\n\n"
            f"{classifications_json}\n\n"
            "COMPLETE BOS SCHEMA:\n\n"
            f"{_bos_schema_text()}"
        ),
        max_tokens=5000,
        temperature=0.0,
    )

    data = _extract_json(response)

    if not isinstance(data, dict):
        raise RuntimeError("BOS audit returned an invalid JSON object.")

    audited = data.get("classifications", [])
    if not isinstance(audited, list):
        raise RuntimeError("BOS audit returned an invalid classifications list.")

    valid_ids = {
        int(item["id"])
        for item in extracted["requirements"]
        if str(item.get("id", "")).isdigit()
    }

    original_by_id = {
        int(item["id"]): str(item.get("requirement", "")).strip()
        for item in extracted["requirements"]
        if str(item.get("id", "")).isdigit()
    }

    result = []
    seen_ids = set()

    for item in audited:
        if not isinstance(item, dict):
            continue

        try:
            requirement_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        if requirement_id not in valid_ids or requirement_id in seen_ids:
            continue

        status = str(item.get("status", "")).strip().upper()
        if status not in {"SUPPORTED", "PARTIAL", "VARIATION"}:
            continue

        bos_feature_ids = item.get("bos_feature_ids", [])
        if not isinstance(bos_feature_ids, list):
            bos_feature_ids = []

        client_requirement = str(item.get("client_requirement", "")).strip()
        variation = str(item.get("variation", "")).strip()
        original = original_by_id[requirement_id]

        # The original extracted requirement is the authoritative wording for
        # a pure variation. This prevents the audit model from returning
        # incomplete fragments such as "based on location and workload".
        if status == "VARIATION":
            client_requirement = ""
            variation = original

        elif status == "SUPPORTED":
            variation = ""
            if not client_requirement:
                client_requirement = original

        elif status == "PARTIAL":
            if not client_requirement and not variation:
                raise RuntimeError(
                    f"BOS audit returned an empty PARTIAL classification for requirement {requirement_id}."
                )
            if variation and len(variation.split()) < 5:
                variation = original

        result.append({
            "id": requirement_id,
            "status": status,
            "bos_feature_ids": [str(value) for value in bos_feature_ids],
            "client_requirement": client_requirement,
            "variation": variation,
        })
        seen_ids.add(requirement_id)

    missing_ids = valid_ids - seen_ids
    if missing_ids:
        raise RuntimeError(
            "BOS audit did not classify every customer requirement. "
            f"Missing requirement IDs: {sorted(missing_ids)}"
        )

    print("BOS classification audit completed.")
    return result


# ============================================================
# BUILD DETERMINISTIC REQUIREMENT SECTIONS
# ============================================================

def _build_requirement_sections(classifications):
    """
    Python, not the proposal-writing model, owns the separation between
    Client Requirements and Variations in Schema.
    """

    client_requirements = []
    variations = []

    for item in classifications:
        supported = str(
            item.get("client_requirement", "")
        ).strip()

        variation = str(
            item.get("variation", "")
        ).strip()

        if supported:
            client_requirements.append(supported)

        if variation:
            variations.append(variation)

    # Remove exact duplicates while preserving order.
    def unique(values):
        result = []
        seen = set()

        for value in values:
            key = re.sub(
                r"\s+",
                " ",
                value.lower()
            ).strip()

            if key in seen:
                continue

            seen.add(key)
            result.append(value)

        return result

    return (
        unique(client_requirements),
        unique(variations)
    )


# ============================================================
# STAGE 3 - WRITE THE PROPOSAL
# ============================================================

PROPOSAL_WRITER_PROMPT = """
You are a senior business proposal writer.

Write a professional English proposal using ONLY the verified information
provided to you.

IMPORTANT ARCHITECTURE RULE:
The Python application has already extracted the genuine customer
requirements and compared them against the BOS schema. The transcript is
intentionally NOT provided to you at this stage.

You must therefore treat the supplied data as the complete source of truth.

VERIFIED DATA RULES:
1. CLIENT REQUIREMENTS contains ONLY BOS-supported customer requirements.
2. Do not add, remove, reinterpret, or invent Client Requirements.
3. VARIATIONS are handled by Python and will be inserted separately.
4. Do NOT mention, describe, list, or repeat any variation in Proposed Solution
   or Scope of Work.
5. Proposed Solution and Scope of Work must be based ONLY on the verified
   BOS-supported Client Requirements.
6. Do not invent requirements, features, integrations, reports, algorithms,
   technology, timeline, pricing, maintenance, or commitments.
7. Do not invent a WhatsApp chatbot or any other feature.
8. Use commercial information exactly as supplied.
9. Use timeline information only when supplied.
10. Write concise, professional business English.

SECTION RESPONSIBILITIES:
- Overview: summarize the verified supported scope at a high level.
- Client Requirements: Python will replace this section with the exact
  verified list. Do not generate detailed alternatives.
- Variations in Schema: Python will replace this section with the exact
  verified variation list. Do not generate this section yourself.
- Proposed Solution: explain how the verified supported requirements can be
  addressed. Do not mention unsupported/variation requirements.
- Scope of Work: describe implementation scope only for the verified
  supported requirements. Do not introduce additional features.
- Timeline: use only the supplied timeline information.
- Pricing: use only the supplied commercial information.
- Business Cost & Efficiency Impact: Python will render this section from a
  separate universal business-impact analysis. Do not generate or invent this
  section yourself.
- Next Steps: keep this generic and professional; do not invent commitments.

Return ONLY these sections in exactly this order:

## Overview
## Client Requirements
## Variations in Schema
## Proposed Solution
## Scope of Work
## Business Cost & Efficiency Impact
## Timeline
## Pricing
## Next Steps

Formatting rules:
- Use one bullet per requirement where appropriate.
- Do not use markdown bold.
- Do not use tables.
- Do not mention BOS, AI, prompts, transcription, internal classification,
  or this instruction.
- Do not repeat the same supported requirement across multiple sections when
  it would become redundant.
- Do not mention any specific variation outside the Variations in Schema
  section.
"""


def _write_proposal(
    client_requirements,
    commercial,
    timeline,
    business_impact
):
    print("Stage 3/3: Writing final proposal...")

    verified_data = {
        "client_requirements": client_requirements,
        "commercial": commercial,
        "timeline": timeline,
        "business_impact": business_impact,
    }

    response = _call_sarvam(
        PROPOSAL_WRITER_PROMPT,
        (
            "VERIFIED REQUIREMENT DATA:\n\n"
            f"{json.dumps(verified_data, ensure_ascii=False, indent=2)}\n\n"
            "Write the proposal now."
        ),
        max_tokens=6000,
        temperature=0.1
    )

    return response



def _replace_section(
    sections,
    target_heading,
    replacement_lines
):
    result = []

    for heading, lines in sections:
        if heading.strip().lower() == target_heading.lower():
            result.append(
                (
                    target_heading,
                    replacement_lines
                )
            )
        else:
            result.append(
                (
                    heading,
                    lines
                )
            )

    return result


def _force_verified_requirement_sections(
    sections,
    client_requirements,
    variations
):
    """
    Final deterministic guard.

    Python owns the two requirement sections. The proposal-writing model
    cannot change their contents.

    If the model omitted either heading, the section is inserted in the
    correct location.
    """

    if not client_requirements:
        client_lines = [
            "No confirmed BOS-supported customer requirements were identified."
        ]
    else:
        client_lines = client_requirements

    if not variations:
        variation_lines = [
            "No schema variations identified from the customer requirements "
            "discussed in this meeting."
        ]
    else:
        variation_lines = variations

    def replace_or_insert(section_list, heading, lines, after_heading=None):
        replaced = False
        result = []

        for current_heading, current_lines in section_list:
            if current_heading.strip().lower() == heading.lower():
                if not replaced:
                    result.append((heading, lines))
                    replaced = True
                continue

            result.append((current_heading, current_lines))

        if replaced:
            return result

        if after_heading is not None:
            inserted = []
            added = False

            for current_heading, current_lines in result:
                inserted.append((current_heading, current_lines))

                if (
                    not added
                    and current_heading.strip().lower()
                    == after_heading.lower()
                ):
                    inserted.append((heading, lines))
                    added = True

            if added:
                return inserted

        result.append((heading, lines))
        return result

    sections = replace_or_insert(
        sections,
        "Client Requirements",
        client_lines,
        after_heading="Overview"
    )

    sections = replace_or_insert(
        sections,
        "Variations in Schema",
        variation_lines,
        after_heading="Client Requirements"
    )

    return sections



def _force_verified_pricing(
    sections,
    commercial
):
    """
    Replace Pricing with only the commercial information extracted from
    the transcript. If no amount/condition was explicitly found, let the
    proposal say that pricing remains to be confirmed.
    """

    pricing_lines = []

    for item in commercial:
        if not isinstance(item, dict):
            continue

        value = str(
            item.get("item", "")
        ).strip()

        if value:
            pricing_lines.append(value)

    if not pricing_lines:
        pricing_lines = [
            "Pricing was not explicitly finalized in the meeting and "
            "will be confirmed in the detailed proposal."
        ]

    return _replace_section(
        sections,
        "Pricing",
        pricing_lines
    )





# ============================================================
# BUSINESS IMPACT SECTION
# ============================================================

def _format_business_impact_lines(analysis):
    """Create compact text lines for the business-impact section."""

    lines = []

    profile = analysis.get("business_profile", {})
    summary = str(profile.get("business_summary", "")).strip()
    if summary:
        lines.append(summary)

    opportunities = analysis.get("opportunities", [])

    if not opportunities:
        lines.append(
            "No measurable business cost or efficiency opportunity could be "
            "reliably established from the meeting data."
        )
        return lines

    for item in opportunities:
        area = str(item.get("area", "")).strip() or "Business process"
        driver = str(item.get("current_cost_driver", "")).strip()
        automation = str(item.get("automation_opportunity", "")).strip()
        metric = str(item.get("metric", "")).strip()
        unit = str(item.get("unit", "")).strip()
        result = item.get("calculated_value")
        result_type = str(item.get("result_type", "")).strip()
        note = str(item.get("calculation_note", "")).strip()

        if result is not None:
            if abs(result - round(result)) < 1e-9:
                result_text = f"{int(round(result)):,}"
            else:
                result_text = f"{result:,.2f}"
            impact = f"Calculated {metric or 'impact'}: {result_text} {unit}".strip()
        elif result_type == "ILLUSTRATIVE_ONLY":
            impact = "Illustrative only; client data is required before calculating."
        else:
            impact = "Calculation pending client data."

        parts = [area]
        if driver:
            parts.append(f"Current driver: {driver}")
        if automation:
            parts.append(f"Automation opportunity: {automation}")
        if metric:
            parts.append(f"Measure: {metric}{(' (' + unit + ')') if unit else ''}")
        parts.append(impact)
        if note and result is None:
            parts.append(note)

        lines.append(" — ".join(parts))

    return lines


def _insert_business_impact_section(sections, analysis):
    """Insert a deterministic placeholder; DOCX rendering turns it into a table."""
    lines = _format_business_impact_lines(analysis)

    # Replace an accidental model-generated section.
    result = []
    found = False
    for heading, existing_lines in sections:
        if heading.strip().lower() == "business cost & efficiency impact":
            if not found:
                result.append(("Business Cost & Efficiency Impact", lines))
                found = True
            continue
        result.append((heading, existing_lines))

    if found:
        return result

    # Prefer placement after Scope of Work.
    result = []
    inserted = False
    for heading, existing_lines in sections:
        result.append((heading, existing_lines))
        if heading.strip().lower() == "scope of work":
            result.append(("Business Cost & Efficiency Impact", lines))
            inserted = True

    if not inserted:
        result.append(("Business Cost & Efficiency Impact", lines))

    return result


# ============================================================
# PARSE PROPOSAL SECTIONS
# ============================================================

def _parse_sections(
    markdown_text: str
):

    """
    Convert Markdown-style proposal sections into:

        [
            ("Overview", [...]),
            ("Client Requirements", [...]),
            ...
        ]
    """

    if markdown_text is None:

        raise RuntimeError(
            "Cannot parse proposal because "
            "Sarvam returned no text."
        )

    if not isinstance(
        markdown_text,
        str
    ):

        markdown_text = str(
            markdown_text
        )

    markdown_text = markdown_text.strip()

    if not markdown_text:

        raise RuntimeError(
            "Cannot parse proposal because "
            "the proposal text is empty."
        )

    sections = []

    current_heading = "Proposal"

    current_lines = []

    for line in markdown_text.splitlines():

        heading_match = re.match(
            r"^\s*#{1,3}\s+(.*)",
            line
        )

        if heading_match:

            if current_lines:

                sections.append(
                    (
                        current_heading,
                        current_lines
                    )
                )

            current_heading = (
                heading_match
                .group(1)
                .strip()
            )

            current_lines = []

        else:

            current_lines.append(
                line
            )

    if current_lines:

        sections.append(
            (
                current_heading,
                current_lines
            )
        )

    if not sections:

        raise RuntimeError(
            "Sarvam returned proposal text, "
            "but no usable sections were found."
        )

    return sections


# ============================================================
# CLEAN MODEL MARKUP
# ============================================================

def _clean_proposal_line(text: str) -> str:
    text = str(text).strip()
    text = re.sub(r"^\s*[-*•]\s*", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*`+|`+\s*$", "", text)
    return text.strip()


def _clean_sections(sections):
    cleaned = []
    for heading, lines in sections:
        clean_heading = _clean_proposal_line(heading)
        clean_lines = []
        for line in lines:
            value = _clean_proposal_line(line)
            if value:
                clean_lines.append(value)
        cleaned.append((clean_heading, clean_lines))
    return cleaned


# ============================================================
# ENSURE BOS VARIATIONS SECTION
# ============================================================

def _ensure_variations_section(sections):
    """
    Ensure the final proposal contains the exact
    "Variations in Schema" section.

    The Sarvam prompt performs the semantic BOS cross-check.
    This helper only protects the document structure if the model
    accidentally omits the heading.
    """

    normalized = []

    for heading, lines in sections:
        if heading.strip().lower() == "variations in schema":
            normalized.append(("Variations in Schema", lines))
        else:
            normalized.append((heading, lines))

    if any(
        heading.strip().lower() == "variations in schema"
        for heading, lines in normalized
    ):
        return normalized

    default_lines = [
        "No schema variations identified from the customer requirements "
        "discussed in this meeting."
    ]

    result = []
    inserted = False

    for heading, lines in normalized:
        result.append((heading, lines))

        if heading.strip().lower() == "client requirements":
            result.append(("Variations in Schema", default_lines))
            inserted = True

    if not inserted:
        result.insert(0, ("Variations in Schema", default_lines))

    return result


# ============================================================
# REMOVE VARIATION DUPLICATES FROM CLIENT REQUIREMENTS
# ============================================================

_VARIATION_STOP_WORDS = {
    "about", "after", "again", "also", "based", "before", "being",
    "between", "could", "customer", "customers", "daily", "details",
    "exact", "from", "have", "into", "must", "only", "previous",
    "provide", "provided", "request", "requests", "required",
    "requirement", "requirements", "should", "specific", "staff",
    "staffs", "that", "their", "these", "this", "using", "with",
    "within", "would", "where", "while", "work", "working",
}

def _meaningful_tokens(text: str):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {
        word
        for word in words
        if len(word) >= 4 and word not in _VARIATION_STOP_WORDS
    }


def _variation_similarity(client_line: str, variation_line: str) -> float:
    client_tokens = _meaningful_tokens(client_line)
    variation_tokens = _meaningful_tokens(variation_line)

    if not client_tokens or not variation_tokens:
        return 0.0

    shared = client_tokens & variation_tokens

    # Jaccard catches paraphrased/expanded versions.
    jaccard = len(shared) / len(client_tokens | variation_tokens)

    # Coverage is important when the variation is a more detailed
    # description of a shorter Client Requirements bullet.
    client_coverage = len(shared) / len(client_tokens)
    variation_coverage = len(shared) / len(variation_tokens)

    sequence = difflib.SequenceMatcher(
        None,
        client_line.lower(),
        variation_line.lower(),
    ).ratio()

    return max(
        jaccard,
        client_coverage * 0.90,
        variation_coverage * 0.85,
        sequence,
    )


def _remove_variation_duplicates(sections):
    """
    Prevent unsupported requirements from being repeated in
    Client Requirements.

    Sarvam performs the semantic BOS classification. This function
    is a final safety layer for obvious duplicate/near-duplicate
    wording that otherwise appears in both sections.
    """

    variations = []
    for heading, lines in sections:
        if heading.strip().lower() == "variations in schema":
            variations.extend(
                line.strip()
                for line in lines
                if line.strip()
            )

    if not variations:
        return sections

    cleaned = []

    for heading, lines in sections:
        if heading.strip().lower() != "client requirements":
            cleaned.append((heading, lines))
            continue

        kept_lines = []

        for client_line in lines:
            duplicate = False

            for variation_line in variations:
                similarity = _variation_similarity(
                    client_line,
                    variation_line,
                )

                # Strong similarity means the same unsupported
                # capability has been repeated.
                if similarity >= 0.78:
                    duplicate = True
                    break

            if not duplicate:
                kept_lines.append(client_line)

        cleaned.append(
            ("Client Requirements", kept_lines)
        )

    return cleaned


# ============================================================
# VERIFY COMMERCIAL SECTION
# ============================================================

def _has_explicit_amount(transcript_text: str) -> bool:
    text = transcript_text.lower()
    patterns = [
        r"\b\d+(?:[.,]\d+)*\s*(?:lakh|lakhs)\b",
        r"₹\s*\d",
        r"\brupees?\s*\d",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _ensure_commercial_section(sections, transcript_text: str):
    """
    Prevent the model from losing an explicitly discussed amount.

    This is intentionally generic: it does not hard-code a project price.
    It only creates a fallback Pricing section when the transcript contains
    an amount and the generated Pricing section failed to preserve it.
    """

    transcript_lower = transcript_text.lower()

    amount_match = re.search(
        r"(?:₹\s*([0-9][0-9,]*(?:\.[0-9]+)?)|\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:lakh|lakhs)\b)",
        transcript_lower
    )

    has_within_lakh = "within one lakh" in transcript_lower
    has_amount = bool(amount_match or has_within_lakh)

    if not has_amount:
        return sections

    pricing_lines = []

    if has_within_lakh:
        pricing_lines.append(
            "The proposed project amount was discussed as approximately within one lakh (₹1,00,000)."
        )
    elif amount_match:
        if amount_match.group(1):
            amount_text = amount_match.group(1)
            pricing_lines.append(
                f"The amount discussed in the meeting was ₹{amount_text}."
            )
        else:
            amount_text = amount_match.group(2)
            pricing_lines.append(
                f"The amount discussed in the meeting was approximately {amount_text} lakh(s)."
            )

    if "server" in transcript_lower and "additional amount" in transcript_lower:
        pricing_lines.append(
            "If LiveAs Technologies provides the server, the server will be an additional cost, as discussed in the meeting."
        )

    if not pricing_lines:
        return sections

    # Check whether the model already preserved a real amount.
    pricing_has_amount = False
    result = []

    for heading, lines in sections:
        if heading.strip().lower() != "pricing":
            result.append((heading, lines))
            continue

        combined = " ".join(lines).lower()
        pricing_has_amount = bool(
            re.search(r"\b\d+(?:[.,]\d+)*\s*(?:lakh|lakhs)\b", combined)
            or re.search(r"₹\s*\d", combined)
            or re.search(r"\brupees?\s*\d", combined)
        )

        if pricing_has_amount:
            result.append(("Pricing", lines))
        else:
            result.append(("Pricing", pricing_lines))

    if not any(h.strip().lower() == "pricing" for h, _ in result):
        result.append(("Pricing", pricing_lines))

    return result




# ============================================================
# LETTERHEAD
# ============================================================



# ============================================================
# ADD PROPOSAL SECTIONS
# ============================================================

def _set_cell_shading(cell, fill):
    from docx.oxml import OxmlElement  # type: ignore[reportMissingImports]
    from docx.oxml.ns import qn  # type: ignore[reportMissingImports]

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_cell_border(cell, **kwargs):
    from docx.oxml import OxmlElement  # type: ignore[reportMissingImports]
    from docx.oxml.ns import qn  # type: ignore[reportMissingImports]

    tc_pr = cell._tc.get_or_add_tcPr()
    tc_borders = tc_pr.first_child_found_in("w:tcBorders")

    if tc_borders is None:
        tc_borders = OxmlElement("w:tcBorders")
        tc_pr.append(tc_borders)

    for edge in ("top", "left", "bottom", "right"):
        if edge not in kwargs:
            continue

        edge_data = kwargs[edge]
        tag = "w:" + edge
        element = tc_borders.find(qn(tag))

        if element is None:
            element = OxmlElement(tag)
            tc_borders.append(element)

        for key in ("val", "sz", "space", "color"):
            if key in edge_data:
                element.set(qn("w:" + key), str(edge_data[key]))


def _configure_document(doc):
    section = doc.sections[0]

    section.top_margin = Inches(0.62)
    section.bottom_margin = Inches(0.62)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    section.header_distance = Inches(0.22)
    section.footer_distance = Inches(0.22)

    styles = doc.styles

    normal = styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10.7)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.08

    heading1 = styles["Heading 1"]
    heading1.font.name = "Aptos"
    heading1.font.size = Pt(13.5)
    heading1.font.bold = True
    heading1.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    heading1.paragraph_format.space_before = Pt(8)
    heading1.paragraph_format.space_after = Pt(3)
    heading1.paragraph_format.keep_with_next = True

    bullet = styles["List Bullet"]
    bullet.font.name = "Aptos"
    bullet.font.size = Pt(10.3)
    bullet.paragraph_format.space_after = Pt(2)
    bullet.paragraph_format.line_spacing = 1.04
    bullet.paragraph_format.left_indent = Inches(0.22)
    bullet.paragraph_format.first_line_indent = Inches(-0.10)

    # Footer with company name and page number.
    footer = section.footer
    footer_p = footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = footer_p.add_run("LiveAs Technologies  •  Page ")
    run.font.name = "Aptos"
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(0x77, 0x77, 0x77)

    from docx.oxml import OxmlElement  # type: ignore[reportMissingImports]
    from docx.oxml.ns import qn  # type: ignore[reportMissingImports]

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")

    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _add_letterhead(doc: Document):
    if COMPANY_LOGO_PATH and os.path.exists(COMPANY_LOGO_PATH):
        logo_paragraph = doc.add_paragraph()
        logo_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo_paragraph.paragraph_format.space_after = Pt(2)

        run = logo_paragraph.add_run()
        run.add_picture(
            COMPANY_LOGO_PATH,
            width=Inches(1.20)
        )

    name_paragraph = doc.add_paragraph()
    name_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_paragraph.paragraph_format.space_after = Pt(1)

    name_run = name_paragraph.add_run(COMPANY_NAME)
    name_run.bold = True
    name_run.font.name = "Aptos"
    name_run.font.size = Pt(16)
    name_run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)

    if COMPANY_TAGLINE:
        tagline = doc.add_paragraph()
        tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
        tagline.paragraph_format.space_after = Pt(1)

        run = tagline.add_run(COMPANY_TAGLINE)
        run.italic = True
        run.font.name = "Aptos"
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    contact_bits = [
        value
        for value in (
            COMPANY_EMAIL,
            COMPANY_PHONE,
            COMPANY_WEBSITE,
        )
        if value
    ]

    if contact_bits:
        contact = doc.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact.paragraph_format.space_after = Pt(5)

        run = contact.add_run("  |  ".join(contact_bits))
        run.font.name = "Aptos"
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    rule = doc.add_paragraph()
    rule.paragraph_format.space_before = Pt(0)
    rule.paragraph_format.space_after = Pt(7)

    p_pr = rule._p.get_or_add_pPr()

    from docx.oxml import OxmlElement  # type: ignore[reportMissingImports]
    from docx.oxml.ns import qn  # type: ignore[reportMissingImports]

    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "10")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "1F3A5F")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _format_number(value):
    if value is None:
        return ""
    try:
        value = float(value)
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value)):,}"
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _build_impact_measurement(item):
    """Build only the Impact / Measurement cell.

    The cell prioritizes meeting-supported calculations. If the meeting does
    not contain enough numbers, it shows a clearly labelled illustrative
    industry example supplied by the universal business-impact analyzer.
    """
    metric = str(item.get("metric", "")).strip()
    unit = str(item.get("unit", "")).strip()
    result_type = str(item.get("result_type", "")).strip().upper()
    result = item.get("calculated_value")
    low = item.get("calculated_low")
    high = item.get("calculated_high")
    example = str(item.get("example_calculation", "")).strip()
    assumption = str(item.get("assumption_note", "")).strip()
    basis = str(item.get("benchmark_basis", "")).strip()

    lines = []

    if result_type == "MEETING_CALCULATED" and result is not None:
        lines.append("Calculated from meeting data")
        value_text = _format_number(result)
        lines.append(f"Result: {value_text}{(' ' + unit) if unit else ''}")

    elif result_type == "ILLUSTRATIVE_ONLY":
        lines.append("Example calculation (illustrative)")
        if example:
            lines.append(example)
        elif low is not None:
            if high is not None and float(high) != float(low):
                lines.append(
                    f"Indicative range: {_format_number(low)}–{_format_number(high)}"
                    f"{(' ' + unit) if unit else ''}"
                )
            else:
                lines.append(
                    f"Indicative result: {_format_number(low)}"
                    f"{(' ' + unit) if unit else ''}"
                )
        lines.append("Industry assumption — validate with client data")

    else:
        # This should be uncommon after the updated analyzer. Keep the wording
        # useful instead of exposing "No data" in the customer proposal.
        lines.append("Illustrative scenario")
        if example:
            lines.append(example)
        else:
            lines.append("Client-specific inputs are required for a quantified estimate")

    if metric:
        lines.append(f"Measure: {metric}")
    if assumption:
        lines.append(f"Assumption: {assumption}")
    elif basis and result_type == "ILLUSTRATIVE_ONLY":
        lines.append(f"Benchmark basis: {basis}")

    return "\n".join(lines)


def _add_business_impact_table(doc: Document, analysis):
    """Render the universal business-impact analysis as a compact table."""
    profile = analysis.get("business_profile", {})
    summary = str(profile.get("business_summary", "")).strip()
    if summary:
        p = doc.add_paragraph(summary)
        p.paragraph_format.space_after = Pt(5)

    opportunities = analysis.get("opportunities", [])
    if not opportunities:
        p = doc.add_paragraph(
            "No business-impact opportunities were identified from the meeting."
        )
        p.paragraph_format.space_after = Pt(4)
        return

    table = doc.add_table(rows=1, cols=4)
    table.autofit = False
    widths = [1.15, 1.65, 2.10, 1.55]
    for idx, width in enumerate(widths):
        table.columns[idx].width = Inches(width)

    headers = [
        "Business Area",
        "Current Driver",
        "Automation Opportunity",
        "Impact / Measurement",
    ]

    for idx, text in enumerate(headers):
        cell = table.rows[0].cells[idx]
        _set_cell_shading(cell, "F3F6F9")
        _set_cell_border(
            cell,
            top={"val": "single", "sz": "4", "color": "D9E1E8"},
            bottom={"val": "single", "sz": "4", "color": "D9E1E8"},
            left={"val": "single", "sz": "4", "color": "D9E1E8"},
            right={"val": "single", "sz": "4", "color": "D9E1E8"},
        )
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Aptos"
        run.font.size = Pt(7.7)
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)

    for item in opportunities:
        row = table.add_row()
        area = str(item.get("area", "")).strip() or "Business process"
        driver = str(item.get("current_cost_driver", "")).strip()
        automation = str(item.get("automation_opportunity", "")).strip()
        impact = _build_impact_measurement(item)

        values = [area, driver, automation, impact]
        for idx, value in enumerate(values):
            cell = row.cells[idx]
            _set_cell_border(
                cell,
                top={"val": "single", "sz": "4", "color": "E3E7EB"},
                bottom={"val": "single", "sz": "4", "color": "E3E7EB"},
                left={"val": "single", "sz": "4", "color": "E3E7EB"},
                right={"val": "single", "sz": "4", "color": "E3E7EB"},
            )
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            for line_index, line in enumerate(str(value).split("\n")):
                if line_index:
                    p.add_run().add_break()
                run = p.add_run(line)
                run.font.name = "Aptos"
                run.font.size = Pt(7.5)
                if idx == 3 and line.startswith("Example calculation"):
                    run.bold = True

    # Do not put "No data" in the matrix. Missing client inputs are still
    # useful, but they belong below the matrix as validation inputs.
    missing = analysis.get("missing_inputs", [])
    if missing:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run("Client data required to replace illustrative assumptions: ")
        run.bold = True
        run.font.size = Pt(8.2)
        labels = []
        for item in missing:
            if isinstance(item, dict):
                label = str(item.get("label", "")).strip()
                if label:
                    labels.append(label)
        if labels:
            r = p.add_run("; ".join(labels))
            r.font.size = Pt(8.2)


def _add_proposal_sections(doc: Document, sections, business_impact=None):
    """Render proposal sections with consistent professional spacing."""

    bullet_sections = {
        "client requirements",
        "variations in schema",
        "scope of work",
        "next steps",
    }

    for heading, lines in sections:
        heading_text = heading.strip()
        paragraph = doc.add_heading(heading_text, level=1)
        paragraph.paragraph_format.keep_with_next = True

        if heading_text.lower() == "variations in schema":
            for run in paragraph.runs:
                run.font.color.rgb = RGBColor(0x8A, 0x4B, 0x08)

        if heading_text.lower() == "business cost & efficiency impact":
            _add_business_impact_table(doc, business_impact or {})
            continue

        for line in lines:
            stripped = _clean_proposal_line(line)
            if not stripped:
                continue

            if heading_text.lower() in bullet_sections:
                p = doc.add_paragraph(stripped, style="List Bullet")
            else:
                p = doc.add_paragraph(stripped)

            p.paragraph_format.keep_together = False
            p.paragraph_format.widow_control = True
            p.paragraph_format.space_after = Pt(2)


def _write_docx(
    english_sections,
    client_name: str,
    output_path: str,
    business_impact=None
):
    doc = Document()

    _configure_document(doc)
    _add_letterhead(doc)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(2)

    title_run = title.add_run(
        f"Proposal for {client_name}"
    )
    title_run.bold = True
    title_run.font.name = "Aptos Display"
    title_run.font.size = Pt(21)
    title_run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(7)

    date_run = subtitle.add_run(
        f"Prepared on {datetime.now().strftime('%d %B %Y')}"
    )
    date_run.italic = True
    date_run.font.name = "Aptos"
    date_run.font.size = Pt(8.5)
    date_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    # Compact metadata strip.
    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    table.columns[0].width = Inches(1.35)
    table.columns[1].width = Inches(5.75)

    metadata = (
        ("DOCUMENT", "Business Proposal"),
        ("CLIENT", client_name),
    )

    for row_index, cell in enumerate(table.rows[0].cells):
        label, value = metadata[row_index]

        _set_cell_shading(cell, "F3F6F9")
        _set_cell_border(
            cell,
            top={"val": "single", "sz": "4", "color": "D9E1E8"},
            bottom={"val": "single", "sz": "4", "color": "D9E1E8"},
            left={"val": "single", "sz": "4", "color": "D9E1E8"},
            right={"val": "single", "sz": "4", "color": "D9E1E8"},
        )

        cell_p = cell.paragraphs[0]
        cell_p.paragraph_format.space_after = Pt(0)

        label_run = cell_p.add_run(label + "  ")
        label_run.bold = True
        label_run.font.name = "Aptos"
        label_run.font.size = Pt(7.5)
        label_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        value_run = cell_p.add_run(value)
        value_run.font.name = "Aptos"
        value_run.font.size = Pt(9)

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(1)

    _add_proposal_sections(
        doc,
        english_sections,
        business_impact=business_impact
    )

    doc.save(output_path)


# ============================================================
# EXPORT PDF
# ============================================================

def _export_pdf(
    docx_path: str,
    pdf_path: str
):

    print("Converting DOCX to PDF...")

    try:

        from importlib import import_module

        convert = import_module("docx2pdf").convert

    except ImportError:

        raise RuntimeError(
            "docx2pdf is not installed.\n"
            "Run:\n"
            "pip install docx2pdf"
        )

    try:

        convert(
            docx_path,
            pdf_path
        )

    except Exception as exc:

        raise RuntimeError(
            "PDF conversion failed.\n"
            f"Reason: {exc}\n\n"
            "Make sure Microsoft Word is installed "
            "and the DOCX file can be opened normally."
        ) from exc

    if not os.path.exists(pdf_path):

        raise RuntimeError(
            "PDF conversion finished, "
            "but the PDF file was not created."
        )

    print(
        f"PDF saved to: {pdf_path}"
    )

    return pdf_path

    # try:

    #     from docx2pdf import convert

    #     convert(
    #         docx_path,
    #         pdf_path
    #     )

    #     return True

    # except Exception as exc:

    #     print(
    #         "Could not export PDF automatically "
    #         f"({exc})."
    #     )

    #     print(
    #         "The DOCX is still available. "
    #         "You can save it as PDF from Microsoft Word."
    #     )

    #     return False




# ============================================================
# MAIN PROPOSAL GENERATION FUNCTION
# ============================================================

def generate_proposal(
    transcript_text: str,
    client_name: str = "Client"
) -> str:

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    if not transcript_text or not transcript_text.strip():
        raise RuntimeError(
            "Transcript is empty. Cannot generate a proposal."
        )

    # --------------------------------------------------------
    # Stage 1
    # --------------------------------------------------------

    extracted = _extract_requirements(
        transcript_text
    )

    # --------------------------------------------------------
    # Stage 2
    # --------------------------------------------------------

    classifications = _match_requirements(
        extracted
    )

    (
        client_requirements,
        variations
    ) = _build_requirement_sections(
        classifications
    )

    print(
        f"Supported requirements: "
        f"{len(client_requirements)}"
    )

    print(
        f"Schema variations: "
        f"{len(variations)}"
    )

    # --------------------------------------------------------
    # Business Impact Analysis
    # --------------------------------------------------------

    business_impact = analyze_business_impact(
        transcript_text,
        verified_requirements=client_requirements + variations
    )

    business_impact_path = os.path.join(
        OUTPUT_DIR,
        "business_impact_"
        f"{re.sub(r'[^A-Za-z0-9_-]+', '_', client_name).strip('_') or 'Client'}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

    save_business_impact_report(
        business_impact,
        business_impact_path
    )

    print(
        f"Business impact analysis saved to: {business_impact_path}"
    )

    # --------------------------------------------------------
    # Stage 3
    # --------------------------------------------------------

    proposal_markdown = _write_proposal(
        client_requirements,
        extracted.get("commercial", []),
        extracted.get("timeline", []),
        business_impact
    )

    print(
        "Final proposal received."
    )

    english_sections = _parse_sections(
        proposal_markdown
    )

    english_sections = _clean_sections(
        english_sections
    )

    english_sections = _ensure_variations_section(
        english_sections
    )

    # The following two sections are controlled by Python.
    english_sections = _force_verified_requirement_sections(
        english_sections,
        client_requirements,
        variations
    )

    english_sections = _force_verified_pricing(
        english_sections,
        extracted.get("commercial", [])
    )

    english_sections = _insert_business_impact_section(
        english_sections,
        business_impact
    )

    # --------------------------------------------------------
    # Create timestamp
    # --------------------------------------------------------

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    # --------------------------------------------------------
    # Make safe client name
    # --------------------------------------------------------

    safe_client = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        client_name
    ).strip("_")

    if not safe_client:
        safe_client = "Client"

    # --------------------------------------------------------
    # DOCX path
    # --------------------------------------------------------

    docx_path = os.path.join(
        OUTPUT_DIR,
        (
            f"Proposal_"
            f"{safe_client}_"
            f"{timestamp}.docx"
        )
    )

    # --------------------------------------------------------
    # Create DOCX
    # --------------------------------------------------------

    print(
        "Creating DOCX..."
    )

    _write_docx(
        english_sections,
        client_name,
        docx_path,
        business_impact=business_impact
    )

    print(
        f"DOCX saved to: {docx_path}"
    )

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    if EXPORT_PDF:

        pdf_path = os.path.join(
            OUTPUT_DIR,
            (
                f"Proposal_"
                f"{safe_client}_"
                f"{timestamp}.pdf"
            )
        )

        print(
            "Exporting PDF..."
        )

        _export_pdf(
            docx_path,
            pdf_path
        )

        print(
            f"PDF saved to: {pdf_path}"
        )

    # --------------------------------------------------------
    # Save transcript
    # --------------------------------------------------------

    transcript_path = os.path.join(
        OUTPUT_DIR,
        (
            f"transcript_"
            f"{safe_client}_"
            f"{timestamp}.txt"
        )
    )

    with open(
        transcript_path,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(
            transcript_text
        )

    print(
        f"Transcript saved to: "
        f"{transcript_path}"
    )

    return docx_path
