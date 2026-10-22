"""
risk_summary.py - The "Patient Risk Summary" box from the project diagram.

Builds a simple, transparent risk score for one patient. Every factor adds a
few points, and the tool returns the points per factor so the LLM can explain
WHY a patient is rated Low / Moderate / High.

IMPORTANT: this is a teaching example with made-up thresholds. It is NOT a
clinically validated risk model and must not be used for real medical decisions.
"""

from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from db import AGE_SQL, query_one

# Thresholds live here, at the top, so they are easy to find and tune.
LAB_WINDOW_DAYS = 180        # how far back we look for abnormal labs
VISIT_WINDOW_DAYS = 365      # how far back we look for emergency / inpatient visits
MAX_SCORE = 13               # 2 (age) + 4 (chronic) + 2 (meds) + 2 (labs) + 2 (visits) + 1 (smoker)
MODERATE_FROM = 4            # score 0-3 = Low, 4-6 = Moderate, 7+ = High
HIGH_FROM = 7


def _factor(name: str, detail: str, points: int) -> dict[str, Any]:
    return {"factor": name, "detail": detail, "points": points}


def register(mcp) -> None:
    @mcp.tool()
    def get_patient_risk_summary(patient_id: int) -> dict[str, Any]:
        """
        Calculate a risk score and risk level (Low / Moderate / High) for one
        patient from their age, chronic conditions, number of active medications,
        recent abnormal lab results, recent emergency/inpatient visits and smoking
        status. Returns the score broken down by factor so the result can be explained.
        """
        patient = query_one(
            f"SELECT patient_id, first_name, last_name, {AGE_SQL} AS age, smoker "
            "FROM Patients WHERE patient_id = ?",
            (patient_id,),
        )
        if patient is None:
            raise ToolError(f"No patient found with patient_id {patient_id}")

        # Dates are ISO text, so a plain string comparison (>=) orders them correctly.
        lab_cutoff = (date.today() - timedelta(days=LAB_WINDOW_DAYS)).isoformat()
        visit_cutoff = (date.today() - timedelta(days=VISIT_WINDOW_DAYS)).isoformat()

        # Each query returns one row with one number: {"n": 3}
        chronic = query_one(
            "SELECT COUNT(DISTINCT icd10_code) AS n FROM Diagnoses WHERE patient_id = ? AND is_chronic = 1",
            (patient_id,),
        )["n"]
        medications = query_one(
            "SELECT COUNT(*) AS n FROM Medications WHERE patient_id = ? AND end_date IS NULL",
            (patient_id,),
        )["n"]
        abnormal_labs = query_one(
            "SELECT COUNT(*) AS n FROM Lab_Results WHERE patient_id = ? AND result_date >= ? "
            "AND (value < ref_low OR value > ref_high)",
            (patient_id, lab_cutoff),
        )["n"]
        urgent_visits = query_one(
            "SELECT COUNT(*) AS n FROM Visits WHERE patient_id = ? AND admit_date >= ? "
            "AND visit_type IN ('Emergency', 'Inpatient')",
            (patient_id, visit_cutoff),
        )["n"]

        # --- Turn each measurement into points --------------------------------
        age = patient["age"]
        age_points = 2 if age >= 65 else 1 if age >= 50 else 0
        chronic_points = min(chronic, 4)                       # 1 point each, capped at 4
        med_points = 2 if medications >= 5 else 1 if medications >= 3 else 0   # polypharmacy
        lab_points = 2 if abnormal_labs >= 3 else 1 if abnormal_labs >= 1 else 0
        visit_points = 2 if urgent_visits >= 2 else 1 if urgent_visits == 1 else 0
        smoker_points = 1 if patient["smoker"] else 0

        factors = [
            _factor("Age", f"{age} years", age_points),
            _factor("Chronic conditions", f"{chronic} recorded", chronic_points),
            _factor("Active medications", f"{medications} (5+ = polypharmacy)", med_points),
            _factor("Abnormal labs", f"{abnormal_labs} in last {LAB_WINDOW_DAYS} days", lab_points),
            _factor("Emergency/inpatient visits", f"{urgent_visits} in last {VISIT_WINDOW_DAYS} days", visit_points),
            _factor("Smoker", "yes" if patient["smoker"] else "no", smoker_points),
        ]
        score = sum(f["points"] for f in factors)
        level = "High" if score >= HIGH_FROM else "Moderate" if score >= MODERATE_FROM else "Low"

        return {
            "patient_id": patient["patient_id"],
            "name": f"{patient['first_name']} {patient['last_name']}",
            "risk_score": score,
            "max_score": MAX_SCORE,
            "risk_level": level,
            "factors": factors,
            "disclaimer": "Illustrative rule-based score for a demo project; not a clinical tool.",
        }
