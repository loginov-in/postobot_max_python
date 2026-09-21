import hashlib
import hmac
import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MAX_BOT_TOKEN

app = Flask(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "requests.json"


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"requests": [], "next_id": 1}


def save_data(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def validate_init_data(init_data: str) -> dict | None:
    """Проверяет подпись initData по алгоритму MAX и возвращает user из неё."""
    if not MAX_BOT_TOKEN or not init_data:
        return None

    pairs = []
    for item in init_data.split("&"):
        if "=" in item:
            key, _, value = item.partition("=")
            pairs.append([key, value])

    if sum(1 for k, _ in pairs if k == "hash") != 1:
        return None

    original_hash = next(v for k, v in pairs if k == "hash")

    for pair in pairs:
        if pair[0] == "hash":
            continue
        pair[1] = urllib.parse.unquote(pair[1])

    launch_params = "\n".join(
        f"{k}={v}" for k, v in sorted((p for p in pairs if p[0] != "hash"), key=lambda p: p[0])
    )

    secret_key = hmac.new(b"WebAppData", MAX_BOT_TOKEN.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, original_hash):
        return None

    user_raw = next((v for k, v in pairs if k == "user"), None)
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        return None
    return user if isinstance(user, dict) else None


def parse_user() -> dict:
    init_data = request.args.get("initData")
    if init_data:
        user = validate_init_data(init_data)
        if user:
            return user
    raw = request.args.get("user")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


@app.route("/apps")
def apps():
    user = parse_user()
    records = load_data().get("requests", [])
    if user.get("id"):
        records = [r for r in records if r.get("user_id") == user["id"]]
    return render_template("apps.html", user=user, requests=records)


@app.route("/api/auth", methods=["POST"])
def auth():
    payload = request.get_json(silent=True) or {}
    user = validate_init_data(payload.get("initData") or "")
    if not user or not user.get("id"):
        return jsonify({"error": "Не удалось подтвердить данные пользователя."}), 401
    requests_list = [
        r for r in load_data().get("requests", []) if r.get("user_id") == user["id"]
    ]
    return jsonify({"user": user, "requests": requests_list})


@app.route("/api/request", methods=["POST"])
def create_request():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    if kind not in ("заявка", "предложение"):
        return jsonify({"error": "Неизвестный тип обращения."}), 400

    comment = (payload.get("comment") or "").strip()
    if not comment:
        return jsonify({"error": "Напишите комментарий."}), 400

    u = payload.get("user") or {}
    validated = validate_init_data(payload.get("initData") or "")
    if validated:
        u = validated

    try:
        user_id = int(u.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0

    data = load_data()
    record = {
        "id": data["next_id"],
        "user_id": user_id,
        "user": " ".join(
            filter(None, [str(u.get("first_name") or ""), str(u.get("last_name") or "")])
        ).strip()
        or "Пользователь",
        "kind": kind,
        "comment": comment,
        "photo_url": None,
        "photo_token": None,
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "status": "active",
    }
    data["requests"].append(record)
    data["next_id"] += 1
    save_data(data)
    return jsonify({"ok": True, "request": record})


if __name__ == "__main__":
    app.run(debug=True, port=1309)