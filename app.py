import asyncio
import json
import os
import uuid
from datetime import timedelta

import httpx
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
# This must match the agent_name used by the hosted Sania worker.
AGENT_NAME = os.getenv(
    "LIVEKIT_AGENT_NAME",
    "aarna-sania-laptop-test",
)

TOKEN_TTL_MINUTES = int(
    os.getenv("LIVEKIT_TOKEN_TTL_MINUTES", "30")
)

# Public Render URL of the Sania agent Web Service.
# A request to this URL wakes a sleeping Free Render instance before
# we create the LiveKit dispatch.
SANIA_AGENT_URL = os.getenv("SANIA_AGENT_URL", "").rstrip("/")

SANIA_WAKE_TIMEOUT_SECONDS = int(
    os.getenv("SANIA_WAKE_TIMEOUT_SECONDS", "90")
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
# WAKE SANIA AGENT
# ============================================================

async def wake_sania_agent() -> None:
    """
    Wake the Free Render Sania Web Service before dispatching a job.

    Render Free Web Services sleep after inactivity. Calling the
    Sania worker's built-in LiveKit health endpoint wakes it.
    """

    if not SANIA_AGENT_URL:
        raise RuntimeError(
            "SANIA_AGENT_URL is not configured on the token server."
        )

    health_url = f"{SANIA_AGENT_URL}/"

    deadline = (
        asyncio.get_running_loop().time()
        + SANIA_WAKE_TIMEOUT_SECONDS
    )

    last_error = "unknown error"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            10.0,
            connect=10.0,
        )
    ) as client:

        while asyncio.get_running_loop().time() < deadline:

            try:
                response = await client.get(health_url)

                if 200 <= response.status_code < 300:
                    return

                last_error = (
                    "Sania health endpoint returned "
                    f"HTTP {response.status_code}"
                )

            except Exception as exc:
                last_error = repr(exc)

            await asyncio.sleep(3)

    raise RuntimeError(
        "Sania agent did not become reachable within "
        f"{SANIA_WAKE_TIMEOUT_SECONDS} seconds: {last_error}"
    )


# ============================================================
# GENERATE LIVEKIT CREDENTIALS
# ============================================================

@app.route(
    "/api/generate-token",
    methods=["POST", "OPTIONS"],
)
async def generate_token():

    # Handle browser CORS preflight
    if request.method == "OPTIONS":
        return "", 204

    try:

        body = request.get_json(
            silent=True
        ) or {}

        # ----------------------------------------------------
        # Lead information
        # ----------------------------------------------------

        partner_name = str(
            body.get(
                "partner_name",
                "",
            )
        ).strip()

        contact_name = str(
            body.get(
                "contact_name",
                "",
            )
        ).strip()

        category = str(
            body.get(
                "category",
                "",
            )
        ).strip()

        company_synopsis = str(
            body.get(
                "company_synopsis",
                "",
            )
        ).strip()

        digitisation = str(
            body.get(
                "digitisation",
                "",
            )
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
        # Wake Sania's Render Free Web Service
        # ----------------------------------------------------

        await wake_sania_agent()

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

            dispatch = (
                await lk.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name=AGENT_NAME,
                        room=room_name,
                        metadata=metadata,
                    )
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
                .with_identity(
                    participant_identity
                )
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
                    "participantIdentity": (
                        participant_identity
                    ),
                    "expiresInMinutes": (
                        TOKEN_TTL_MINUTES
                    ),
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
        os.getenv(
            "PORT",
            "8000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
