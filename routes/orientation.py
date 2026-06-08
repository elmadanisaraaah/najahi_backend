import os
import json
import uuid
import jwt
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, request, jsonify, g
import requests
from psycopg2.extras import RealDictCursor
from db import get_conn, release_conn
from config import Config
from middleware import token_required

orientation_bp = Blueprint("orientation", __name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

# ── Human-readable labels (used in Groq prompts) ─────────────────────────────

DOMAINE_LABELS = {
    "technologie":            "Technologie & Informatique",
    "ingenierie":             "Ingénierie & BTP",
    "business":               "Business & Finance",
    "sante":                  "Santé & Médecine",
    "arts_design":            "Architecture & Design",
    "sciences":               "Sciences Fondamentales",
    "droit_sciences_sociales":"Droit & Sciences Sociales",
    "communication":          "Communication & Marketing",
    "education":              "Éducation & Enseignement",
    "environnement":          "Environnement & Énergie",
    "tourisme":               "Tourisme & Hôtellerie",
}

CARRIERE_LABELS = {
    "ingenieur_dev":      "Ingénieur / Développeur",
    "medecin":            "Médecin / Pharmacien / Dentiste",
    "manager":            "Manager / Directeur",
    "entrepreneur":       "Entrepreneur",
    "chercheur":          "Chercheur / Scientifique",
    "enseignant":         "Enseignant / Formateur",
    "fonctionnaire":      "Fonctionnaire / Diplomate",
    "architecte_designer":"Architecte / Designer / Créatif",
    "juriste":            "Juriste / Avocat / Notaire",
    "economiste":         "Économiste / Comptable / Financier",
    "paramedical":        "Infirmier / Paramédical",
    "telecoms_cyber":     "Télécom / Réseaux / Cybersécurité",
    "data_ia":            "Data Scientist / IA Engineer",
    "environnementaliste":"Environnementaliste / Agronome",
    "tourisme":           "Tourisme / Hôtellerie / Restauration",
    "product_ux":         "Product Manager / UX Designer",
    # backward compat
    "ingenieur_logiciel":    "Ingénieur Logiciel",
    "data_scientist":        "Data Scientist",
    "analyste_financier":    "Analyste Financier",
    "avocat":                "Avocat / Juriste",
    "professeur":            "Professeur / Formateur",
    "ingenieur_btp":         "Ingénieur BTP",
    "architecte":            "Architecte",
    "scientifique_chercheur":"Chercheur / Scientifique",
}

BAC_MAP = {
    "Bac Sciences Maths A":            "sciences_maths",
    "Bac Sciences Maths B":            "sciences_maths",
    "Bac Sciences Physiques":          "sciences_physiques",
    "Bac Sciences de la Vie":          "sciences_biologiques",
    "Bac Sciences Économiques":        "sciences_economiques",
    "Bac Lettres & Sciences Humaines": "lettres",
    "Bac Technologie Électrique":      "sciences_physiques",
    "BTS / DUT":                       "bts_dut",
    "Autre":                           None,
}

# ── Schools database ──────────────────────────────────────────────────────────
# Each school: primary_domaines (40pt match), secondary_domaines (15pt match),
# careers (30pt match), bac_types, moyenne_min, plus extra info.

SCHOOLS_DB = [
    {
        "id": "emi",
        "name": "EMI – École Mohammadia d'Ingénieurs",
        "type": "engineering",
        "city": ["Rabat"],
        "budget": "public",
        "primary_domaines":   ["ingenierie", "technologie"],
        "secondary_domaines": ["sciences"],
        "careers": ["ingenieur_dev", "ingenieur_btp", "chercheur", "data_ia",
                    "ingenieur_logiciel", "scientifique_chercheur"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 17.0,
        "concours": True,
        "description": "La meilleure école d'ingénieurs publique du Maroc",
        "career_paths": ["Ingénieur logiciel", "Chef de projet IT", "Directeur technique", "Consultant", "Entrepreneur tech"],
        "salary_range": "8 000 – 25 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "ensias",
        "name": "ENSIAS – École Nationale Supérieure d'Informatique et d'Analyse des Systèmes",
        "type": "engineering",
        "city": ["Rabat"],
        "budget": "public",
        "primary_domaines":   ["technologie"],
        "secondary_domaines": ["ingenierie", "sciences"],
        "careers": ["ingenieur_dev", "data_ia", "telecoms_cyber", "product_ux",
                    "chercheur", "data_scientist", "ingenieur_logiciel", "scientifique_chercheur"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 16.5,
        "concours": True,
        "description": "La référence nationale en informatique et génie logiciel",
        "career_paths": ["Développeur full-stack", "Data Engineer", "Cybersécurité", "DevOps", "Product Manager"],
        "salary_range": "8 000 – 22 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "inpt",
        "name": "INPT – Institut National des Postes et Télécommunications",
        "type": "engineering",
        "city": ["Rabat"],
        "budget": "public",
        "primary_domaines":   ["technologie"],
        "secondary_domaines": ["ingenierie"],
        "careers": ["telecoms_cyber", "ingenieur_dev", "data_ia", "ingenieur_logiciel"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 16.0,
        "concours": True,
        "description": "L'école des télécommunications et réseaux du Maroc",
        "career_paths": ["Ingénieur réseaux", "Expert cybersécurité", "Chef de projet télécom", "Architecte SI"],
        "salary_range": "7 000 – 20 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "ehtp",
        "name": "EHTP – École Hassania des Travaux Publics",
        "type": "engineering",
        "city": ["Casablanca"],
        "budget": "public",
        "primary_domaines":   ["ingenierie"],
        "secondary_domaines": ["environnement"],
        "careers": ["ingenieur_btp", "environnementaliste", "ingenieur_dev",
                    "chercheur", "scientifique_chercheur"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 16.0,
        "concours": True,
        "description": "L'école d'élite des travaux publics et génie civil",
        "career_paths": ["Ingénieur civil", "Chef de chantier", "Urbaniste", "Consultant BTP"],
        "salary_range": "8 000 – 22 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "ensa",
        "name": "ENSA – École Nationale des Sciences Appliquées",
        "type": "engineering",
        "city": ["Agadir", "Casablanca", "Fès", "Kenitra", "Marrakech", "Oujda", "Rabat", "Tanger"],
        "budget": "public",
        "primary_domaines":   ["ingenierie", "technologie"],
        "secondary_domaines": ["sciences", "environnement"],
        "careers": ["ingenieur_dev", "ingenieur_btp", "telecoms_cyber",
                    "chercheur", "ingenieur_logiciel", "data_ia"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 14.5,
        "concours": True,
        "description": "Réseau de 8 écoles d'ingénieurs publiques à travers le Maroc",
        "career_paths": ["Ingénieur informatique", "Ingénieur civil", "Ingénieur industriel", "Chef de projet"],
        "salary_range": "7 000 – 18 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "encg",
        "name": "ENCG – École Nationale de Commerce et de Gestion",
        "type": "business",
        "city": ["Agadir", "Casablanca", "Fès", "Kenitra", "Marrakech", "Oujda", "Settat", "Tanger"],
        "budget": "public",
        "primary_domaines":   ["business"],
        "secondary_domaines": ["droit_sciences_sociales", "communication"],
        "careers": ["manager", "economiste", "entrepreneur", "fonctionnaire",
                    "analyste_financier", "product_ux"],
        "bac_types": ["sciences_economiques", "sciences_maths", "sciences_physiques", "lettres"],
        "moyenne_min": 14.0,
        "concours": True,
        "description": "La grande école publique de commerce et gestion du Maroc",
        "career_paths": ["Responsable commercial", "Contrôleur de gestion", "Directeur marketing", "Entrepreneur"],
        "salary_range": "5 000 – 18 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "iscae",
        "name": "ISCAE – Institut Supérieur de Commerce et d'Administration des Entreprises",
        "type": "business",
        "city": ["Casablanca", "Rabat"],
        "budget": "public",
        "primary_domaines":   ["business"],
        "secondary_domaines": ["droit_sciences_sociales"],
        "careers": ["manager", "economiste", "entrepreneur", "analyste_financier"],
        "bac_types": ["sciences_economiques", "sciences_maths", "sciences_physiques"],
        "moyenne_min": 15.0,
        "concours": True,
        "description": "Grande école de management et d'administration des affaires",
        "career_paths": ["Directeur financier", "Auditeur", "Consultant stratégie", "DG / PDG"],
        "salary_range": "6 000 – 22 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "medecine",
        "name": "Faculté de Médecine et de Pharmacie",
        "type": "health",
        "city": ["Rabat", "Casablanca", "Fès", "Marrakech", "Oujda", "Tanger"],
        "budget": "public",
        "primary_domaines":   ["sante"],
        "secondary_domaines": ["sciences"],
        "careers": ["medecin", "paramedical", "chercheur", "scientifique_chercheur"],
        "bac_types": ["sciences_biologiques", "sciences_physiques"],
        "moyenne_min": 17.5,
        "concours": True,
        "description": "La voie royale pour devenir médecin, pharmacien ou dentiste",
        "career_paths": ["Médecin généraliste", "Spécialiste", "Pharmacien", "Chirurgien-dentiste", "Chercheur médical"],
        "salary_range": "12 000 – 60 000 MAD/mois",
        "duration": "7–9 ans après bac",
    },
    {
        "id": "um6p",
        "name": "UM6P – Université Mohammed VI Polytechnique",
        "type": "engineering",
        "city": ["Ben Guerir", "Rabat", "Casablanca"],
        "budget": "semi_public",
        "primary_domaines":   ["technologie", "ingenierie"],
        "secondary_domaines": ["sciences", "environnement"],
        "careers": ["data_ia", "chercheur", "ingenieur_dev", "ingenieur_btp",
                    "environnementaliste", "scientifique_chercheur"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 16.0,
        "concours": True,
        "description": "Université d'excellence africaine avec bourses disponibles",
        "career_paths": ["Chercheur en IA", "Ingénieur agronome", "Data Scientist", "Ingénieur mines", "Chef de projet R&D"],
        "salary_range": "10 000 – 30 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "uir",
        "name": "UIR – Université Internationale de Rabat",
        "type": "engineering",
        "city": ["Rabat"],
        "budget": "prive",
        "primary_domaines":   ["technologie", "ingenierie", "arts_design", "business"],
        "secondary_domaines": ["droit_sciences_sociales", "sante", "communication"],
        "careers": ["ingenieur_dev", "architecte_designer", "manager", "medecin",
                    "telecoms_cyber", "data_ia", "product_ux", "ingenieur_logiciel"],
        "bac_types": ["sciences_maths", "sciences_physiques", "sciences_economiques", "lettres"],
        "moyenne_min": 13.0,
        "concours": False,
        "description": "Université privée internationale avec partenariats mondiaux",
        "career_paths": ["Ingénieur aéronautique", "Architecte", "Manager international", "Avocat d'affaires"],
        "salary_range": "8 000 – 25 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "emsi",
        "name": "EMSI – École Marocaine des Sciences de l'Ingénieur",
        "type": "engineering",
        "city": ["Casablanca", "Rabat", "Marrakech", "Fès"],
        "budget": "prive",
        "primary_domaines":   ["technologie", "ingenierie"],
        "secondary_domaines": [],
        "careers": ["ingenieur_dev", "telecoms_cyber", "ingenieur_btp",
                    "ingenieur_logiciel", "data_ia"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 12.0,
        "concours": False,
        "description": "École d'ingénieurs privée reconnue par l'État",
        "career_paths": ["Ingénieur logiciel", "Ingénieur réseaux", "Chef de projet", "Développeur mobile"],
        "salary_range": "6 000 – 18 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "hem",
        "name": "HEM – Hautes Études de Management",
        "type": "business",
        "city": ["Casablanca", "Rabat", "Marrakech", "Fès", "Tanger"],
        "budget": "prive",
        "primary_domaines":   ["business", "communication"],
        "secondary_domaines": [],
        "careers": ["manager", "entrepreneur", "economiste", "analyste_financier",
                    "product_ux", "fonctionnaire"],
        "bac_types": ["sciences_economiques", "sciences_maths", "lettres", "sciences_physiques"],
        "moyenne_min": 12.0,
        "concours": False,
        "description": "La grande école de management privée de référence au Maroc",
        "career_paths": ["Directeur commercial", "Responsable marketing", "Entrepreneur", "Consultant"],
        "salary_range": "5 000 – 20 000 MAD/mois",
        "duration": "5 ans après bac",
    },
    {
        "id": "architecture",
        "name": "École Nationale d'Architecture",
        "type": "architecture",
        "city": ["Rabat", "Marrakech", "Fès", "Tétouan"],
        "budget": "public",
        "primary_domaines":   ["arts_design"],
        "secondary_domaines": ["ingenierie"],
        "careers": ["architecte_designer", "architecte", "ingenieur_btp", "product_ux"],
        "bac_types": ["sciences_maths", "sciences_physiques", "lettres"],
        "moyenne_min": 14.0,
        "concours": True,
        "description": "La voie officielle pour devenir architecte au Maroc",
        "career_paths": ["Architecte", "Urbaniste", "Designer d'intérieur", "Chef de projet BTP"],
        "salary_range": "6 000 – 20 000 MAD/mois",
        "duration": "6 ans après bac",
    },
    {
        "id": "cpge",
        "name": "CPGE – Classes Préparatoires aux Grandes Écoles",
        "type": "preparatoire",
        "city": ["Rabat", "Casablanca", "Fès", "Marrakech", "Oujda", "Tanger"],
        "budget": "public",
        "primary_domaines":   ["technologie", "ingenierie", "sciences"],
        "secondary_domaines": [],
        "careers": ["ingenieur_dev", "chercheur", "ingenieur_btp", "data_ia",
                    "ingenieur_logiciel", "scientifique_chercheur"],
        "bac_types": ["sciences_maths", "sciences_physiques"],
        "moyenne_min": 16.5,
        "concours": False,
        "description": "2 ans intenses pour intégrer EMI, ENSIAS, ENSA via concours",
        "career_paths": ["Ingénieur (après grande école)", "Chercheur", "Directeur technique"],
        "salary_range": "8 000 – 25 000 MAD/mois (après grande école)",
        "duration": "2 ans + grande école",
    },
    {
        "id": "fsjes",
        "name": "FSJES – Faculté des Sciences Juridiques, Économiques et Sociales",
        "type": "university",
        "city": ["Rabat", "Casablanca", "Fès", "Marrakech", "Oujda", "Tanger", "Meknès"],
        "budget": "public",
        "primary_domaines":   ["droit_sciences_sociales", "business"],
        "secondary_domaines": ["communication", "education"],
        "careers": ["juriste", "fonctionnaire", "economiste", "enseignant",
                    "avocat", "professeur"],
        "bac_types": ["sciences_economiques", "sciences_maths", "lettres", "sciences_physiques", "bts_dut"],
        "moyenne_min": 10.0,
        "concours": False,
        "description": "Faculté publique ouverte à tous les profils",
        "career_paths": ["Avocat", "Juge", "Fonctionnaire d'État", "Expert-comptable", "Économiste"],
        "salary_range": "4 000 – 15 000 MAD/mois",
        "duration": "3–5 ans après bac",
    },
    {
        "id": "tourisme",
        "name": "ISIT / ISTA Tourisme – Institut Spécialisé en Tourisme et Hôtellerie",
        "type": "university",
        "city": ["Casablanca", "Marrakech", "Agadir", "Fès", "Tanger"],
        "budget": "public",
        "primary_domaines":   ["tourisme", "communication"],
        "secondary_domaines": ["business"],
        "careers": ["tourisme", "manager", "entrepreneur"],
        "bac_types": ["sciences_economiques", "lettres", "sciences_physiques", "bts_dut"],
        "moyenne_min": 11.0,
        "concours": False,
        "description": "Formation professionnelle en tourisme, hôtellerie et restauration",
        "career_paths": ["Directeur d'hôtel", "Guide touristique", "Chef de cuisine", "Event manager"],
        "salary_range": "4 000 – 14 000 MAD/mois",
        "duration": "3 ans après bac",
    },
]


# ── DB setup ─────────────────────────────────────────────────────────────────

def _ensure_orientation_results_table():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orientation_results (
                    id          UUID        PRIMARY KEY,
                    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    ecole       VARCHAR(255),
                    filiere     VARCHAR(255),
                    confidence  FLOAT,
                    alternatives JSONB,
                    raw_answers  JSONB,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
    except Exception as e:
        print(f"[orientation] table creation error: {e}")
        conn.rollback()
    finally:
        release_conn(conn)

_ensure_orientation_results_table()


# ── Scoring engine ────────────────────────────────────────────────────────────

def score_school(school, data):
    domaine  = data.get("domaine", "")
    carriere = data.get("carriere", "")
    bac      = data.get("bac", "")
    moyenne  = float(data.get("moyenne") or 14)
    budget   = data.get("budget", "")
    ville    = data.get("ville", "")
    mobility = data.get("mobility", True)
    bac_key  = BAC_MAP.get(bac)

    # ── HARD EXCLUSIONS (return -999 immediately) ──────────────────────────

    # Médecine: strictly for sante/sciences + right bac
    if school["id"] == "medecine":
        if domaine not in ("sante", "sciences"):
            return -999
        if bac_key == "bts_dut":
            return -999
        if carriere not in ("medecin", "paramedical", "chercheur", "scientifique_chercheur"):
            return -999

    # CPGE: only for high-achievers, not BTS/DUT, not business/health/arts
    if school["id"] == "cpge":
        if bac_key == "bts_dut":
            return -999
        if moyenne < 16.0:
            return -999
        if domaine in ("sante", "business", "arts_design", "droit_sciences_sociales",
                       "communication", "education", "tourisme"):
            return -999
        if bac_key not in ("sciences_maths", "sciences_physiques", None):
            return -999

    # Architecture: only for arts_design/ingenierie
    if school["id"] == "architecture":
        if domaine not in ("arts_design", "ingenierie"):
            return -999

    # Engineering schools: never for health-only seekers
    if school["type"] == "engineering" and school["id"] not in ("uir",):
        if domaine == "sante" and carriere == "medecin":
            return -999

    # Business schools: never for health seekers
    if school["type"] == "business":
        if domaine == "sante" and carriere in ("medecin", "paramedical"):
            return -999

    # ── 1. DOMAIN MATCH (40 pts) ───────────────────────────────────────────
    score = 0
    if domaine in school["primary_domaines"]:
        score += 40
    elif domaine in school["secondary_domaines"]:
        score += 15
    else:
        score -= 35

    # ── 2. CAREER MATCH (30 pts) ───────────────────────────────────────────
    if carriere in school["careers"]:
        score += 30
    else:
        # Partial match via career family
        career_families = {
            "data_ia":        ["data_scientist", "ingenieur_dev", "ingenieur_logiciel"],
            "ingenieur_dev":  ["data_ia", "ingenieur_logiciel", "telecoms_cyber"],
            "manager":        ["economiste", "entrepreneur", "analyste_financier"],
            "economiste":     ["manager", "analyste_financier"],
            "medecin":        ["paramedical", "chercheur"],
            "juriste":        ["fonctionnaire", "avocat"],
            "chercheur":      ["scientifique_chercheur", "data_ia"],
            "architecte_designer": ["architecte"],
            "enseignant":     ["professeur"],
        }
        related = career_families.get(carriere, [])
        if any(r in school["careers"] for r in related):
            score += 12
        else:
            score -= 20

    # ── 3. GRADES (20 pts) ─────────────────────────────────────────────────
    if moyenne >= school["moyenne_min"]:
        score += min(20, int((moyenne - school["moyenne_min"]) * 5))
    else:
        deficit = school["moyenne_min"] - moyenne
        score -= int(deficit * 10)

    # ── 4. BAC TYPE (part of 20%) ──────────────────────────────────────────
    if bac_key and bac_key != "bts_dut":
        if bac_key in school["bac_types"]:
            score += 8
        else:
            score -= 12
    elif bac_key == "bts_dut":
        if bac_key in school.get("bac_types", []):
            score += 5
        else:
            score -= 5

    # ── 5. BUDGET (10 pts) ─────────────────────────────────────────────────
    if budget == "public":
        if school["budget"] == "public":
            score += 10
        elif school["budget"] == "semi_public":
            score += 4
        else:
            score -= 8
    elif budget == "semi_public":
        if school["budget"] in ("public", "semi_public"):
            score += 8
        else:
            score -= 4
    elif budget in ("prive_abordable", "prive_premium"):
        if school["budget"] == "prive":
            score += 10
        elif school["budget"] == "semi_public":
            score += 5

    # ── 6. LOCATION (10 pts) ───────────────────────────────────────────────
    in_city = ville and ville in school["city"]
    if not mobility:
        score += 10 if in_city else -15
    else:
        if in_city:
            score += 5

    # ── 7. PERSONALITY BONUS (10 pts) ──────────────────────────────────────
    personality = data.get("personnalite") or []
    if isinstance(personality, str):
        personality = [personality]
    personality_lower = [p.lower() for p in personality]

    eng_traits  = {"analytique", "curieux", "logique", "rigoureux"}
    biz_traits  = {"leader", "ambitieux", "charismatique", "communicant"}
    med_traits  = {"empathique", "altruiste", "patient", "serviable"}
    art_traits  = {"créatif", "creatif", "artistique", "imaginatif"}

    if school["type"] in ("engineering", "preparatoire") and any(t in eng_traits for t in personality_lower):
        score += 10
    elif school["type"] == "business" and any(t in biz_traits for t in personality_lower):
        score += 10
    elif school["type"] == "health" and any(t in med_traits for t in personality_lower):
        score += 10
    elif school["type"] == "architecture" and any(t in art_traits for t in personality_lower):
        score += 10

    return score


def recommend_schools(data, top_n=3):
    scored = []
    for school in SCHOOLS_DB:
        s = score_school(school, data)
        scored.append((s, school))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Filter hard exclusions
    valid = [(s, sch) for s, sch in scored if s > -100]
    if len(valid) < top_n:
        valid = scored  # fallback: take whatever we have

    top = valid[:top_n]
    pct_bases = [92, 78, 64]

    results = []
    for i, (s, sch) in enumerate(top):
        base = pct_bases[i] if i < len(pct_bases) else 55
        if s > 100:
            adj = 5
        elif s > 70:
            adj = 2
        elif s > 40:
            adj = 0
        elif s > 10:
            adj = -3
        else:
            adj = -6
        pct = min(98, max(45, base + adj))
        results.append({"school": sch, "score": s, "match_pct": pct})

    return results


# ── Groq ──────────────────────────────────────────────────────────────────────

def call_groq(prompt, api_key):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": 0.65,
    }
    try:
        res = requests.post(GROQ_API_URL, headers=headers, json=body, timeout=28)
        print(f"[Groq orient] status={res.status_code}")
        if not res.ok:
            print(f"[Groq orient] error={res.text[:200]}")
            return None
        return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Groq orient] exception: {e}")
        return None


def build_groq_prompt(school, data, match_pct):
    domaine_label  = DOMAINE_LABELS.get(data.get("domaine", ""), data.get("domaine", "son domaine"))
    carriere_label = CARRIERE_LABELS.get(data.get("carriere", ""), data.get("carriere", "sa carrière"))
    bac     = data.get("bac", "non précisé")
    moyenne = data.get("moyenne", 14)
    ville   = data.get("ville", "non précisée")
    budget  = data.get("budget", "")
    perso   = ", ".join(data.get("personnalite", [])) or "non précisé"

    budget_txt = {
        "public": "écoles publiques uniquement",
        "semi_public": "semi-public ou avec bourse",
        "prive_abordable": "privé abordable",
        "prive_premium": "privé premium",
    }.get(budget, budget)

    return (
        f"Tu es NajahiBot, conseiller d'orientation expert au Maroc. Réponds en français.\n\n"
        f"Un étudiant marocain a passé le test d'orientation avec ces réponses EXACTES:\n"
        f"- Bac: {bac}\n"
        f"- Moyenne bac: {moyenne}/20\n"
        f"- Domaine choisi: {domaine_label}\n"
        f"- Objectif de carrière: {carriere_label}\n"
        f"- Traits de personnalité: {perso}\n"
        f"- Ville: {ville}\n"
        f"- Budget: {budget_txt}\n\n"
        f"L'école recommandée est: **{school['name']}** (compatibilité {match_pct}%)\n\n"
        f"RÈGLE ABSOLUE: Mentionne EXPLICITEMENT dans le texte:\n"
        f"- Son domaine: \"{domaine_label}\"\n"
        f"- Son objectif: \"{carriere_label}\"\n"
        f"- Sa moyenne: {moyenne}/20\n\n"
        f"Génère UNIQUEMENT un JSON avec:\n"
        f"1. \"pourquoi\": 3 phrases personnalisées qui COMMENCENT par ses vrais choix. "
        f"Ex: \"Tu as choisi le domaine {domaine_label} et ton objectif est de devenir {carriere_label}...\"\n"
        f"2. \"conseils_admission\": 3 conseils CONCRETS et SPÉCIFIQUES à {school['name']}\n\n"
        f"JSON uniquement, aucun texte autour:\n"
        f"{{\"pourquoi\": \"...\", \"conseils_admission\": [\"c1\", \"c2\", \"c3\"]}}"
    )


def build_fallback(school, data):
    domaine_label  = DOMAINE_LABELS.get(data.get("domaine", ""), "ce domaine")
    carriere_label = CARRIERE_LABELS.get(data.get("carriere", ""), "ta carrière")
    moyenne = data.get("moyenne", 14)

    pourquoi = (
        f"Tu as choisi le domaine {domaine_label} et ton objectif est de devenir {carriere_label} — "
        f"{school['name']} est parfaitement alignée avec cette ambition. "
        f"Avec ta moyenne de {moyenne}/20, tu as le profil pour réussir l'intégration. "
        f"{school['description']}."
    )
    conseils = [
        f"Révise intensément les matières fondamentales de ton bac avant le concours de {school['name']}.",
        f"Consulte le site officiel de {school['name']} pour les dates de dépôt de candidature.",
        f"Rejoins les groupes Facebook d'entraide des étudiants de {school['name']} pour des conseils directs.",
    ]
    return pourquoi, conseils


# ── Routes ────────────────────────────────────────────────────────────────────

@orientation_bp.route("/predict", methods=["POST"])
def predict():
    try:
        data = request.get_json(silent=True) or {}

        # ── Extract user from JWT (optional) ─────────────────────────────────
        user_id = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                token = auth_header.split(" ", 1)[1].strip()
                payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
                if payload.get("type") == "access":
                    user_id = payload.get("sub")
            except Exception:
                pass

        # ── Fill missing bac/moyenne/ville from student profile ──────────────
        if user_id and (not data.get("bac") or not data.get("moyenne") or not data.get("ville")):
            conn = get_conn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT type_bac, note_bac, ville FROM student_profiles WHERE user_id = %s LIMIT 1",
                        (user_id,)
                    )
                    profile = cur.fetchone()
                if profile:
                    if not data.get("bac") and profile.get("type_bac"):
                        data["bac"] = profile["type_bac"]
                    if not data.get("moyenne") and profile.get("note_bac") is not None:
                        data["moyenne"] = float(profile["note_bac"])
                    if not data.get("ville") and profile.get("ville"):
                        data["ville"] = profile["ville"]
                    print(f"[/predict] profile prefill: bac={data.get('bac')} moy={data.get('moyenne')} ville={data.get('ville')}")
            except Exception as pe:
                print(f"[/predict] profile fetch error: {pe}")
            finally:
                release_conn(conn)

        print(f"[/predict] domaine={data.get('domaine')} carriere={data.get('carriere')} moyenne={data.get('moyenne')}")

        recs = recommend_schools(data, top_n=3)
        api_key = os.environ.get("GROQ_API_KEY", "")
        print(f"[/predict] GROQ key prefix={api_key[:10]!r} | top school={recs[0]['school']['id'] if recs else 'none'}")

        results = []
        for rec in recs:
            school    = rec["school"]
            match_pct = rec["match_pct"]

            pourquoi, conseils = build_fallback(school, data)

            if api_key:
                prompt = build_groq_prompt(school, data, match_pct)
                raw    = call_groq(prompt, api_key)
                if raw:
                    try:
                        start = raw.find("{")
                        end   = raw.rfind("}") + 1
                        if start >= 0 and end > start:
                            parsed = json.loads(raw[start:end])
                            pourquoi = parsed.get("pourquoi", pourquoi)
                            conseils = parsed.get("conseils_admission", conseils)
                    except Exception as pe:
                        print(f"[/predict] JSON parse error: {pe}")

            results.append({
                "id":               school["id"],
                "name":             school["name"],
                "type":             school["type"],
                "city":             school["city"],
                "budget":           school["budget"],
                "match_pct":        match_pct,
                "concours":         school["concours"],
                "pourquoi":         pourquoi,
                "conseils_admission": conseils,
                "career_paths":     school.get("career_paths", []),
                "salary_range":     school.get("salary_range", ""),
                "duration":         school.get("duration", ""),
            })

        # ── Save result for authenticated users ──────────────────────────────
        if user_id and results:
            try:
                top = results[0]
                alternatives = [{"name": r["name"], "match_pct": r["match_pct"]} for r in results[1:]]
                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO orientation_results
                                (id, user_id, ecole, filiere, confidence, alternatives, raw_answers)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            str(uuid.uuid4()),
                            user_id,
                            top["name"],
                            top.get("type", ""),
                            top["match_pct"],
                            json.dumps(alternatives),
                            json.dumps(data),
                        ))
                        conn.commit()
                except Exception as db_e:
                    print(f"[/predict] DB save error: {db_e}")
                    conn.rollback()
                finally:
                    release_conn(conn)
            except Exception as save_e:
                print(f"[/predict] save error: {save_e}")

        return jsonify({"results": results, "success": True}), 200

    except Exception as e:
        print(f"[/predict] FATAL: {e}")
        return jsonify({"results": [], "success": False, "error": str(e)}), 200


@orientation_bp.route("/my-result", methods=["GET"])
@token_required
def my_result():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT ecole, filiere, confidence, alternatives, raw_answers, created_at
                FROM orientation_results
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (str(g.current_user["id"]),))
            row = cur.fetchone()

        if not row:
            return jsonify({"result": None}), 200

        return jsonify({
            "result": {
                "ecole":        row["ecole"],
                "filiere":      row["filiere"],
                "confidence":   float(row["confidence"]) if row["confidence"] else None,
                "alternatives": row["alternatives"] or [],
                "raw_answers":  row["raw_answers"] or {},
                "created_at":   row["created_at"].isoformat() if row["created_at"] else None,
            }
        }), 200
    except Exception as e:
        print(f"[/my-result] error: {e}")
        return jsonify({"result": None, "error": str(e)}), 200
    finally:
        release_conn(conn)


@orientation_bp.route("/test-ia", methods=["POST"])
def test_ia():
    return jsonify({"filiere_recommandee": "Informatique", "dream_fields": ["Informatique"]}), 200
