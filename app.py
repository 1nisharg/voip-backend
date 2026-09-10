import json
import os
import uuid
from datetime import timedelta

from flask import Flask, jsonify, request
from livekit import api


app = Flask(__name__)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]

# IMPORTANT:
# This must be the agent_name of your already-hosted Sania agent.
AGENT_NAME = os.getenv(
    "LIVEKIT_AGENT_NAME",
    "aarna-sania-laptop-test",
)

TOKEN_TTL_MINUTES = int(
    os.getenv("LIVEKIT_TOKEN_TTL_MINUTES", "30")
)


# ============================================================
# CORS
# ============================================================

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )
    return response


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def home():
    return jsonify(
        {
            "service": "Sania LiveKit Credential Server",
            "status": "running",
        }
    )


@app.get("/health")
def health():
    return jsonify({"ok": True})


# ============================================================
# GENERATE LIVEKIT CREDENTIALS
# ============================================================

@app.route("/api/generate-token", methods=["POST", "OPTIONS"])
async def generate_token():

    # Handle browser CORS preflight
    if request.method == "OPTIONS":
        return "", 204

    try:
        body = request.get_json(silent=True) or {}

        # ----------------------------------------------------
        # Lead information
        # ----------------------------------------------------

        partner_name = str(
            body.get("partner_name", "")
        ).strip()

        contact_name = str(
            body.get("contact_name", "")
        ).strip()

        category = str(
            body.get("category", "")
        ).strip()

        company_synopsis = str(
            body.get("company_synopsis", "")
        ).strip()

        digitisation = str(
            body.get("digitisation", "")
        ).strip()

        # ----------------------------------------------------
        # Generate unique room and participant identity
        # ----------------------------------------------------

        room_name = (
            f"sania-demo-{uuid.uuid4().hex[:12]}"
        )

        participant_identity = (
            f"lead-{uuid.uuid4().hex[:12]}"
        )

        # ----------------------------------------------------
        # Metadata sent to Sania
        # ----------------------------------------------------

        metadata = json.dumps(
            {
                "partner_name": partner_name,
                "contact_name": contact_name,
                "category": category,
                "company_synopsis": company_synopsis,
                "digitisation": digitisation,
            }
        )

        # ----------------------------------------------------
        # Connect to LiveKit
        # ----------------------------------------------------

        lk = api.LiveKitAPI(
            LIVEKIT_URL,
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )

        try:

            # ------------------------------------------------
            # Dispatch Sania into this room
            # ------------------------------------------------

            dispatch = await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME,
                    room=room_name,
                    metadata=metadata,
                )
            )

            # ------------------------------------------------
            # Generate browser access token
            # ------------------------------------------------

            token = (
                api.AccessToken(
                    LIVEKIT_API_KEY,
                    LIVEKIT_API_SECRET,
                )
                .with_identity(participant_identity)
                .with_name(
                    contact_name
                    or participant_identity
                )
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=room_name,
                        can_publish=True,
                        can_subscribe=True,
                    )
                )
                .with_ttl(
                    timedelta(
                        minutes=TOKEN_TTL_MINUTES
                    )
                )
                .to_jwt()
            )

            # ------------------------------------------------
            # Return credentials to browser
            # ------------------------------------------------

            return jsonify(
                {
                    "ok": True,
                    "serverUrl": LIVEKIT_URL,
                    "token": token,
                    "room": room_name,
                    "participantIdentity": participant_identity,
                    "expiresInMinutes": TOKEN_TTL_MINUTES,
                    "agentName": AGENT_NAME,
                    "dispatchId": getattr(
                        dispatch,
                        "id",
                        None,
                    ),
                }
            )

        finally:
            await lk.aclose()

    except Exception as e:

        print(
            "ERROR generating credentials:",
            repr(e),
            flush=True,
        )

        return jsonify(
            {
                "ok": False,
                "error": str(e),
            }
        ), 500


# ============================================================
# LOCAL DEVELOPMENT ONLY
# ============================================================

if __name__ == "__main__":
    port = int(
        os.getenv("PORT", "8000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
