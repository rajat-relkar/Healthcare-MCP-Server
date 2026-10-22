"""
readmission.py - Finds hospital readmissions.

Definition used here (a common simplification of the "30-day readmission"
measure hospitals report):
    A READMISSION is an Emergency or Inpatient visit that starts within N days
    (default 30) after the patient was discharged from an Inpatient stay.
The earlier stay is called the "index stay".
"""

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from db import query

MAX_EVENTS = 50   # cap on how many readmission events we list in the response


def register(mcp) -> None:
    @mcp.tool()
    def get_readmission_analysis(patient_id: int | None = None, window_days: int = 30) -> dict[str, Any]:
        """
        Find hospital readmissions: Emergency or Inpatient visits that happen
        within `window_days` (default 30) after discharge from an Inpatient stay.
        Pass a patient_id to analyse one patient, or leave it out to get the
        readmission rate across all patients.
        """
        if not 1 <= window_days <= 365:
            raise ToolError("window_days must be between 1 and 365")

        # Optional filter: only add the patient condition when a patient was given.
        patient_filter = "AND idx.patient_id = ?" if patient_id is not None else ""
        params: tuple[Any, ...] = (window_days,) + ((patient_id,) if patient_id is not None else ())

        # SELF-JOIN: the Visits table is joined to itself.
        #   idx = the earlier Inpatient stay ("index stay")
        #   nxt = a later visit by the SAME patient
        # julianday() converts a date to a number, so subtracting two dates
        # gives the number of days between them.
        events = query(
            f"""
            SELECT idx.patient_id,
                   p.first_name || ' ' || p.last_name AS patient_name,
                   idx.visit_id        AS index_visit_id,
                   idx.discharge_date  AS index_discharge_date,
                   nxt.visit_id        AS readmission_visit_id,
                   nxt.visit_type      AS readmission_type,
                   nxt.admit_date      AS readmission_date,
                   CAST(julianday(nxt.admit_date) - julianday(idx.discharge_date) AS INTEGER) AS days_after_discharge
            FROM Visits idx
            JOIN Visits nxt
              ON nxt.patient_id = idx.patient_id
             AND nxt.visit_id  != idx.visit_id          -- a visit can't be its own readmission
             AND nxt.visit_type IN ('Emergency', 'Inpatient')
             AND julianday(nxt.admit_date) - julianday(idx.discharge_date) BETWEEN 0 AND ?
            JOIN Patients p ON p.patient_id = idx.patient_id
            WHERE idx.visit_type = 'Inpatient'
            {patient_filter}
            ORDER BY idx.discharge_date DESC
            """,
            params,
        )

        # How many inpatient stays were there in total? (the denominator of the rate)
        total_filter = "AND patient_id = ?" if patient_id is not None else ""
        total_stays = query(
            f"SELECT COUNT(*) AS n FROM Visits WHERE visit_type = 'Inpatient' {total_filter}",
            params[1:],   # params[0] is window_days, which this query doesn't use
        )[0]["n"]

        # One index stay can be followed by several returns, so count DISTINCT stays.
        readmitted_stays = len({event["index_visit_id"] for event in events})

        return {
            "window_days": window_days,
            "scope": f"patient {patient_id}" if patient_id is not None else "all patients",
            "inpatient_stays": total_stays,
            "stays_followed_by_readmission": readmitted_stays,
            "readmission_rate_percent": round(100 * readmitted_stays / total_stays, 1) if total_stays else 0.0,
            "events": events[:MAX_EVENTS],
            "events_truncated": len(events) > MAX_EVENTS,
        }
