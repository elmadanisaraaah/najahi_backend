import os
import uuid
import traceback
import base64
import json
import re as _re
import requests as _req
from dotenv import load_dotenv
load_dotenv()
from flask import Blueprint, request, jsonify, g, send_file
from werkzeug.utils import secure_filename
from db import get_conn, release_conn
from middleware import token_required
import io

profile_bp = Blueprint("profile", __name__)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads/bulletins")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_BULLETINS = 5
MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def _ensure_bulletins_table():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bulletins (
                    id          UUID        PRIMARY KEY,
                    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    original_name TEXT      NOT NULL,
                    stored_name TEXT        NOT NULL,
                    uploaded_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_conn(conn)


_ensure_bulletins_table()

_PIXTRAL_MODEL = "pixtral-12b-2409"
_notes_table_ok = False


def _ensure_notes_table():
    global _notes_table_ok
    if _notes_table_ok:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bulletin_notes (
                    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    bulletin_id UUID        REFERENCES bulletins(id) ON DELETE CASCADE,
                    matiere     VARCHAR(120) NOT NULL,
                    note        DECIMAL(4,2) NOT NULL,
                    coefficient DECIMAL(3,1),
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
        _notes_table_ok = True
    except Exception:
        conn.rollback()
    finally:
        release_conn(conn)


_ensure_notes_table()


def _auth_headers():
    token = (request.headers.get("Authorization", "") or "").replace("Bearer ", "").strip()
    return token


# ── GET /api/profile/me ───────────────────────────────────────────────────────

@profile_bp.route("/me", methods=["GET"])
@token_required
def get_me():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.email, u.role, u.created_at,
                       p.prenom, p.nom, p.telephone, p.date_naissance,
                       p.ville, p.niveau, p.filiere_actuelle,
                       p.etablissement, p.annee_scolaire, p.moyenne_generale,
                       p.avatar_url, p.type_bac, p.note_bac,
                       COALESCE(p.show_in_leaderboard, FALSE) AS show_in_leaderboard
                FROM users u
                LEFT JOIN student_profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (g.current_user["id"],),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        return jsonify({
            "id":               str(row[0]),
            "email":            row[1],
            "role":             row[2],
            "created_at":       row[3].isoformat() if row[3] else None,
            "prenom":           row[4] or "",
            "nom":              row[5] or "",
            "telephone":        row[6] or "",
            "date_naissance":   str(row[7]) if row[7] else None,
            "ville":            row[8] or "",
            "niveau":           row[9] or "",
            "filiere_actuelle": row[10] or "",
            "filiere":          row[10] or "",
            "etablissement":    row[11] or "",
            "annee_scolaire":   row[12] or "",
            "moyenne_generale": float(row[13]) if row[13] is not None else None,
            "avatar_url":           row[14] or None,
            "type_bac":             row[15] or "",
            "note_bac":             float(row[16]) if row[16] is not None else None,
            "show_in_leaderboard":  bool(row[17]),
        }), 200

    except Exception as e:
        print("PROFILE ME ERROR:", traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── PUT /api/profile/me ───────────────────────────────────────────────────────

@profile_bp.route("/me", methods=["PUT"])
@token_required
def update_me():
    data = request.get_json(silent=True) or {}

    allowed = [
        "prenom", "nom", "telephone", "date_naissance",
        "ville", "niveau", "filiere_actuelle",
        "etablissement", "annee_scolaire", "moyenne_generale",
        "type_bac", "note_bac", "show_in_leaderboard",
    ]
    fields = {k: data[k] for k in allowed if k in data}

    if not fields:
        return jsonify({"error": "Aucun champ à mettre à jour"}), 400

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values     = list(fields.values()) + [g.current_user["id"]]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE student_profiles SET {set_clause} WHERE user_id = %s",
                values,
            )
            print(f"[PROFILE] update_me  user={g.current_user['id']}  fields={list(fields.keys())}  rowcount={cur.rowcount}", flush=True)

            if cur.rowcount == 0:
                # No student_profiles row yet — insert one with the supplied fields.
                # This covers Google-OAuth users and any edge-case registration gaps.
                ins_cols = "user_id, " + ", ".join(fields.keys())
                ins_phs  = ", ".join(["%s"] * (len(fields) + 1))
                cur.execute(
                    f"INSERT INTO student_profiles ({ins_cols}) VALUES ({ins_phs})",
                    [g.current_user["id"]] + list(fields.values()),
                )
                print(f"[PROFILE] created missing student_profiles row for user={g.current_user['id']}", flush=True)

            conn.commit()
        return jsonify({"message": "Profil mis à jour"}), 200
    except Exception as e:
        conn.rollback()
        print(f"[PROFILE] update_me error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── POST /api/profile/upload-bulletin ────────────────────────────────────────

@profile_bp.route("/upload-bulletin", methods=["POST"])
@token_required
def upload_bulletin():
    file = request.files.get("bulletin")
    if not file or not file.filename:
        return jsonify({"error": "Aucun fichier fourni"}), 400

    if file.mimetype not in ("application/pdf", "application/x-pdf"):
        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        if ext != "pdf":
            return jsonify({"error": "Seuls les fichiers PDF sont acceptés"}), 400

    content = file.read()
    if len(content) > MAX_SIZE:
        return jsonify({"error": "Fichier trop grand (max 10 MB)"}), 400

    user_id = str(g.current_user["id"])

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM bulletins WHERE user_id = %s",
                (user_id,),
            )
            count = cur.fetchone()[0]

        if count >= MAX_BULLETINS:
            release_conn(conn)
            return jsonify({"error": f"Maximum {MAX_BULLETINS} bulletins autorisés"}), 400

        original_name = secure_filename(file.filename or "bulletin.pdf")
        bulletin_id = str(uuid.uuid4())
        stored_name = f"{user_id}_{bulletin_id[:8]}_{original_name}"
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        with open(filepath, "wb") as f:
            f.write(content)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bulletins (id, user_id, original_name, stored_name)
                VALUES (%s, %s, %s, %s)
                """,
                (bulletin_id, user_id, original_name, stored_name),
            )
            conn.commit()

        return jsonify({
            "id":            bulletin_id,
            "original_name": original_name,
            "uploaded_at":   None,
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── GET /api/profile/bulletins ────────────────────────────────────────────────

@profile_bp.route("/bulletins", methods=["GET"])
@token_required
def list_bulletins():
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_name, stored_name, uploaded_at
                FROM bulletins
                WHERE user_id = %s
                ORDER BY uploaded_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

        result = [
            {
                "id":            str(r[0]),
                "original_name": r[1],
                "stored_name":   r[2],
                "uploaded_at":   r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]
        return jsonify({"bulletins": result}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── GET /api/profile/bulletin/<id>/download ───────────────────────────────────

@profile_bp.route("/bulletin/<bulletin_id>/download", methods=["GET"])
@token_required
def download_bulletin(bulletin_id):
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT original_name, stored_name FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Bulletin introuvable"}), 404

        original_name, stored_name = row
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        if not os.path.exists(filepath):
            return jsonify({"error": "Fichier introuvable sur le serveur"}), 404

        with open(filepath, "rb") as f:
            data = f.read()

        return send_file(
            io.BytesIO(data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=original_name,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── DELETE /api/profile/bulletin/<id> ────────────────────────────────────────

@profile_bp.route("/bulletin/<bulletin_id>", methods=["DELETE"])
@token_required
def delete_bulletin(bulletin_id):
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stored_name FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Bulletin introuvable"}), 404

        stored_name = row[0]
        filepath = os.path.join(UPLOAD_DIR, stored_name)

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            conn.commit()

        if os.path.exists(filepath):
            os.remove(filepath)

        return jsonify({"message": "Bulletin supprimé"}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        release_conn(conn)


# ── POST /api/profile/bulletin/extract ───────────────────────────────────────

@profile_bp.route("/bulletin/extract", methods=["POST"])
@token_required
def extract_bulletin_notes():
    data = request.get_json(silent=True) or {}
    bulletin_id = data.get("bulletin_id", "").strip()
    if not bulletin_id:
        return jsonify({"error": "bulletin_id requis"}), 400

    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stored_name FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            row = cur.fetchone()
    finally:
        release_conn(conn)

    if not row:
        return jsonify({"error": "Bulletin introuvable"}), 404

    filepath = os.path.join(UPLOAD_DIR, row[0])
    if not os.path.exists(filepath):
        return jsonify({"error": "Fichier introuvable sur le serveur"}), 404

    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Service OCR non configuré"}), 200

    with open(filepath, "rb") as f:
        raw_bytes = f.read()

    ext = row[0].rsplit(".", 1)[-1].lower() if "." in row[0] else "pdf"

    # Pixtral only accepts images (PNG/JPEG/WebP), not raw PDFs.
    # Convert the first page of any PDF to PNG in-memory using PyMuPDF.
    if ext == "pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            if doc.page_count == 0:
                doc.close()
                return jsonify({"ok": False, "error": "PDF vide — saisis les notes manuellement."}), 200
            page = doc[0]
            # 2× scale → ~150 dpi effective resolution, good for OCR
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            png_bytes = pix.tobytes("png")
            page_count = doc.page_count
            doc.close()
            b64  = base64.b64encode(png_bytes).decode("utf-8")
            mime = "image/png"
            print(f"[OCR] PDF→PNG ok  pages={page_count}  png_size={len(png_bytes)}", flush=True)
        except ImportError:
            return jsonify({"ok": False, "error": "Conversion PDF indisponible — saisis les notes manuellement."}), 200
        except Exception as pdf_exc:
            print(f"[OCR] PDF→PNG error: {pdf_exc}", flush=True)
            return jsonify({"ok": False, "error": "Conversion PDF échouée — saisis les notes manuellement."}), 200
    else:
        b64  = base64.b64encode(raw_bytes).decode("utf-8")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")

    prompt = (
        "Analyse ce bulletin scolaire marocain et extrais les notes. "
        "Retourne UNIQUEMENT un objet JSON valide (sans markdown ni ```) avec cette structure:\n"
        '{"notes":[{"matiere":"Mathématiques","note":14.5,"coefficient":3}],'
        '"moyenne_generale":15.2,"type_bac":"Bac Sciences Maths A"}\n'
        "Règles: note entre 0 et 20, coefficient null si non visible, "
        "moyenne_generale null si non visible, type_bac null si non visible. "
        'Si le document est illisible: {"error":"illisible"}'
    )

    raw = ""
    try:
        resp = _req.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": _PIXTRAL_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                "max_tokens": 2000,
                "temperature": 0.1,
            },
            timeout=60,
        )
        print(f"[OCR] Mistral status={resp.status_code} bulletin={bulletin_id}")
        if not resp.ok:
            print(f"[OCR] Error body: {resp.text[:400]}")
            return jsonify({"ok": False, "error": "Extraction échouée — saisis les notes manuellement."}), 200

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[OCR] Raw (first 300): {raw[:300]}")

        cleaned = _re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = _re.sub(r"\s*```$", "", cleaned.strip())
        parsed = json.loads(cleaned)

        if "error" in parsed:
            return jsonify({"ok": False, "error": "Document illisible — saisis les notes manuellement."}), 200

        valid_notes = []
        for n in parsed.get("notes", []):
            try:
                mat = str(n.get("matiere", "")).strip()[:120]
                if not mat:
                    continue
                note_val = round(float(n["note"]), 2)
                coeff = round(float(n["coefficient"]), 1) if n.get("coefficient") is not None else None
                valid_notes.append({"matiere": mat, "note": note_val, "coefficient": coeff})
            except (ValueError, TypeError, KeyError):
                continue

        moy = None
        if parsed.get("moyenne_generale") is not None:
            try:
                moy = round(float(parsed["moyenne_generale"]), 2)
            except (ValueError, TypeError):
                pass

        return jsonify({
            "ok": True,
            "notes": valid_notes,
            "moyenne_generale": moy,
            "type_bac": parsed.get("type_bac") or None,
        }), 200

    except json.JSONDecodeError:
        print(f"[OCR] JSON parse error. Raw: {raw[:300]}")
        return jsonify({"ok": False, "error": "Analyse impossible — saisis les notes manuellement."}), 200
    except Exception as exc:
        print(f"[OCR] Exception: {exc}")
        return jsonify({"ok": False, "error": "Erreur OCR — saisis les notes manuellement."}), 200


# ── POST /api/profile/bulletin/confirm ───────────────────────────────────────

@profile_bp.route("/bulletin/confirm", methods=["POST"])
@token_required
def confirm_bulletin_notes():
    data = request.get_json(silent=True) or {}
    bulletin_id      = data.get("bulletin_id", "").strip()
    notes            = data.get("notes", [])
    moyenne_generale = data.get("moyenne_generale")
    type_bac_raw     = data.get("type_bac", "") or ""
    type_bac         = str(type_bac_raw).strip()[:120] or None

    if not bulletin_id:
        return jsonify({"error": "bulletin_id requis"}), 400

    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM bulletins WHERE id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )
            if not cur.fetchone():
                return jsonify({"error": "Bulletin introuvable"}), 404

            cur.execute(
                "DELETE FROM bulletin_notes WHERE bulletin_id = %s AND user_id = %s",
                (bulletin_id, user_id),
            )

            saved = 0
            for n in notes:
                mat = str(n.get("matiere", "")).strip()[:120]
                if not mat:
                    continue
                try:
                    note_val = round(float(n["note"]), 2)
                except (ValueError, TypeError, KeyError):
                    continue
                coeff = None
                if n.get("coefficient") is not None:
                    try:
                        coeff = round(float(n["coefficient"]), 1)
                    except (ValueError, TypeError):
                        pass
                cur.execute(
                    "INSERT INTO bulletin_notes (user_id, bulletin_id, matiere, note, coefficient)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (user_id, bulletin_id, mat, note_val, coeff),
                )
                saved += 1

            updates = {}
            if moyenne_generale is not None:
                try:
                    updates["moyenne_generale"] = round(float(moyenne_generale), 2)
                except (ValueError, TypeError):
                    pass
            if type_bac:
                updates["type_bac"] = type_bac

            if updates:
                set_clause = ", ".join(f"{k} = %s" for k in updates)
                cur.execute(
                    f"UPDATE student_profiles SET {set_clause} WHERE user_id = %s",
                    list(updates.values()) + [user_id],
                )

            conn.commit()

        return jsonify({"ok": True, "saved": saved}), 200

    except Exception as exc:
        conn.rollback()
        print(f"[CONFIRM NOTES] Error: {exc}")
        return jsonify({"error": str(exc)}), 500
    finally:
        release_conn(conn)


# ── GET /api/profile/bulletin/<id>/notes ─────────────────────────────────────

@profile_bp.route("/bulletin/<bulletin_id>/notes", methods=["GET"])
@token_required
def get_bulletin_notes(bulletin_id):
    user_id = str(g.current_user["id"])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT matiere, note, coefficient
                   FROM bulletin_notes
                   WHERE bulletin_id = %s AND user_id = %s
                   ORDER BY created_at ASC""",
                (bulletin_id, user_id),
            )
            rows = cur.fetchall()

        return jsonify({
            "notes": [
                {
                    "matiere":     r[0],
                    "note":        float(r[1]),
                    "coefficient": float(r[2]) if r[2] is not None else None,
                }
                for r in rows
            ]
        }), 200

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        release_conn(conn)
