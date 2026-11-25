"""Kaggle -> MySQL importer for CareLedger.

Dataset : "Disease Symptom Prediction" by Pranay Patil (itachi9604) on Kaggle
          https://www.kaggle.com/datasets/itachi9604/disease-symptom-description-dataset
Files   : data/kaggle/dataset.csv              4,920 patient records (disease + up to 17 symptoms)
          data/kaggle/Symptom-severity.csv     severity weight 1-7 for each of 131 symptoms
          data/kaggle/symptom_Description.csv  a description for each of the 41 diseases
          data/kaggle/symptom_precaution.csv   4 precautions for each disease

What it does
  1. Cleans the CSVs (typos, stray spaces, duplicate symptoms).
  2. Writes them as INSERT statements into the KAGGLE section of healthcare_mysql.sql:
       symptoms, diseases, disease_precautions, kaggle_cases, kaggle_case_symptoms
     MySQL itself then derives the AI knowledge base (disease_symptoms.weight) from the raw cases.
  3. Measures how accurate the symptom matcher is on this data and stores it in model_evaluations.

Run   : python kaggle_import.py         (only needed if you change the CSVs; the SQL file is already built)
"""
import collections
import csv
import random
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data" / "kaggle"
SQL_FILE = HERE / "healthcare_mysql.sql"
START, END = "-- >>> KAGGLE DATA START", "-- <<< KAGGLE DATA END"

# Specialization ids match the INSERT INTO specializations block in healthcare_mysql.sql
GEN, CARD, PULM, ENDO, NEURO, DERM, GASTRO, INFECT, ORTHO, URO = range(1, 11)

# Kaggle disease label -> (clean name, ICD-10, severity 1-5, specialization)
DISEASES = {
    "fungalinfection": ("Fungal Infection", "B49", 1, DERM),
    "allergy": ("Allergy", "T78.4", 1, GEN),
    "gerd": ("GERD (Acid Reflux)", "K21.9", 2, GASTRO),
    "chroniccholestasis": ("Chronic Cholestasis", "K83.1", 3, GASTRO),
    "drugreaction": ("Drug Reaction", "T88.7", 3, GEN),
    "pepticulcerdiseae": ("Peptic Ulcer Disease", "K27.9", 3, GASTRO),
    "aids": ("AIDS", "B20", 5, INFECT),
    "diabetes": ("Diabetes", "E11", 3, ENDO),
    "gastroenteritis": ("Gastroenteritis", "A09", 2, GASTRO),
    "bronchialasthma": ("Bronchial Asthma", "J45", 3, PULM),
    "hypertension": ("Hypertension", "I10", 3, CARD),
    "migraine": ("Migraine", "G43.9", 2, NEURO),
    "cervicalspondylosis": ("Cervical Spondylosis", "M47.812", 2, ORTHO),
    "paralysisbrainhemorrhage": ("Paralysis (Brain Hemorrhage)", "I61.9", 5, NEURO),
    "jaundice": ("Jaundice", "R17", 3, GASTRO),
    "malaria": ("Malaria", "B54", 4, INFECT),
    "chickenpox": ("Chickenpox", "B01.9", 2, INFECT),
    "dengue": ("Dengue", "A90", 4, INFECT),
    "typhoid": ("Typhoid", "A01.0", 4, INFECT),
    "hepatitisa": ("Hepatitis A", "B15.9", 3, GASTRO),
    "hepatitisb": ("Hepatitis B", "B16.9", 4, GASTRO),
    "hepatitisc": ("Hepatitis C", "B17.10", 4, GASTRO),
    "hepatitisd": ("Hepatitis D", "B17.0", 4, GASTRO),
    "hepatitise": ("Hepatitis E", "B17.2", 3, GASTRO),
    "alcoholichepatitis": ("Alcoholic Hepatitis", "K70.10", 4, GASTRO),
    "tuberculosis": ("Tuberculosis", "A15.9", 4, PULM),
    "commoncold": ("Common Cold", "J00", 1, GEN),
    "pneumonia": ("Pneumonia", "J18.9", 4, PULM),
    "dimorphichemorrhoidspiles": ("Hemorrhoids (Piles)", "K64.9", 2, GASTRO),
    "heartattack": ("Heart Attack", "I21.9", 5, CARD),
    "varicoseveins": ("Varicose Veins", "I83.90", 2, CARD),
    "hypothyroidism": ("Hypothyroidism", "E03.9", 2, ENDO),
    "hyperthyroidism": ("Hyperthyroidism", "E05.90", 3, ENDO),
    "hypoglycemia": ("Hypoglycemia", "E16.2", 4, ENDO),
    "osteoarthristis": ("Osteoarthritis", "M19.90", 2, ORTHO),
    "arthritis": ("Arthritis", "M13.9", 2, ORTHO),
    "vertigoparoymsalpositionalvertigo": ("Positional Vertigo (BPPV)", "H81.10", 2, NEURO),
    "acne": ("Acne", "L70.0", 1, DERM),
    "urinarytractinfection": ("Urinary Tract Infection", "N39.0", 2, URO),
    "psoriasis": ("Psoriasis", "L40.9", 2, DERM),
    "impetigo": ("Impetigo", "L01.00", 2, DERM),
}
# Spelling differences between the Kaggle files
ALIASES = {"dimorphichemmorhoidspiles": "dimorphichemorrhoidspiles",
           "paralysisbrainhaemorrhage": "paralysisbrainhemorrhage"}

