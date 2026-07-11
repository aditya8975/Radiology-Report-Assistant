"""
icd_mapping.py
Keyword-based ICD-10 suggestion engine for radiology findings.

NOTE: This is a heuristic keyword matcher, not a certified coding tool.
Real coding requires a licensed medical coder / radiologist sign-off.
"""

import re

# Each entry: keyword/phrase (lowercase, regex-safe) -> (ICD-10 code, human description)
# Ordered roughly by specificity; longer/more specific phrases are checked first.
ICD_RULES = [
    # Chest / thoracic
    (r"pneumothorax", "J93.9", "Pneumothorax, unspecified"),
    (r"tension pneumothorax", "J93.0", "Spontaneous tension pneumothorax"),
    (r"pleural effusion", "J91.8", "Pleural effusion in other conditions classified elsewhere"),
    (r"consolidation|pneumonia|airspace opacity|airspace disease", "J18.9", "Pneumonia, unspecified organism"),
    (r"atelectasis", "J98.11", "Atelectasis"),
    (r"cardiomegaly|enlarged cardiac silhouette", "I51.7", "Cardiomegaly"),
    (r"pulmonary edema", "J81.1", "Chronic pulmonary edema"),
    (r"pulmonary nodule|lung nodule", "R91.1", "Solitary pulmonary nodule"),
    (r"pulmonary mass|lung mass", "R91.8", "Other nonspecific abnormal finding of lung field"),
    (r"interstitial (lung )?disease|fibrosis", "J84.10", "Pulmonary fibrosis, unspecified"),
    (r"copd|emphysema|hyperinflation", "J44.9", "Chronic obstructive pulmonary disease, unspecified"),
    (r"pulmonary embolism|pe\b", "I26.99", "Other pulmonary embolism without acute cor pulmonale"),
    (r"rib fracture", "S22.9", "Fracture of rib(s), unspecified"),

    # Fractures / MSK
    (r"fracture.*(femur|femoral)", "S72.90", "Fracture of femur, unspecified"),
    (r"fracture.*(tibia|fibula)", "S82.90", "Fracture of lower leg, unspecified"),
    (r"fracture.*(humerus|humeral)", "S42.90", "Fracture of humerus, unspecified"),
    (r"fracture.*(radius|ulna)", "S52.90", "Fracture of forearm, unspecified"),
    (r"fracture.*(wrist|carpal)", "S62.90", "Fracture at wrist and hand level, unspecified"),
    (r"fracture.*(ankle)", "S82.9", "Unspecified fracture of lower leg"),
    (r"fracture.*(hip)", "S72.00", "Fracture of unspecified part of neck of femur"),
    (r"fracture.*(spine|vertebra|vertebral)", "S22.089", "Other fracture of vertebra, unspecified level"),
    (r"fracture.*(skull|cranial)", "S02.91", "Unspecified fracture of skull"),
    (r"compression fracture", "M48.50", "Collapsed vertebra, unspecified"),
    (r"dislocation", "S43.006", "Unspecified dislocation of unspecified shoulder joint"),
    (r"\bfracture\b", "T14.8", "Other injury of unspecified body region (fracture, site unspecified)"),

    # Abdomen
    (r"appendicitis", "K35.80", "Unspecified acute appendicitis"),
    (r"cholelithiasis|gallstone", "K80.20", "Calculus of gallbladder without cholecystitis"),
    (r"cholecystitis", "K81.9", "Cholecystitis, unspecified"),
    (r"bowel obstruction|small bowel obstruction", "K56.60", "Unspecified intestinal obstruction"),
    (r"diverticulitis", "K57.92", "Diverticulitis of intestine, unspecified"),
    (r"hepatomegaly", "R16.0", "Hepatomegaly, not elsewhere classified"),
    (r"splenomegaly", "R16.1", "Splenomegaly, not elsewhere classified"),
    (r"renal calculus|kidney stone|nephrolithiasis", "N20.0", "Calculus of kidney"),
    (r"hydronephrosis", "N13.30", "Unspecified hydronephrosis"),
    (r"free fluid|ascites", "R18.8", "Other ascites"),
    (r"free air|pneumoperitoneum", "R19.11", "Free air under diaphragm"),
    (r"liver lesion|hepatic lesion|hepatic mass", "R93.2", "Abnormal findings on diagnostic imaging of liver and biliary tract"),
    (r"renal mass|renal lesion|kidney mass", "R93.4", "Abnormal findings on diagnostic imaging of urinary organs"),
    (r"adnexal mass|ovarian mass|ovarian cyst", "N83.20", "Unspecified ovarian cyst"),

    # Neuro / head
    (r"intracranial hemorrhage|intracerebral hemorrhage", "I61.9", "Nontraumatic intracerebral hemorrhage, unspecified"),
    (r"subdural hematoma", "S06.5X0A", "Traumatic subdural hemorrhage without loss of consciousness, initial encounter"),
    (r"subarachnoid hemorrhage", "I60.9", "Nontraumatic subarachnoid hemorrhage, unspecified"),
    (r"midline shift|mass effect", "R90.0", "Intracranial space-occupying lesion on imaging"),
    (r"infarct|ischemi", "I63.9", "Cerebral infarction, unspecified"),
    (r"hydrocephalus", "G91.9", "Hydrocephalus, unspecified"),
    (r"sinusitis", "J32.9", "Chronic sinusitis, unspecified"),

    # General / catch-alls
    (r"degenerative (change|disease)|osteoarthritis|spondylosis", "M19.90", "Osteoarthritis, unspecified site"),
    (r"osteopenia|osteoporosis", "M81.0", "Age-related osteoporosis without current pathological fracture"),
    (r"calcification", "R93.8", "Abnormal findings on diagnostic imaging of other specified body structures"),
    (r"foreign body", "T18.9", "Foreign body in alimentary tract, unspecified"),
    (r"lymphadenopathy", "R59.9", "Enlarged lymph nodes, unspecified"),
    (r"no acute (finding|abnormality|process)|unremarkable|within normal limits", "Z00.00", "Encounter for general adult medical examination without abnormal findings"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), code, desc) for pat, code, desc in ICD_RULES]

_NEGATION_CUE = re.compile(
    r"\b(no|not|without|negative for|absence of|rules? out|ruled out|denies|"
    r"free of|no evidence of|no signs? of)\b",
    re.IGNORECASE,
)


def _is_negated(line: str, match_start: int) -> bool:
    """Check for a negation cue word within ~30 chars before the matched phrase,
    on the same line/clause (split on '.', ';', ',')."""
    window_start = max(0, match_start - 30)
    window = line[window_start:match_start]
    for sep in [".", ";"]:
        idx = window.rfind(sep)
        if idx != -1:
            window = window[idx + 1:]
    return bool(_NEGATION_CUE.search(window))


def suggest_icd_codes(findings_text: str):
    """Return a de-duplicated list of (code, description, matched_phrase) tuples.
    Skips matches that are clearly negated (e.g. 'no pneumothorax')."""
    results = []
    seen_codes = set()
    lines = re.split(r"\n+", findings_text)
    for pattern, code, desc in _COMPILED:
        found = None
        for line in lines:
            match = pattern.search(line)
            if match and not _is_negated(line, match.start()):
                found = match
                break
        if found and code not in seen_codes:
            results.append({
                "code": code,
                "description": desc,
                "matched_text": found.group(0),
            })
            seen_codes.add(code)
    if not results:
        results.append({
            "code": "R69",
            "description": "Illness, unspecified (findings do not match a known pattern)",
            "matched_text": "N/A",
        })
    return results
