# app.py
import os
from flask import Flask, request, jsonify
from worker import solve_quiz_task

app = Flask(__name__)

EXPECTED_SECRET = os.getenv("TDS_SECRET")

@app.route("/", methods=["GET"])
def home():
    return "TDS Project 2 endpoint running!"

@app.route("/quiz", methods=["POST"])
def quiz():
    # validate JSON
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    # required fields
    email = data.get("email")
    secret = data.get("secret")
    url = data.get("url")

    if EXPECTED_SECRET is None:
        return jsonify({"error": "Server misconfigured: TDS_SECRET not set"}), 500

    if secret != EXPECTED_SECRET:
        return jsonify({"error": "Forbidden"}), 403

    if not email or not url:
        return jsonify({"error": "Missing email or url"}), 400

    # Run the solver (synchronous). For heavy loads use background worker.
    try:
        result = solve_quiz_task(email=email, secret=secret, url=url)
        return jsonify({"status": "done", "result": result}), 200
    except Exception as e:
        # For local debugging show error; hide details in production
        return jsonify({"error": str(e)}), 500
    
print("DEBUG: Starting app.py")
print("DEBUG: EXPECTED_SECRET=", EXPECTED_SECRET)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
