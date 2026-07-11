"""
report_engine.py
Core logic for the Radiology Report Assistant.

Two modes:
1. Rule-based (default, zero-cost, works offline) - template-driven structuring
   of whatever findings the user pastes in.
2. LLM-assisted (optional) - if the user supplies a Groq API key, the same
   findings are sent to an LLM for higher-quality drafting/paraphrasing.
   Falls back to rule-based automatically if no key is given or the call fails.

This tool DRAFTS documentation for review by a licensed radiologist/physician.
It does not diagnose and must never be presented to a patient as a final
medical opinion.
"""

import os
import re
import json
from datetime import date
from dataclasses import dataclass, field
from typing import Optional

from icd_mapping import suggest_icd_codes

DISCLAIMER = (
    "This output is an AI-generated DRAFT for documentation support only. "
    "It is NOT a medical diagnosis and has NOT been reviewed by a licensed "
    "radiologist or physician. All content must be verified by a qualified "
    "clinician before any clinical decision is made or the report is finalized."
)

FOLLOWUP_RULES = [
    (r"pulmonary nodule|lung nodule", "Recommend dedicated follow-up CT chest per Fleischner Society guidelines based on nodule size and patient risk factors."),
    (r"pneumothorax", "Recommend immediate clinical correlation; repeat chest imaging after intervention or in 6 hours if small/stable and asymptomatic."),
    (r"fracture", "Recommend orthopedic consultation and clinical correlation; consider follow-up imaging in 2-6 weeks to assess healing if managed conservatively."),
    (r"pulmonary embolism|pe\b", "Recommend urgent clinical correlation and hematology/pulmonology consultation; consider follow-up CT or V/Q scan per treatment response."),
    (r"mass|lesion|malignan", "Recommend multidisciplinary review; consider dedicated contrast-enhanced imaging, biopsy, or oncology referral as clinically indicated."),
    (r"appendicitis", "Recommend urgent surgical consultation."),
    (r"intracranial hemorrhage|subdural|subarachnoid", "Recommend urgent neurosurgical/neurology consultation and repeat imaging per clinical trajectory."),
    (r"infarct|ischemi", "Recommend urgent neurology consultation and correlation with clinical stroke protocol."),
    (r"no acute|unremarkable|within normal limits", "No urgent follow-up imaging indicated based on these findings alone; continue routine clinical care as directed by the treating physician."),
]

PATIENT_FRIENDLY_GLOSSARY = {
    "consolidation": "an area where the lung tissue looks denser than normal, often from infection or fluid",
    "pneumothorax": "a collapsed lung caused by air leaking into the space around the lung",
    "pleural effusion": "extra fluid built up around the lung",
    "atelectasis": "a small area of the lung that hasn't fully expanded",
    "cardiomegaly": "an enlarged heart shadow on the image",
    "opacity": "an area that appears more solid or cloudy than the surrounding tissue",
    "nodule": "a small round spot",
    "mass": "a larger abnormal growth of tissue",
    "fracture": "a broken bone",
    "effusion": "extra fluid collecting in a space in the body",
    "hemorrhage": "bleeding",
    "infarct": "an area of tissue damaged from lost blood supply",
    "hydronephrosis": "swelling of a kidney due to a build-up of urine",
    "calculus": "a stone",
    "lymphadenopathy": "swollen lymph nodes",
    "hepatomegaly": "an enlarged liver",
    "splenomegaly": "an enlarged spleen",
    "degenerative changes": "normal wear-and-tear changes, often related to aging",
    "osteopenia": "lower than normal bone density",
    "ascites": "fluid build-up in the abdomen",
}


@dataclass
class ReportInput:
    patient_age: Optional[str] = ""
    patient_sex: Optional[str] = ""
    modality: str = "X-ray"
    body_region: str = ""
    clinical_history: str = ""
    findings_raw: str = ""
    radiologist_name: Optional[str] = ""


@dataclass
class ReportOutput:
    structured_report: str
    clinical_impression: str
    icd_suggestions: list
    patient_explanation: str
    followup_recommendations: str
    disclaimer: str = DISCLAIMER
    generated_with: str = "rule-based"