# Readable names for symptoms whose Kaggle key has a typo
SYMPTOM_NAMES = {
    "foulsmellofurine": "Foul smell of urine", "swollenextremeties": "Swollen extremities",
    "scurring": "Scarring", "coldhandsandfeets": "Cold hands and feet",
    "dischromicpatches": "Dyschromic patches", "toxiclooktyphos": "Toxic look (typhos)",
    "diarrhoea": "Diarrhoea", "spottingurination": "Spotting during urination",
    "burningmicturition": "Burning urination", "continuousfeelofurine": "Constant urge to urinate",
    "receivingunsterileinjections": "Received unsterile injections",
    "receivingbloodtransfusion": "Received blood transfusion",
    "historyofalcoholconsumption": "History of alcohol use",
}

BODY = {
    "Skin": "itching skinrash nodalskineruptions dischromicpatches pusfilledpimples blackheads scurring skinpeeling "
            "silverlikedusting smalldentsinnails inflammatorynails blister redsorearoundnose yellowcrustooze "
            "redspotsoverbody bruising brittlenails",
    "Respiratory": "continuoussneezing cough breathlessness phlegm throatirritation sinuspressure runnynose congestion "
                   "mucoidsputum rustysputum bloodinsputum patchesinthroat lossofsmell",
    "Digestive & Liver": "stomachpain acidity ulcersontongue vomiting indigestion nausea lossofappetite constipation "
                         "abdominalpain diarrhoea bellypain passageofgases internalitching stomachbleeding "
                         "distentionofabdomen swellingofstomach painduringbowelmovements paininanalregion bloodystool "
                         "irritationinanus yellowishskin darkurine yellowurine yellowingofeyes acuteliverfailure fluidoverload",
    "Urinary": "burningmicturition spottingurination bladderdiscomfort foulsmellofurine continuousfeelofurine polyuria",
    "Heart & Circulation": "chestpain fastheartrate palpitations swollenlegs swollenbloodvessels prominentveinsoncalf coldhandsandfeets",
    "Brain & Nerves": "headache dizziness spinningmovements lossofbalance unsteadiness weaknessofonebodyside slurredspeech "
                      "alteredsensorium coma lackofconcentration visualdisturbances blurredanddistortedvision "
                      "painbehindtheeyes stiffneck",
    "Bones & Muscles": "jointpain musclewasting backpain neckpain kneepain hipjointpain muscleweakness swellingjoints "
                       "movementstiffness painfulwalking musclepain cramps weaknessinlimbs",
    "Hormones & Metabolism": "weightgain weightloss irregularsugarlevel obesity enlargedthyroid puffyfaceandeyes "
                             "swollenextremeties dryingandtinglinglips abnormalmenstruation increasedappetite excessivehunger",
    "Mood": "anxiety moodswings restlessness depression irritability",
    "Eyes": "rednessofeyes wateringfromeyes sunkeneyes",
    "Risk factors": "familyhistory extramaritalcontacts receivingbloodtransfusion receivingunsterileinjections historyofalcoholconsumption",
}
BODY_OF = {k: system for system, keys in BODY.items() for k in keys.split()}


