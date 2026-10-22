"""
query_tool.py - The "Query Patient Details" box from the project diagram.

Three tools:
  * search_patients        - find a patient_id from a name
  * query_patient_details  - everything we know about one patient
  * run_sql_query          - flexible read-only SQL for questions the other
                             tools don't cover
"""

import re
import sqlite3
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from db import AGE_SQL, query, query_one

# Never send more than this many rows back to the LLM. Big results waste its
# context window and can bury the answer.
MAX_ROWS = 100

# A query is only accepted if it starts with SELECT or WITH (a common-table
# expression). This is a friendly early check with a clear error message; the
# REAL protection is that db.py opens the database read-only.
_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def register(mcp) -> None:
    @mcp.tool()
    def search_patients(name: str, limit: int = 10) -> dict[str, Any]:
        """
        Find patients whose first or last name contains the given text
        (case-insensitive). Returns patient_id, name, date of birth and gender.
        Use this to look up a patient_id before calling the other patient tools.
        """
        pattern = f"%{name.strip()}%"   # % is the SQL wildcard: "%sha%" matches "Asha" and "Sharma"
        matches = query(
            """
            SELECT patient_id, first_name, last_name, date_of_birth, gender
            FROM Patients
            WHERE first_name LIKE ? OR last_name LIKE ? OR first_name || ' ' || last_name LIKE ?
            ORDER BY last_name, first_name
            LIMIT ?
            """,
            (pattern, pattern, pattern, max(1, min(limit, 50))),
        )
        # Return an object rather than a bare list: an empty list would reach
        # the LLM as "nothing at all", while {"count": 0} clearly says "no match".
        return {"count": len(matches), "patients": matches}

    @mcp.tool()
    def query_patient_details(patient_id: int) -> dict[str, Any]:
        """
        Get the full record of one patient: demographics, most recent visits,
        diagnoses, currently active medications and the latest lab results
        (each flagged LOW / HIGH / NORMAL against its reference range).
        """
        patient = query_one(
            f"SELECT patient_id, first_name, last_name, date_of_birth, {AGE_SQL} AS age, "
            "gender, blood_type, smoker FROM Patients WHERE patient_id = ?",
            (patient_id,),
        )
        if patient is None:
            # ToolError = "a failure I anticipated". The server replies with an
            # MCP error result carrying this message, so the LLM learns the call
            # failed and why. (Any OTHER exception type is hidden from the client
            # as just "Error executing tool ..." and only written to the server log.)
            raise ToolError(f"No patient found with patient_id {patient_id}")

        return {
            "patient": patient,
            "recent_visits": query(
                "SELECT visit_id, visit_type, admit_date, discharge_date, department, reason "
                "FROM Visits WHERE patient_id = ? ORDER BY admit_date DESC LIMIT 10",
                (patient_id,),
            ),
            # The same diagnosis can be recorded on many visits, so group them.
            "diagnoses": query(
                "SELECT icd10_code, description, is_chronic, MIN(diagnosed_date) AS first_diagnosed, "
                "COUNT(*) AS times_recorded FROM Diagnoses WHERE patient_id = ? "
                "GROUP BY icd10_code, description, is_chronic ORDER BY is_chronic DESC, first_diagnosed",
                (patient_id,),
            ),
            # end_date IS NULL means the patient is still taking the drug.
            "active_medications": query(
                "SELECT drug_name, dosage, frequency, start_date FROM Medications "
                "WHERE patient_id = ? AND end_date IS NULL ORDER BY drug_name",
                (patient_id,),
            ),
            "recent_lab_results": query(
                """
                SELECT result_date, test_name, value, unit, ref_low, ref_high,
                       CASE WHEN value < ref_low THEN 'LOW'
                            WHEN value > ref_high THEN 'HIGH'
                            ELSE 'NORMAL' END AS flag
                FROM Lab_Results WHERE patient_id = ? ORDER BY result_date DESC LIMIT 10
                """,
                (patient_id,),
            ),
        }

    @mcp.tool()
    def run_sql_query(sql: str) -> dict[str, Any]:
        """
        Run a read-only SQL SELECT query against the patient database and return
        the rows. Use this for custom questions such as counts or filters across
        many patients. Call get_database_schema first so you know the table and
        column names. Dates are stored as 'YYYY-MM-DD' text. At most 100 rows
        are returned.
        """
        if not _SELECT_ONLY.match(sql):
            raise ToolError("Only SELECT queries are allowed (the query must start with SELECT or WITH).")

        try:
            # Ask for one row more than we will return, so we can tell the LLM
            # whether the result was cut off.
            rows = query(sql, limit=MAX_ROWS + 1)
        except sqlite3.Error as error:
            # Typos in table/column names end up here. Passing the message back
            # lets the LLM fix its query and try again.
            raise ToolError(f"SQL error: {error}") from error

        return {
            "row_count": min(len(rows), MAX_ROWS),
            "truncated": len(rows) > MAX_ROWS,
            "rows": rows[:MAX_ROWS],
        }
