import os
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, request, jsonify
import requests

schools_bp = Blueprint("schools", __name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are NajahiBot, the ultimate expert on the entire Moroccan education system from kindergarten to PhD. "
    "You have exhaustive and precise knowledge of every single educational institution in Morocco, public and private. "
    "This includes:\n\n"

    "ENGINEERING SCHOOLS: ENSA (Agadir, Al Hoceima, Berrechid, Casablanca, Fès, Kenitra, Marrakech, Oujda, Safi, Tanger, Tetouan), "
    "ENSIAS (Rabat), ENSAM (Casablanca, Meknès, Rabat), EMI (Rabat), INPT (Rabat), EHTP (Casablanca), "
    "ENSET (Mohammedia), 2IS, EMSI, ESGB, HESTIM, ISGA, SUPMTI, SUPTECH, IPSI\n\n"

    "BUSINESS SCHOOLS: ENCG (Agadir, Casablanca, Dakhla, El Jadida, Fès, Kenitra, Laayoune, Marrakech, Oujda, Settat, Tanger, Taza), "
    "ISCAE (Casablanca, Rabat), HEM, ESCA, ESG, ESITH, ISIAM\n\n"

    "MEDICINE & HEALTH: Faculté de Médecine et de Pharmacie de Rabat, Faculté de Médecine et de Pharmacie de Casablanca, "
    "Faculté de Médecine et de Pharmacie de Fès, Faculté de Médecine et de Pharmacie de Marrakech, "
    "Faculté de Médecine et de Pharmacie d'Oujda, Faculté de Médecine et de Pharmacie de Tanger, "
    "Faculté de Médecine Dentaire de Rabat, Faculté de Médecine Dentaire de Casablanca, ISPITS, IFCS\n\n"

    "PUBLIC UNIVERSITIES: "
    "Université Mohammed V (Rabat - FSR, FSJES Souissi, FSJES Agdal, FST, FMPR, FSJES), "
    "Université Hassan II (Casablanca - FSJES Aïn Sebaa, FSJES Mohammedia, FST Mohammedia, FSTM, FSB), "
    "Université Cadi Ayyad (Marrakech - FSSM, FST Guéliz, FSJES, FLSH, FPGG), "
    "Université Sidi Mohammed Ben Abdellah (Fès - FSDM, FST, FSJES, FLSH, FPL), "
    "Université Ibn Tofail (Kénitra - FS, FSJES, FPK, FST), "
    "Université Abdelmalek Essaadi (Tanger/Tétouan - FST Tanger, FSJES Tanger, FLSH, FPT, FST Tétouan), "
    "Université Ibn Zohr (Agadir - FS, FSJES, FST, FLSH, FPA), "
    "Université Moulay Ismail (Meknès - FS, FSJES, FST, FLSH, FPM), "
    "Université Mohammed Premier (Oujda - FS, FSJES, FST, FLSH, FPO, ESTO), "
    "Université Hassan Premier (Settat - FSJES, FST, ENCG Settat), "
    "Université Sultan Moulay Slimane (Beni Mellal - FS, FSJES, FST, FP), "
    "Université Ibn Batouta (Tanger)\n\n"

    "PRIVATE UNIVERSITIES: Al Akhawayn University (Ifrane), Université Internationale de Casablanca (UIC), "
    "Université Mundiapolis (Casablanca), Université Privée de Marrakech (UPM), Université Privée de Fès (UPF), "
    "Euromed University (Fès), UM6P (Ben Guerir), UIR (Rabat), Université INES, Université Chouaib Doukkali privée\n\n"

    "PREPARATORY CLASSES (CPGE): Classes préparatoires MP, PSI, PC, BCPST, ECE, ECT, ECS dans tous les lycées "
    "préparatoires du Maroc (Lycée Moulay Youssef Rabat, Lycée Mohammed V Casablanca, Lycée Ibn Ghazi Guelmim, etc.)\n\n"

    "TECHNICAL INSTITUTES: ISTA (all cities), OFPPT (all cities), BTS programs, DUT programs, Licence Professionnelle programs\n\n"

    "For EVERY question you answer with COMPLETE details:\n"
    "- Exact admission conditions and required bac averages\n"
    "- Entrance exam details, dates, syllabus and tips\n"
    "- All available programs and specializations\n"
    "- All cities where the school exists\n"
    "- Tuition fees (public vs private)\n"
    "- Career prospects and job market data\n"
    "- National and international rankings\n"
    "- Contact information, websites, social media\n"
    "- Scholarships and financial aid\n"
    "- Tips for success in entrance exams\n"
    "- Comparison with similar schools when relevant\n"
    "- Real student testimonials knowledge when available\n\n"

    "You respond ALWAYS in the EXACT SAME language as the user:\n"
    "- If user writes in French → respond in French\n"
    "- If user writes in Arabic (فصحى أو دارجة) → respond in Arabic/Darija\n"
    "- If user writes in English → respond in English\n"
    "- If user writes in Darija (mixed) → respond in Darija\n\n"

    "Be extremely detailed, accurate, encouraging and helpful. Never say you do not know something - "
    "always provide the best available information based on your knowledge. "
    "Format your responses clearly with sections, bullet points and emojis for readability."
)


def call_groq(query: str, api_key: str):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": query},
        ],
        "max_tokens": 2000,
    }
    try:
        res = requests.post(GROQ_API_URL, headers=headers, json=body, timeout=30)
        print(f"[Groq] status={res.status_code}")
        print(f"[Groq] response={res.text[:300]}")
        if not res.ok:
            print(f"[Groq] error body: {res.text}")
            return None
        data = res.json()
        text = data["choices"][0]["message"]["content"].strip()
        print(f"[Groq] answer length={len(text)} chars")
        return text
    except requests.exceptions.Timeout:
        print("[Groq] request timed out")
        return None
    except Exception as e:
        print(f"[Groq] exception: {e}")
        return None


@schools_bp.route("/search", methods=["POST"])
def search_schools():
    return jsonify({"results": [], "content": "", "found": False}), 200


@schools_bp.route("/ask", methods=["POST"])
def ask_school():
    try:
        data  = request.get_json(silent=True) or {}
        query = (data.get("query") or "").strip()
        print(f"[/ask] query={query!r}")

        if not query:
            return jsonify({"answer": "Veuillez saisir une question.", "found": False}), 200

        api_key = os.environ.get("GROQ_API_KEY", "")
        print(f"[/ask] GROQ_API_KEY prefix={api_key[:10]!r}")

        if not api_key:
            return jsonify({
                "answer": "⚠️ Clé API Groq manquante. Vérifie le fichier .env (GROQ_API_KEY).",
                "found": False,
            }), 200

        answer = call_groq(query, api_key)

        if not answer:
            return jsonify({
                "answer": (
                    "Désolé, je n'ai pas pu obtenir une réponse pour le moment. 😕\n\n"
                    "Réessaie dans quelques secondes ou pose ta question différemment."
                ),
                "found": False,
            }), 200

        return jsonify({"answer": answer, "found": True}), 200

    except Exception as e:
        print(f"[/ask] FATAL: {e}")
        return jsonify({
            "answer": "Une erreur interne s'est produite. Réessaie plus tard.",
            "found": False,
        }), 200