def key(s): return re.sub(r"[^a-z0-9]", "", s.lower())
def dkey(s): return ALIASES.get(key(s), key(s))
def q(s): return "NULL" if s is None else "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


def load():
    # 1. symptoms + severity (Kaggle lists fluid_overload twice; keep the first value)
    sym = {}
    for r in csv.DictReader(open(DATA / "Symptom-severity.csv", encoding="utf-8")):
        k = key(r["Symptom"])
        if k and k != "prognosis" and k not in sym:
            raw = r["Symptom"].strip()
            sym[k] = {"raw": raw, "sev": int(r["weight"]),
                      "name": SYMPTOM_NAMES.get(k, raw.replace("_", " ").strip().capitalize())}
    # 2. patient records
    cases = []
    for r in list(csv.reader(open(DATA / "dataset.csv", encoding="utf-8")))[1:]:
        d = dkey(r[0])
        if d not in DISEASES: raise SystemExit(f"Unknown disease in dataset.csv: {r[0]!r}")
        s = sorted({key(x) for x in r[1:] if x.strip()})
        missing = [x for x in s if x not in sym]
        if missing: raise SystemExit(f"Symptom without severity: {missing}")
        cases.append((d, s))
    desc = {dkey(r["Disease"]): (lambda x: x[:1].upper() + x[1:])(r["Description"].strip())
            for r in csv.DictReader(open(DATA / "symptom_Description.csv", encoding="utf-8"))}
    prec = {}
    for r in csv.reader(open(DATA / "symptom_precaution.csv", encoding="utf-8")):
        if r[0] == "Disease": continue
        prec[dkey(r[0])] = [p.strip().capitalize() for p in r[1:] if p.strip()]
    return sym, cases, desc, prec


# ---- same scoring as the match_diseases stored procedure ----
def weights(cases, sym):
    """weight = P(symptom | disease) * (0.5 + severity/14)   -- identical to the SQL in healthcare_mysql.sql"""
    by = collections.defaultdict(list)
    for d, s in cases: by[d].append(s)
    W = {}
    for d, L in by.items():
        cnt = collections.Counter(x for s in L for x in s)
        W[d] = {}
        for x, n in cnt.items():
            freq = (Decimal(n) / len(L)).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            fac = Decimal("0.5") + (Decimal(sym[x]["sev"]) / 14).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            W[d][x] = max(Decimal("0.01"), (freq * fac).quantize(Decimal("0.01"), ROUND_HALF_UP))
    return W


def rank(W, symptoms, severity=3):
    """disease coverage x patient coverage, exactly like match_diseases()"""
    out = []
    for d, w in W.items():
        hit = [x for x in symptoms if x in w]
        if hit:
            disease_cov = sum(w[x] * severity for x in hit) / sum(v * 5 for v in w.values())
            patient_cov = Decimal(len(hit) * severity) / (len(symptoms) * severity)
            out.append((disease_cov * patient_cov, d))
    return [d for _, d in sorted(out, reverse=True)]


