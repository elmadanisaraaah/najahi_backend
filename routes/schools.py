import os
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, request, jsonify
import requests

schools_bp = Blueprint("schools", __name__)

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "mistral-large-latest"

SYSTEM_PROMPT = (
    "You are NajahiBot, a highly accurate expert assistant specializing exclusively in Moroccan higher education. "
    "You have deep, verified knowledge of all Moroccan schools and universities including ENSA, ENSIAS, ENSAM, EMI, INPT, EHTP, "
    "ENCG, ISCAE, UIR, UM6P, Al Akhawayn, all Facultés de Médecine, CPGE, BTS, OFPPT, and all public universities "
    "(Mohammed V, Hassan II, Cadi Ayyad, Sidi Mohammed Ben Abdellah, Ibn Tofail, Abdelmalek Essaadi, Ibn Zohr, "
    "Moulay Ismail, Mohammed Premier, Hassan Premier, Sultan Moulay Slimane).\n\n"

    "RULES:\n"
    "1. NEVER invent data, statistics, phone numbers, or websites you are not sure about\n"
    "2. ALWAYS be specific - give real admission requirements, real concours names (CNC, ENCG concours, Médecine concours)\n"
    "3. If you don't know something exactly, say 'je ne suis pas certain de ce chiffre exact, vérifiez sur le site officiel'\n"
    "4. Respond in the EXACT same language as the user (French, Arabic, English, or Darija)\n"
    "5. Be comprehensive but accurate - quality over quantity\n"
    "6. For engineering schools: mention CNC, note bac 16+, CPGE required\n"
    "7. For medicine: mention the national concours, very competitive, 17+ average\n"
    "8. For ENCG/business: mention their own concours, note bac 14+\n"
    "9. Always mention if a school is public (free) or private (fees in MAD)"
)


def call_mistral(query: str, api_key: str):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MISTRAL_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": query},
        ],
        "max_tokens": 2000,
    }
    try:
        res = requests.post(MISTRAL_API_URL, headers=headers, json=body, timeout=30)
        print(f"[Mistral] status={res.status_code}")
        print(f"[Mistral] response={res.text[:300]}")
        if not res.ok:
            print(f"[Mistral] error body: {res.text}")
            return None
        data = res.json()
        text = data["choices"][0]["message"]["content"].strip()
        print(f"[Mistral] answer length={len(text)} chars")
        return text
    except requests.exceptions.Timeout:
        print("[Mistral] request timed out")
        return None
    except Exception as e:
        print(f"[Mistral] exception: {e}")
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

        api_key = os.environ.get("MISTRAL_API_KEY", "")
        print(f"[/ask] MISTRAL_API_KEY prefix={api_key[:10]!r}")

        if not api_key:
            return jsonify({
                "answer": "⚠️ Clé API Mistral manquante. Vérifie le fichier .env (MISTRAL_API_KEY).",
                "found": False,
            }), 200

        answer = call_mistral(query, api_key)

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
