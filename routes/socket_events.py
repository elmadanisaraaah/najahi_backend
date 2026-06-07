from flask import request
from flask_socketio import emit, join_room, leave_room

# In-memory store: { room_id: { "participants": [...], "permissions": {"camera": False, "mic": False}, "timer": {...} } }
rooms = {}

def get_room(room_id):
    if room_id not in rooms:
        rooms[room_id] = {
            "participants": [],
            "permissions": {"camera": False, "mic": False},
            "timer": {
                "is_running": False,
                "time_left": 25 * 60,
                "phase": "focus",
            }
        }
    return rooms[room_id]

def register_socket_events(socketio):

    # ── Join room ──────────────────────────────────────────────────────────────
    @socketio.on("join_room")
    def on_join(data):
        room_id  = data.get("room_id") or data.get("room")
        user_id  = data.get("user_id")
        nom      = data.get("nom") or data.get("name") or "Anonyme"
        is_host  = data.get("is_host", False)

        join_room(room_id)

        room = get_room(room_id)

        # Remove existing entry for same user
        room["participants"] = [
            p for p in room["participants"]
            if p.get("sid") != request.sid and p.get("user_id") != user_id
        ]

        participant = {
            "sid":     request.sid,
            "user_id": user_id,
            "name":    nom,
            "is_host": is_host,
            "camera":  False,
            "mic":     False,
        }
        room["participants"].append(participant)

        # Notify all in room
        emit("participants_update", {
            "participants": room["participants"]
        }, to=room_id)

        # Send current permissions to new joiner
        emit("permissions_update", room["permissions"], to=request.sid)

        # Send current timer state to new joiner
        emit("timer_update", room["timer"], to=request.sid)

        emit("user_joined", {"participant": participant}, to=room_id)

    # ── Leave room ─────────────────────────────────────────────────────────────
    @socketio.on("leave_room")
    def on_leave(data):
        room_id = data.get("room_id") or data.get("room")
        leave_room(room_id)

        room = get_room(room_id)
        room["participants"] = [
            p for p in room["participants"]
            if p.get("sid") != request.sid
        ]

        emit("participants_update", {
            "participants": room["participants"]
        }, to=room_id)

        emit("user_left", {"sid": request.sid}, to=room_id)

    # ── Disconnect ─────────────────────────────────────────────────────────────
    @socketio.on("disconnect")
    def on_disconnect():
        for room_id, room in rooms.items():
            before = len(room["participants"])
            room["participants"] = [
                p for p in room["participants"]
                if p.get("sid") != request.sid
            ]
            if len(room["participants"]) < before:
                emit("participants_update", {
                    "participants": room["participants"]
                }, to=room_id)
                emit("user_left", {"sid": request.sid}, to=room_id)

    # ── Chat message ───────────────────────────────────────────────────────────
    @socketio.on("room_message")
    def on_message(data):
        room_id = data.get("room_id") or data.get("room")
        emit("room_message", {
            "sender_id":   data.get("sender_id"),
            "sender_name": data.get("sender_name") or data.get("nom") or "Anonyme",
            "text":        data.get("text") or data.get("message") or "",
            "timestamp":   data.get("timestamp"),
        }, to=room_id)

    # ── Timer controls ─────────────────────────────────────────────────────────
    @socketio.on("timer_control")
    def on_timer_control(data):
        room_id    = data.get("room_id")
        action     = data.get("action")   # "start" | "pause" | "reset" | "phase"
        phase      = data.get("phase")    # "focus" | "break" | "longBreak"
        time_left  = data.get("time_left")

        room = get_room(room_id)
        timer = room["timer"]

        if action == "start":
            timer["is_running"] = True
        elif action == "pause":
            timer["is_running"] = False
        elif action == "reset":
            timer["is_running"] = False
            timer["time_left"]  = time_left or 25 * 60
        elif action == "phase":
            timer["is_running"] = False
            timer["phase"]      = phase or "focus"
            DURATIONS = {"focus": 25*60, "break": 5*60, "longBreak": 15*60}
            timer["time_left"]  = DURATIONS.get(phase, 25*60)

        if time_left is not None and action != "phase":
            timer["time_left"] = time_left

        emit("timer_update", timer, to=room_id)

    # ── Permissions (host only) ────────────────────────────────────────────────
    @socketio.on("update_permissions")
    def on_update_permissions(data):
        room_id = data.get("room_id")
        room    = get_room(room_id)

        new_perms = {
            "camera": data.get("camera", False),
            "mic":    data.get("mic",    False),
        }
        room["permissions"] = new_perms

        # If permission turned off, force disable for all participants
        for p in room["participants"]:
            if not new_perms["camera"]: p["camera"] = False
            if not new_perms["mic"]:    p["mic"]    = False

        emit("permissions_update", new_perms, to=room_id)
        emit("participants_update", {"participants": room["participants"]}, to=room_id)

    # ── Participant media state update ─────────────────────────────────────────
    @socketio.on("media_state")
    def on_media_state(data):
        room_id = data.get("room_id")
        room    = get_room(room_id)

        for p in room["participants"]:
            if p.get("sid") == request.sid:
                if "camera" in data: p["camera"] = data["camera"]
                if "mic"    in data: p["mic"]    = data["mic"]
                break

        emit("participants_update", {
            "participants": room["participants"]
        }, to=room_id)

    # ── WebRTC signaling ───────────────────────────────────────────────────────
    @socketio.on("offer")
    def on_offer(data):
        emit("offer", data, to=data.get("target"))

    @socketio.on("answer")
    def on_answer(data):
        emit("answer", data, to=data.get("target"))

    @socketio.on("ice_candidate")
    def on_ice(data):
        emit("ice_candidate", data, to=data.get("target"))

    # ── Legacy chat support ────────────────────────────────────────────────────
    @socketio.on("chat_message")
    def on_chat_legacy(data):
        room_id = data.get("room_id") or data.get("room")
        emit("room_message", {
            "sender_name": data.get("nom") or "Anonyme",
            "text":        data.get("message") or "",
            "timestamp":   None,
        }, to=room_id)