def _split_findings_into_lines(findings_raw: str):
    # Split on newlines or sentence-ending periods followed by space/capital
    lines = re.split(r"\n+", findings_raw.strip())
    cleaned = []
    for line in lines:
        line = line.strip(" -•\t")
        if line:
            cleaned.append(line)
    if len(cleaned) <= 1 and findings_raw.strip():
        # try splitting a single paragraph into sentences
        cleaned = [s.strip() for s in re.split(r"(?<=[.;])\s+", findings_raw.strip()) if s.strip()]
    return cleaned


_NEGATION_CUE = re.compile(
    r"\b(no|not|without|negative for|absence of|rules? out|ruled out|denies|"
    r"free of|no evidence of|no signs? of)\b",
    re.IGNORECASE,
)


def _line_is_negated_positive(line: str, marker_match) -> bool:
    """True if a clinically significant term appears but is negated in the same line."""
    window = line[max(0, marker_match.start() - 30):marker_match.start()]
    return bool(_NEGATION_CUE.search(window))


def _build_impression(findings_lines):
    """Pick out clinically significant, non-negated lines to form a terse impression."""
    significant_markers = re.compile(
        r"fracture|mass|nodule|effusion|pneumothorax|consolidation|hemorrhage|"
        r"infarct|embolism|obstruction|calculus|dislocation|lesion|malignan|"
        r"cholecystitis|appendicitis|hydronephrosis|edema|cardiomegaly",
        re.IGNORECASE,
    )
    significant = []
    for l in findings_lines:
        m = significant_markers.search(l)
        if m and not _line_is_negated_positive(l, m):
            significant.append(l)
    normal_marker = re.compile(r"no acute|unremarkable|within normal limits|no evidence of", re.IGNORECASE)

    if significant:
        numbered = [f"{i+1}. {s.rstrip('.')}." for i, s in enumerate(significant)]
        return "\n".join(numbered)
    elif any(normal_marker.search(l) for l in findings_lines):
        return "1. No acute radiographic abnormality identified."
    else:
        # fallback: summarize first 1-2 lines
        summary = " ".join(findings_lines[:2]) if findings_lines else "Findings as detailed above."
        return f"1. {summary.rstrip('.')}."


def _build_patient_explanation(findings_raw: str, impression: str):
    text = findings_raw.lower()
    explained_terms = []
    for term, plain in PATIENT_FRIENDLY_GLOSSARY.items():
        if term in text and term not in explained_terms:
            explained_terms.append((term, plain))

    intro = "Here's a plain-language summary of what your scan showed:\n\n"
    if not explained_terms:
        body = (
            "The report describes findings from your imaging study. Overall, no "
            "specific abnormal terms from our plain-language glossary were detected "
            "in the text, which may mean the findings are subtle, normal, or use "
            "terminology outside this tool's glossary. Please discuss the full "
            "report with your doctor, who can explain exactly what it means for you."
        )
    else:
        bullet_lines = [f"- \"{term.capitalize()}\" means {plain}." for term, plain in explained_terms]
        body = (
            "Your scan mentioned a few medical terms — here's what they mean in "
            "everyday language:\n" + "\n".join(bullet_lines)
        )

    closing = (
        "\n\nThis explanation is meant to help you understand the words in your "
        "report. It is not a diagnosis, and your doctor is the best person to "
        "explain what these findings mean for your specific health and what "
        "happens next."
    )
    return intro + body + closing


def _build_followup(findings_raw: str):
    lines = re.split(r"\n+", findings_raw)
    matched = []
    for pattern, rec in FOLLOWUP_RULES:
        compiled = re.compile(pattern, re.IGNORECASE)
        for line in lines:
            m = compiled.search(line)
            if m and not _line_is_negated_positive(line, m):
                matched.append(rec)
                break
    if not matched:
        matched.append(
            "No specific follow-up trigger identified from the listed findings. "
            "Follow-up timing and next steps should be determined by the "
            "ordering/treating physician based on full clinical context."
        )
    numbered = [f"{i+1}. {m}" for i, m in enumerate(matched)]
    header = (
        "EDUCATIONAL ONLY — NOT MEDICAL ADVICE. These are generic, guideline-"
        "informed suggestions based on keyword matching, not a personalized "
        "clinical recommendation:\n\n"
    )
    return header + "\n".join(numbered)


