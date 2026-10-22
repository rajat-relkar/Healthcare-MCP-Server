"""
seed_data.py - Create patients.db and fill it with SYNTHETIC healthcare data.

Run it once before starting the server:
    python seed_data.py

It is safe to run again: it drops and recreates every table, so you always get
a fresh database. All names, dates and lab values are randomly generated -
there is no real patient information in here.

The five tables match the project diagram:
    Patients, Visits, Diagnoses, Medications, Lab_Results
"""

import random
import sqlite3
from datetime import date, timedelta

from db import DB_PATH

SEED = 42            # fixed seed -> the same "random" patients every run
NUM_PATIENTS = 60
TODAY = date.today()

# --------------------------------------------------------------------------
# 1. TABLE DEFINITIONS
# --------------------------------------------------------------------------
# Dates are stored as ISO text ('YYYY-MM-DD'). SQLite has no date type, but ISO
# text sorts and compares correctly, and julianday() understands it.
SCHEMA = """
DROP TABLE IF EXISTS Lab_Results;
DROP TABLE IF EXISTS Medications;
DROP TABLE IF EXISTS Diagnoses;
DROP TABLE IF EXISTS Visits;
DROP TABLE IF EXISTS Patients;

CREATE TABLE Patients (
    patient_id     INTEGER PRIMARY KEY,
    first_name     TEXT NOT NULL,
    last_name      TEXT NOT NULL,
    date_of_birth  TEXT NOT NULL,
    gender         TEXT NOT NULL CHECK (gender IN ('F', 'M')),
    blood_type     TEXT,
    smoker         INTEGER NOT NULL DEFAULT 0          -- 1 = yes, 0 = no
);

CREATE TABLE Visits (
    visit_id        INTEGER PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES Patients(patient_id),
    visit_type      TEXT NOT NULL CHECK (visit_type IN ('Outpatient', 'Emergency', 'Inpatient')),
    admit_date      TEXT NOT NULL,
    discharge_date  TEXT NOT NULL,                     -- same day for most outpatient visits
    department      TEXT,
    reason          TEXT
);

CREATE TABLE Diagnoses (
    diagnosis_id    INTEGER PRIMARY KEY,
    visit_id        INTEGER NOT NULL REFERENCES Visits(visit_id),
    patient_id      INTEGER NOT NULL REFERENCES Patients(patient_id),
    icd10_code      TEXT NOT NULL,
    description     TEXT NOT NULL,
    is_chronic      INTEGER NOT NULL DEFAULT 0,        -- 1 = long-term condition
    diagnosed_date  TEXT NOT NULL
);

CREATE TABLE Medications (
    medication_id   INTEGER PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES Patients(patient_id),
    drug_name       TEXT NOT NULL,
    dosage          TEXT,
    frequency       TEXT,
    start_date      TEXT NOT NULL,
    end_date        TEXT                               -- NULL = patient still takes it
);

CREATE TABLE Lab_Results (
    lab_id          INTEGER PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES Patients(patient_id),
    visit_id        INTEGER NOT NULL REFERENCES Visits(visit_id),
    test_name       TEXT NOT NULL,
    value           REAL NOT NULL,
    unit            TEXT,
    ref_low         REAL,                              -- normal range; a value outside
    ref_high        REAL,                              -- it counts as "abnormal"
    result_date     TEXT NOT NULL
);

CREATE INDEX idx_visits_patient      ON Visits(patient_id);
CREATE INDEX idx_diagnoses_patient   ON Diagnoses(patient_id);
CREATE INDEX idx_medications_patient ON Medications(patient_id);
CREATE INDEX idx_labs_patient        ON Lab_Results(patient_id);
"""

# --------------------------------------------------------------------------
# 2. REFERENCE DATA used by the random generator
# --------------------------------------------------------------------------
FIRST_NAMES = {
    "F": ["Asha", "Maria", "Linda", "Priya", "Sofia", "Emma", "Fatima", "Grace", "Hana", "Olivia"],
    "M": ["Rahul", "James", "Carlos", "Wei", "Omar", "Liam", "Arjun", "Daniel", "Noah", "Ethan"],
}
LAST_NAMES = ["Sharma", "Garcia", "Smith", "Chen", "Khan", "Johnson", "Patel", "Brown",
              "Nguyen", "Silva", "Kumar", "Miller", "Rossi", "Ali", "Taylor"]
BLOOD_TYPES = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]