def evaluate(cases, sym):
    res = []
    # A) 80/20 split on the unique symptom combinations (Kaggle repeats each combination many times)
    uniq = sorted({(d, tuple(s)) for d, s in cases})
    random.Random(42).shuffle(uniq)
    cut = int(len(uniq) * 0.8)
    W = weights([(d, list(s)) for d, s in uniq[:cut]], sym)
    test = uniq[cut:]
    t1 = sum(rank(W, s)[:1] == [d] for d, s in test); t3 = sum(d in rank(W, s)[:3] for d, s in test)
    res.append(("Hold-out: 20% of unique symptom combinations", len(test), t1 / len(test), t3 / len(test)))
    # B) stress test: every record, but the patient only reports half of their symptoms
    W = weights(cases, sym); rnd = random.Random(7); t1 = t3 = 0
    for d, s in cases:
        part = rnd.sample(s, min(len(s), max(2, len(s) // 2)))
        r = rank(W, part); t1 += r[:1] == [d]; t3 += d in r[:3]
    res.append(("Stress test: all 4,920 records, only half the symptoms reported", len(cases), t1 / len(cases), t3 / len(cases)))
    return res


def build_sql(sym, cases, desc, prec, evals):
    sym_ids = {k: i for i, k in enumerate(sym, 1)}
    dis_ids = {k: i for i, k in enumerate(DISEASES, 1)}
    L = [START,
         "-- Generated by kaggle_import.py from data/kaggle/*.csv. Edit the CSVs and re-run the script, not this block.",
         "-- Source: Kaggle, 'Disease Symptom Prediction' (itachi9604)",
         f"-- {len(cases)} patient records | {len(DISEASES)} diseases | {len(sym)} symptoms", ""]
    L.append("INSERT INTO symptoms (symptom_id,name,kaggle_key,body_system,severity_weight) VALUES")
    L.append(",\n".join(f" ({sym_ids[k]},{q(v['name'])},{q(v['raw'])},{q(BODY_OF.get(k, 'General'))},{v['sev']})"
                        for k, v in sym.items()) + ";\n")
    L.append("INSERT INTO diseases (disease_id,name,icd10_code,severity_level,recommended_specialization_id,description) VALUES")
    L.append(",\n".join(f" ({dis_ids[k]},{q(n)},{q(icd)},{sv},{sp},{q(desc.get(k))})"
                        for k, (n, icd, sv, sp) in DISEASES.items()) + ";\n")
    L.append("INSERT INTO disease_precautions (disease_id,step_no,precaution) VALUES")
    L.append(",\n".join(f" ({dis_ids[k]},{i},{q(p)})" for k in DISEASES for i, p in enumerate(prec.get(k, []), 1)) + ";\n")
    L.append("-- Raw Kaggle records, one row per patient record")
    L.append("INSERT INTO kaggle_cases (case_id,disease_id) VALUES")
    rows = [f"({i},{dis_ids[d]})" for i, (d, _) in enumerate(cases, 1)]
    L.append(",\n".join(",".join(rows[j:j + 20]) for j in range(0, len(rows), 20)) + ";\n")
    L.append("INSERT INTO kaggle_case_symptoms (case_id,symptom_id) VALUES")
    pairs = [f"({i},{sym_ids[x]})" for i, (_, s) in enumerate(cases, 1) for x in s]
    L.append(",\n".join(",".join(pairs[j:j + 25]) for j in range(0, len(pairs), 25)) + ";\n")
    L.append("""-- Build the AI knowledge base from the raw Kaggle records, inside MySQL:
--   weight = (records of the disease that show the symptom / all records of the disease)
--            * (0.5 + Kaggle severity / 14)        -> always between 0.01 and 1
INSERT INTO disease_symptoms (disease_id, symptom_id, weight)
SELECT kc.disease_id, kcs.symptom_id,
       GREATEST(0.01, ROUND(ROUND(COUNT(*) / t.n, 4) * (0.5 + ROUND(s.severity_weight / 14, 4)), 2))
FROM kaggle_case_symptoms kcs
JOIN kaggle_cases kc ON kc.case_id = kcs.case_id
JOIN symptoms s      ON s.symptom_id = kcs.symptom_id
JOIN (SELECT disease_id, COUNT(*) AS n FROM kaggle_cases GROUP BY disease_id) t ON t.disease_id = kc.disease_id
GROUP BY kc.disease_id, kcs.symptom_id, t.n, s.severity_weight;
""")
    L.append("INSERT INTO ai_models (model_id,name,version) VALUES (1,'SymptomMatcher (Kaggle knowledge base)','2.0.0');\n")
    L.append("INSERT INTO model_evaluations (model_id,dataset,method,records,top1_accuracy,top3_accuracy) VALUES")
    L.append(",\n".join(f" (1,'Kaggle: Disease Symptom Prediction',{q(m)},{n},{a1:.4f},{a3:.4f})" for m, n, a1, a3 in evals) + ";")
    L.append(END)
    return "\n".join(L)


def main():
    sym, cases, desc, prec = load()
    evals = evaluate(cases, sym)
    block = build_sql(sym, cases, desc, prec, evals)
    sql = SQL_FILE.read_text(encoding="utf-8")
    a, b = sql.index(START), sql.index(END) + len(END)
    SQL_FILE.write_text(sql[:a] + block + sql[b:], encoding="utf-8")
    print(f"Wrote {len(cases)} records, {len(DISEASES)} diseases, {len(sym)} symptoms into {SQL_FILE.name}")
    for m, n, a1, a3 in evals:
        print(f"  {m}: top-1 {a1:.1%}, top-3 {a3:.1%}  (n={n})")


if __name__ == "__main__":
    main()