def generate_report_rule_based(data: ReportInput) -> ReportOutput:
    findings_lines = _split_findings_into_lines(data.findings_raw)
    findings_block = "\n".join(f"- {l}" for l in findings_lines) if findings_lines else "- Not provided."

    header = (
        f"RADIOLOGY REPORT (DRAFT)\n"
        f"Date: {date.today().isoformat()}\n"
        f"Modality: {data.modality}\n"
        f"Body Region: {data.body_region or 'Not specified'}\n"
        f"Patient: {data.patient_age or 'Age N/A'} / {data.patient_sex or 'Sex N/A'}\n"
        f"Reporting Radiologist: {data.radiologist_name or '[Pending sign-off]'}\n"
    )

    clinical_history = f"CLINICAL HISTORY:\n{data.clinical_history or 'Not provided.'}\n"
    technique = f"TECHNIQUE:\nStandard {data.modality} imaging of the {data.body_region or 'specified region'}.\n"
    findings_section = f"FINDINGS:\n{findings_block}\n"
    impression_text = _build_impression(findings_lines)
    impression_section = f"IMPRESSION:\n{impression_text}\n"

    structured_report = "\n".join([
        header, clinical_history, technique, findings_section, impression_section
    ])

    icd_suggestions = suggest_icd_codes(data.findings_raw)
    patient_explanation = _build_patient_explanation(data.findings_raw, impression_text)
    followup = _build_followup(data.findings_raw)

    return ReportOutput(
        structured_report=structured_report.strip(),
        clinical_impression=impression_text,
        icd_suggestions=icd_suggestions,
        patient_explanation=patient_explanation,
        followup_recommendations=followup,
        generated_with="rule-based",
    )


def _call_groq(prompt: str, api_key: str, model: str = "llama-3.3-70b-versatile") -> Optional[str]:
    """Minimal Groq API call. Returns None on any failure so caller can fall back."""
    try:
        import requests
    except ImportError:
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": (
                        "You are a radiology documentation assistant helping a "
                        "clinician draft structured report language from raw "
                        "findings. You never invent findings not present in the "
                        "input. You always write DRAFT content that requires "
                        "physician sign-off. Do not add real patient identifiers."
                    )},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 900,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        return None
    except Exception:
        return None


def generate_report_llm(data: ReportInput, api_key: str) -> ReportOutput:
    """Use Groq LLM to improve phrasing of report/impression/patient explanation.
    ICD suggestions and follow-up still use the deterministic rule engine so
    codes stay auditable and don't rely on the model inventing codes.
    """
    base = generate_report_rule_based(data)

    prompt = f"""Given these raw radiology findings, produce a JSON object with keys:
"structured_report" (a formatted report with CLINICAL HISTORY, TECHNIQUE, FINDINGS, IMPRESSION sections),
"clinical_impression" (a concise numbered impression),
"patient_explanation" (a warm, plain-language explanation a non-medical patient could understand, 2-4 short paragraphs, ending with a reminder to discuss with their doctor).

Modality: {data.modality}
Body region: {data.body_region}
Clinical history: {data.clinical_history}
Raw findings:
{data.findings_raw}

Respond with ONLY the JSON object, no markdown fences, no commentary."""

    raw = _call_groq(prompt, api_key)
    if not raw:
        return base  # graceful fallback

    try:
        cleaned = raw.strip()
        cleaned = re.sub(r"^```json|```$", "", cleaned, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        return ReportOutput(
            structured_report=parsed.get("structured_report", base.structured_report),
            clinical_impression=parsed.get("clinical_impression", base.clinical_impression),
            icd_suggestions=base.icd_suggestions,
            patient_explanation=parsed.get("patient_explanation", base.patient_explanation),
            followup_recommendations=base.followup_recommendations,
            generated_with="groq-llm",
        )
    except Exception:
        return base


def generate_report(data: ReportInput, groq_api_key: Optional[str] = None) -> ReportOutput:
    if groq_api_key:
        return generate_report_llm(data, groq_api_key)
    return generate_report_rule_based(data)