# Chronic conditions: (ICD-10 code, description, [(drug, dosage, frequency), ...])
CHRONIC = [
    ("E11.9", "Type 2 diabetes mellitus", [("Metformin", "500 mg", "Twice daily"),
                                          ("Glipizide", "5 mg", "Once daily")]),
    ("I10",   "Essential hypertension",   [("Lisinopril", "10 mg", "Once daily"),
                                          ("Amlodipine", "5 mg", "Once daily")]),
    ("E78.5", "Hyperlipidemia",           [("Atorvastatin", "20 mg", "Once daily")]),
    ("J44.9", "COPD",                     [("Tiotropium", "18 mcg", "Once daily")]),
    ("I50.9", "Heart failure",            [("Furosemide", "40 mg", "Once daily"),
                                          ("Carvedilol", "6.25 mg", "Twice daily")]),
    ("N18.3", "Chronic kidney disease, stage 3", [("Losartan", "50 mg", "Once daily")]),
]

# Acute (short-term) conditions: (ICD-10 code, description, short-course drug or None)
ACUTE = [
    ("J18.9", "Pneumonia",                  ("Amoxicillin", "500 mg", "Three times daily")),
    ("N39.0", "Urinary tract infection",    ("Nitrofurantoin", "100 mg", "Twice daily")),
    ("A09",   "Gastroenteritis",            None),
    ("S52.5", "Fracture of forearm",        ("Ibuprofen", "400 mg", "As needed")),
    ("R07.9", "Chest pain, unspecified",    None),
    ("J20.9", "Acute bronchitis",           ("Azithromycin", "250 mg", "Once daily")),
]

# Lab tests: name -> (unit, normal low, normal high, which direction is "bad")
LAB_TESTS = {
    "HbA1c":           ("%",      4.0,  5.6,   "high"),
    "Glucose":         ("mg/dL",  70.0, 99.0,  "high"),
    "LDL Cholesterol": ("mg/dL",  0.0,  100.0, "high"),
    "Creatinine":      ("mg/dL",  0.6,  1.3,   "high"),
    "eGFR":            ("mL/min", 90.0, 120.0, "low"),
    "Potassium":       ("mmol/L", 3.5,  5.1,   "high"),
    "Hemoglobin":      ("g/dL",   12.0, 17.5,  "low"),
}

# A chronic condition makes certain labs far more likely to be abnormal.
LAB_DRIVERS = {
    "E11.9": ["HbA1c", "Glucose"],
    "E78.5": ["LDL Cholesterol"],
    "N18.3": ["Creatinine", "eGFR"],
    "I50.9": ["Potassium", "Hemoglobin"],
}

DEPARTMENTS = {
    "Outpatient": ["Primary Care", "Endocrinology"],
    "Emergency":  ["Emergency"],
    "Inpatient":  ["Internal Medicine", "Cardiology", "Pulmonology"],
}


# --------------------------------------------------------------------------
# 3. HELPERS THAT GENERATE ONE KIND OF ROW
# --------------------------------------------------------------------------
def length_of_stay(rng: random.Random, visit_type: str) -> int:
    """How many days a visit lasts (0 = same-day)."""
    if visit_type == "Inpatient":
        return rng.randint(2, 9)
    if visit_type == "Emergency":
        return rng.randint(0, 1)
    return 0


def lab_value(rng: random.Random, test: str, abnormal: bool) -> float:
    """Make a lab value that is either inside or outside the normal range."""
    _, low, high, bad_direction = LAB_TESTS[test]
    span = high - low
    if not abnormal:
        return round(rng.uniform(low + 0.1 * span, high - 0.1 * span), 2)
    if bad_direction == "high":
        return round(high * rng.uniform(1.1, 1.6), 2)
    return round(low * rng.uniform(0.4, 0.85), 2)


def insert_labs(conn, rng, patient_id, visit_id, visit_date, chronic, how_many):
    """Add lab results for one visit, favouring tests linked to the patient's conditions."""
    candidates = []
    for code, _, _ in chronic:
        candidates += LAB_DRIVERS.get(code, [])
    candidates += rng.sample(list(LAB_TESTS), 2)          # a couple of routine tests
    candidates = list(dict.fromkeys(candidates))          # drop duplicates, keep order

    for test in rng.sample(candidates, min(how_many, len(candidates))):
        unit, low, high, _ = LAB_TESTS[test]
        # Patients with a related chronic condition are abnormal ~70% of the time,
        # everyone else only ~8% of the time.
        linked = any(test in LAB_DRIVERS.get(code, []) for code, _, _ in chronic)
        abnormal = rng.random() < (0.70 if linked else 0.08)
        conn.execute(
            "INSERT INTO Lab_Results (patient_id, visit_id, test_name, value, unit, ref_low, ref_high, result_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (patient_id, visit_id, test, lab_value(rng, test, abnormal), unit, low, high, visit_date.isoformat()),
        )


