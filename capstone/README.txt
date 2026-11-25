CareLedger (MySQL) - Intelligent Healthcare & Patient Diagnostic System
=======================================================================

SETUP (no Docker needed)

1. Create the database (MySQL 8.0.19 or newer). This one file builds the schema
   AND loads the Kaggle data:
     mysql -u root -p < healthcare_mysql.sql

2. Install the Python packages:
     pip install -r requirements.txt

3. Set your MySQL login and start the app:
     Windows (cmd):         set DB_USER=root & set DB_PASSWORD=yourpassword & uvicorn app:app
     Windows (PowerShell):  $env:DB_USER="root"; $env:DB_PASSWORD="yourpassword"; uvicorn app:app
     Mac / Linux:           DB_USER=root DB_PASSWORD=yourpassword uvicorn app:app

4. Open http://localhost:8000


WHERE THE KAGGLE DATA IS USED
-----------------------------
Dataset: "Disease Symptom Prediction" (itachi9604) on Kaggle
  https://www.kaggle.com/datasets/itachi9604/disease-symptom-description-dataset

  data/kaggle/dataset.csv              4,920 patient records (disease + its symptoms)
  data/kaggle/Symptom-severity.csv     severity 1-7 for each of the 131 symptoms
  data/kaggle/symptom_Description.csv  description of each of the 41 diseases
  data/kaggle/symptom_precaution.csv   4 precautions per disease

Flow:
  1. kaggle_import.py cleans the CSVs (typos, spaces, duplicates) and writes them
     into the KAGGLE DATA block of healthcare_mysql.sql.
  2. MySQL tables filled from Kaggle:
       symptoms              131 rows (name, body system, severity_weight)
       diseases              41 rows  (ICD-10, severity, specialist, description)
       disease_precautions   163 rows
       kaggle_cases          4,920 raw records
       kaggle_case_symptoms  36,648 rows (which symptoms each record has)
  3. The AI knowledge base (disease_symptoms.weight) is LEARNED INSIDE MySQL with one
     INSERT ... SELECT:
       weight = (records of the disease showing the symptom / all its records)
                * (0.5 + Kaggle severity / 14)
  4. Stored procedure match_diseases() scores every disease for a patient's check:
       score = disease coverage x patient coverage
     and the API saves the top 5 in ai_predictions.
  5. Accuracy on the Kaggle records is stored in model_evaluations:
       Hold-out (20% of unique symptom combinations)        top-1 100%,  top-3 100%
       Stress test (all 4,920 records, half the symptoms)    top-1 96.4%, top-3 100%

In the app: "Symptom checker" uses all 131 Kaggle symptoms and shows the Kaggle
description and precautions for the top match. The "Kaggle data" page shows the
dataset stats, accuracy and what the model learned, queried live from MySQL.

You only need to run  python kaggle_import.py  if you change the CSV files.

Handy queries for the viva:
  SELECT * FROM model_evaluations;
  SELECT * FROM v_kaggle_disease_profile WHERE disease = 'Dengue' ORDER BY weight DESC;
  CALL match_diseases(1, 5);


DOCTOR DATA
-----------
  doctors                 22 doctors (gender, languages, room, bio, joined date, fee, experience)
  doctor_specializations  every one of the 10 specializations has at least 2 doctors
                          (the diseases table recommends a specialization, so the checker can always suggest a doctor)
  doctor_schedules        52 weekly sessions, each with its own slot length (20 or 30 min)
  doctor_leaves           6 leave periods (past and upcoming)
  v_doctor_directory      doctor + specialties + languages + room + fee
  v_doctor_workload       completed visits, upcoming visits and patients per doctor

Supporting sample data so the doctors have a real history: 6 patients, 12 medicines, 9 lab tests,
15 appointments (completed, cancelled, no-show, scheduled), 8 consultations, prescriptions and lab results.
Every sample appointment lies inside its doctor's schedule and outside their leave.

API additions (app.py)
  GET  /api/doctors/{id}/slots?day=YYYY-MM-DD   free / taken slots from the schedule, minus leave and bookings
  POST /api/appointments                         now also rejects times outside working hours and days on leave
                                                 (double bookings are still rejected by the MySQL trigger)
  GET  /api/meta                                 doctors now carry bio, languages, room, leaves and workload
Schedules are clinic time (IST by default); appointments are stored in UTC. For another time zone set
CLINIC_TZ_OFFSET_MIN (minutes from UTC, default 330).

UI: the Doctors page has search, profile details, workload and leave notices. Booking now picks a free
time slot from the doctor's real availability instead of a free-text date and time.


UI
--
frontend/index.html   whole app (plain HTML/CSS/JS, no build step)
frontend/globe.js     3D knowledge globe (three.js): 131 symptoms on the outer shell,
                      41 diseases in the core, lines = learned links. Selected symptoms
                      and matched diseases light up in the symptom checker.
frontend/vendor/      three.js copy, so the 3D view works without internet
