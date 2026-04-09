"""Journey API — personalized step-by-step guides and deadlines.

Serves the Home screen of the Portiva iOS app with journey steps,
deadlines, and progress data based on user's country and situation.

For MVP: reads from config/journeys.json (static data).
Phase 2: dynamic steps based on user's actual progress stored in DB.
"""

import json
import structlog
from datetime import datetime, timedelta
from fastapi import APIRouter
from pydantic import BaseModel

logger = structlog.get_logger()
router = APIRouter()

# Load journey data once at import time
_JOURNEYS: dict = {}
try:
    with open("config/journeys.json", "r", encoding="utf-8") as f:
        _JOURNEYS = json.load(f)
    logger.info("Journey data loaded", countries=len(_JOURNEYS))
except FileNotFoundError:
    logger.warning("config/journeys.json not found — journey API will return empty data")


# ── Response models ───────────────────────────────────────────────

class JourneyStep(BaseModel):
    id: str
    title: str
    description: str
    status: str  # "completed" | "active" | "upcoming"
    documents_needed: int
    estimated_time: str
    cost: str
    category: str
    icon_name: str  # SF Symbol name for iOS


class Deadline(BaseModel):
    id: str
    title: str
    description: str
    date: str  # ISO date string
    category: str  # "tax" | "registration" | "insurance" | "permit"


class JourneyResponse(BaseModel):
    country: str
    situation: str
    steps: list[JourneyStep]
    deadlines: list[Deadline]
    total_steps: int
    completed_steps: int


# ── Endpoint ──────────────────────────────────────────────────────

@router.get("/{country}/{situation}", response_model=JourneyResponse)
async def get_journey(country: str, situation: str):
    """Get personalized journey steps and deadlines.

    Args:
        country: ISO country code (e.g. "de", "nl")
        situation: User situation (e.g. "freelancer", "employee")

    Returns journey steps with status, deadlines with dates.
    Falls back to generic steps if country/situation not configured.
    """
    # Try exact match, then country-level default, then global default
    country_data = _JOURNEYS.get(country, _JOURNEYS.get("default", {}))
    journey_data = country_data.get(situation, country_data.get("default", {}))

    steps_raw = journey_data.get("steps", [])
    deadlines_raw = journey_data.get("deadlines", [])

    # Convert steps
    steps = []
    for s in steps_raw:
        steps.append(JourneyStep(
            id=s["id"],
            title=s["title"],
            description=s.get("description", ""),
            status=s.get("status", "upcoming"),
            documents_needed=s.get("documents_needed", 0),
            estimated_time=s.get("estimated_time", ""),
            cost=s.get("cost", "Free"),
            category=s.get("category", "other"),
            icon_name=s.get("icon_name", "doc.text"),
        ))

    # Convert deadlines — resolve relative dates
    deadlines = []
    today = datetime.now()
    for d in deadlines_raw:
        date_str = d.get("date", "")
        if date_str.startswith("+"):
            # Relative date: "+30d" means 30 days from now
            days = int(date_str.replace("+", "").replace("d", ""))
            date_str = (today + timedelta(days=days)).strftime("%Y-%m-%d")

        deadlines.append(Deadline(
            id=d["id"],
            title=d["title"],
            description=d.get("description", ""),
            date=date_str,
            category=d.get("category", "other"),
        ))

    completed = sum(1 for s in steps if s.status == "completed")

    logger.info("Journey served", country=country, situation=situation,
                steps=len(steps), deadlines=len(deadlines))

    return JourneyResponse(
        country=country,
        situation=situation,
        steps=steps,
        deadlines=deadlines,
        total_steps=len(steps),
        completed_steps=completed,
    )