# --------------------------------------------------------------------------
# 4. BUILD ONE PATIENT (and everything linked to them)
# --------------------------------------------------------------------------
def create_patient(conn: sqlite3.Connection, rng: random.Random) -> None:
    gender = rng.choice(["F", "M"])
    age = rng.randint(18, 90)
    dob = TODAY - timedelta(days=int(age * 365.25) + rng.randint(0, 364))

    cursor = conn.execute(
        "INSERT INTO Patients (first_name, last_name, date_of_birth, gender, blood_type, smoker)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (rng.choice(FIRST_NAMES[gender]), rng.choice(LAST_NAMES), dob.isoformat(),
         gender, rng.choice(BLOOD_TYPES), int(rng.random() < 0.2)),
    )
    patient_id = cursor.lastrowid

    # Older patients tend to have more chronic conditions.
    weights = [1, 3, 4, 3] if age >= 65 else [3, 4, 2, 1] if age >= 45 else [7, 2, 1, 0]
    chronic = rng.sample(CHRONIC, rng.choices([0, 1, 2, 3], weights=weights)[0])

    # --- Plan the visits first, then sort them by date and insert in order ----
    planned = []   # each item: (admit_date, visit_type, days_in_hospital)
    for _ in range(rng.randint(1, 3) + len(chronic)):   # sicker patients visit more often
        admit = TODAY - timedelta(days=rng.randint(10, 730))
        visit_type = rng.choices(["Outpatient", "Emergency", "Inpatient"], weights=[55, 20, 25])[0]
        stay = length_of_stay(rng, visit_type)
        planned.append((admit, visit_type, stay))

        # After an inpatient stay, sometimes the patient comes straight back.
        # This is what creates "readmissions" for the readmission tool to find.
        if visit_type == "Inpatient" and rng.random() < 0.15 + 0.10 * len(chronic):
            back = admit + timedelta(days=stay + rng.randint(3, 28))
            if back <= TODAY - timedelta(days=10):
                return_type = rng.choice(["Emergency", "Inpatient"])
                planned.append((back, return_type, length_of_stay(rng, return_type)))

    planned.sort()
    first_visit_date = planned[0][0]

    for index, (admit, visit_type, stay) in enumerate(planned):
        discharge = admit + timedelta(days=stay)

        # The main diagnosis of this visit: an acute illness, or (for routine
        # outpatient visits) a check-up of one of the patient's chronic conditions.
        if chronic and visit_type == "Outpatient":
            code, description, _ = rng.choice(chronic)
            primary = (code, description, 1)
            short_course = None
        else:
            code, description, short_course = rng.choice(ACUTE)
            primary = (code, description, 0)

        cursor = conn.execute(
            "INSERT INTO Visits (patient_id, visit_type, admit_date, discharge_date, department, reason)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, visit_type, admit.isoformat(), discharge.isoformat(),
             rng.choice(DEPARTMENTS[visit_type]), description),
        )
        visit_id = cursor.lastrowid

        # Diagnoses: the primary one, plus (on the first visit only) every
        # chronic condition, so each one is recorded once from the start.
        diagnoses = {primary[0]: primary}
        if index == 0:
            for code, description, _ in chronic:
                diagnoses.setdefault(code, (code, description, 1))
        for code, description, is_chronic in diagnoses.values():
            conn.execute(
                "INSERT INTO Diagnoses (visit_id, patient_id, icd10_code, description, is_chronic, diagnosed_date)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (visit_id, patient_id, code, description, is_chronic, admit.isoformat()),
            )

        # A short antibiotic / painkiller course for acute illnesses.
        if short_course:
            drug, dosage, frequency = short_course
            end = admit + timedelta(days=stay + 7)
            conn.execute(
                "INSERT INTO Medications (patient_id, drug_name, dosage, frequency, start_date, end_date)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (patient_id, drug, dosage, frequency, admit.isoformat(),
                 end.isoformat() if end <= TODAY else None),
            )

        labs_wanted = 3 if visit_type != "Outpatient" else rng.randint(1, 2)
        insert_labs(conn, rng, patient_id, visit_id, discharge, chronic, labs_wanted)

    # Long-term medications start at the first visit and are normally still active.
    for _, _, meds in chronic:
        for drug, dosage, frequency in meds:
            end = None
            if rng.random() < 0.10:                       # ~10% were stopped at some point
                stopped = first_visit_date + timedelta(days=rng.randint(30, 300))
                end = stopped.isoformat() if stopped < TODAY else None
            conn.execute(
                "INSERT INTO Medications (patient_id, drug_name, dosage, frequency, start_date, end_date)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (patient_id, drug, dosage, frequency, first_visit_date.isoformat(), end),
            )


def main() -> None:
    rng = random.Random(SEED)
    # This is the ONLY place in the project that opens the database for writing.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")   # make SQLite enforce the REFERENCES links
    conn.executescript(SCHEMA)

    with conn:                                  # one transaction: all rows or none
        for _ in range(NUM_PATIENTS):
            create_patient(conn, rng)

    print(f"Created {DB_PATH}")
    for table in ["Patients", "Visits", "Diagnoses", "Medications", "Lab_Results"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<12} {count:>5} rows")
    conn.close()


if __name__ == "__main__":
    main()
