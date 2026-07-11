"""
app.py — Radiology Report Assistant
Streamlit front-end. Run with: streamlit run app.py
"""

import streamlit as st
from datetime import date
from io import BytesIO

from report_engine import ReportInput, generate_report, DISCLAIMER

st.set_page_config(page_title="Radiology Report Assistant", page_icon="🥈", layout="wide")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    st.caption("Works fully offline with the built-in rule engine. Optionally add a free Groq API key for smoother LLM-drafted language.")
    groq_key = st.text_input("Groq API key (optional)", type="password", help="Get a free key at console.groq.com. Leave blank to use the offline rule-based engine.")
    st.divider()
    st.header("🧾 Case Details")
    modality = st.selectbox("Modality", ["X-ray", "CT", "MRI", "Ultrasound"])
    body_region = st.text_input("Body region", placeholder="e.g. Chest, Abdomen, Left wrist")
    patient_age = st.text_input("Patient age", placeholder="e.g. 54Y")
    patient_sex = st.selectbox("Patient sex", ["", "Male", "Female", "Other/Unspecified"])
    radiologist_name = st.text_input("Reporting radiologist (optional)", placeholder="Dr. ...")
    st.divider()
    st.warning(
        "⚠️ Educational / documentation-support tool only. Not a diagnostic "
        "device. All output requires review and sign-off by a licensed "
        "radiologist or physician before clinical use.",
        icon="⚠️",
    )

st.title("🥈 Radiology Report Assistant")
st.caption("Paste raw findings → get a structured draft report, impression, ICD-10 suggestions, a patient-friendly explanation, and follow-up notes.")

clinical_history = st.text_area(
    "Clinical history / reason for exam",
    placeholder="e.g. 62-year-old male with acute shortness of breath and cough.",
    height=80,
)

findings_raw = st.text_area(
    "Raw findings",
    placeholder=(
        "Paste dictated or bullet-point findings here, e.g.:\n"
        "- Right lower lobe consolidation with air bronchograms.\n"
        "- Small right pleural effusion.\n"
        "- No pneumothorax.\n"
        "- Cardiac silhouette within normal limits."
    ),
    height=220,
)

generate_clicked = st.button("Generate Report", type="primary", use_container_width=True)

if generate_clicked:
    if not findings_raw.strip():
        st.error("Please enter at least some findings before generating a report.")
    else:
        data = ReportInput(
            patient_age=patient_age,
            patient_sex=patient_sex,
            modality=modality,
            body_region=body_region,
            clinical_history=clinical_history,
            findings_raw=findings_raw,
            radiologist_name=radiologist_name,
        )
        with st.spinner("Generating draft..."):
            result = generate_report(data, groq_api_key=groq_key or None)

        st.success(f"Draft generated ({result.generated_with}).")
        st.error(DISCLAIMER, icon="🚨")

        tabs = st.tabs([
            "📄 Structured Report",
            "🩺 Clinical Impression",
            "🔢 ICD-10 Suggestions",
            "🗣️ Patient-Friendly Explanation",
            "📅 Follow-up Recommendations",
        ])

        with tabs[0]:
            st.code(result.structured_report, language=None)
            st.download_button(
                "Download report (.txt)",
                result.structured_report,
                file_name=f"radiology_report_{date.today().isoformat()}.txt",
            )

        with tabs[1]:
            st.markdown(result.clinical_impression.replace("\n", "  \n"))

        with tabs[2]:
            st.caption("Heuristic keyword-based suggestions. Must be verified by a certified medical coder — not a substitute for professional coding review.")
            for item in result.icd_suggestions:
                st.markdown(f"**{item['code']}** — {item['description']}  \n*matched: \"{item['matched_text']}\"*")

        with tabs[3]:
            st.markdown(result.patient_explanation.replace("\n", "  \n"))

        with tabs[4]:
            st.markdown(result.followup_recommendations.replace("\n", "  \n"))

        st.divider()
        full_text = (
            result.structured_report
            + "\n\n=== ICD-10 SUGGESTIONS ===\n"
            + "\n".join(f"{i['code']} - {i['description']}" for i in result.icd_suggestions)
            + "\n\n=== PATIENT-FRIENDLY EXPLANATION ===\n" + result.patient_explanation
            + "\n\n=== FOLLOW-UP RECOMMENDATIONS (EDUCATIONAL ONLY) ===\n" + result.followup_recommendations
            + "\n\n=== DISCLAIMER ===\n" + DISCLAIMER
        )
        st.download_button(
            "⬇️ Download full report package (.txt)",
            full_text,
            file_name=f"radiology_full_package_{date.today().isoformat()}.txt",
            use_container_width=True,
        )
else:
    st.info("Enter clinical history and findings on the left/above, then click **Generate Report**.")
