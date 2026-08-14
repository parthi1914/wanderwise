"""
Wanderwise - AI road-trip concierge.

One Lambda behind a Function URL:
  GET  /  -> serves the single-page UI (index.html)
  POST /  -> runs the trip-planning agent and returns JSON

Ground-truth layer (deterministic, always present):
  - geocoding + weather via Open-Meteo (free, no API key)
  - route distance/time via a haversine estimator
AI layer (Amazon Bedrock, Nova Lite via the Converse API):
  - a real tool-use loop that composes the itinerary, stops, hotels,
    kids' play areas, timing and layered safety checks as strict JSON.
"""

import os
import json
import math
import base64
import datetime
import urllib.parse
import urllib.request

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-lite-v1:0")
HTTP_TIMEOUT = 8

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_HTML = None


# --------------------------------------------------------------------------- #
# Tiny HTTP helper (urllib -> no extra dependencies in the Lambda bundle)
# --------------------------------------------------------------------------- #
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Wanderwise/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Tools (these are what the agent can call, and what we also call directly
# to guarantee a reliable ground-truth layer for the map + weather)
# --------------------------------------------------------------------------- #
def tool_geocode(name):
    q = urllib.parse.urlencode(
        {"name": name, "count": 1, "language": "en", "format": "json"}
    )
    data = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    results = data.get("results") or []
    if not results:
        return {"found": False, "query": name}
    r = results[0]
    return {
        "found": True,
        "name": r.get("name"),
        "admin1": r.get("admin1"),
        "country": r.get("country"),
        "lat": r.get("latitude"),
        "lon": r.get("longitude"),
    }


_WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers",
    81: "Showers", 82: "Violent showers", 85: "Snow showers",
    86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}


def tool_weather(lat, lon, date):
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,wind_speed_10m_max",
        "timezone": "auto",
        "start_date": date,
        "end_date": date,
    }
    data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    d = data.get("daily") or {}
    if not d.get("time"):
        return {"available": False}
    code = d["weathercode"][0]
    return {
        "available": True,
        "date": d["time"][0],
        "summary": _WMO.get(code, "Unknown"),
        "code": code,
        "temp_max_c": d["temperature_2m_max"][0],
        "temp_min_c": d["temperature_2m_min"][0],
        "rain_chance_pct": d["precipitation_probability_max"][0],
        "wind_max_kmh": d["wind_speed_10m_max"][0],
    }


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def tool_route(from_lat, from_lon, to_lat, to_lon):
    straight = _haversine(from_lat, from_lon, to_lat, to_lon)
    driving = straight * 1.25          # rough road vs. straight-line factor
    hours = driving / 72.0             # ~72 km/h effective incl. minor slowdowns
    return {
        "straight_km": round(straight, 1),
        "driving_km": round(driving, 1),
        "drive_hours": round(hours, 1),
    }


def _dispatch(name, inp):
    if name == "geocode":
        return tool_geocode(inp["name"])
    if name == "weather":
        return tool_weather(inp["lat"], inp["lon"], inp["date"])
    if name == "route":
        return tool_route(inp["from_lat"], inp["from_lon"], inp["to_lat"], inp["to_lon"])
    return {"error": f"unknown tool: {name}"}


TOOL_SPECS = [
    {"toolSpec": {
        "name": "geocode",
        "description": "Resolve a place name to coordinates + country.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]}}}},
    {"toolSpec": {
        "name": "weather",
        "description": "Daily weather forecast for coordinates on a date (YYYY-MM-DD).",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "date": {"type": "string"}},
            "required": ["lat", "lon", "date"]}}}},
    {"toolSpec": {
        "name": "route",
        "description": "Estimate driving distance (km) and drive time (hours) between two coordinates.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "from_lat": {"type": "number"}, "from_lon": {"type": "number"},
                "to_lat": {"type": "number"}, "to_lon": {"type": "number"}},
            "required": ["from_lat", "from_lon", "to_lat", "to_lon"]}}}},
]


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
_PLAN_SCHEMA = """{
  "headline": "short, evocative one-line trip title",
  "overview": "2-3 sentence summary of the journey and the vibe",
  "timing": {"suggested_start": "07:30", "estimated_arrival": "HH:MM",
             "recommended_stay": "e.g. 2-3 nights"},
  "route": {"summary": "1-2 sentences on the recommended route and why",
            "major_roads": ["road/highway names"],
            "waypoints": [{"name": "town/landmark", "reason": "why stop or pass here"}]},
  "meals": {
    "breakfast": {"place": "", "town": "", "cuisine": "", "rating": 4.5,
                  "approx_time": "08:00", "why": "one short sentence"},
    "lunch":     {"place": "", "town": "", "cuisine": "", "rating": 4.3,
                  "approx_time": "12:30", "why": ""},
    "dinner":    {"place": "", "town": "", "cuisine": "", "rating": 4.6,
                  "approx_time": "19:30", "why": ""}
  },
  "rest_breaks": [{"place": "", "town": "", "approx_time": "", "note": ""}],
  "hotels": [{"name": "", "area": "", "price_band": "$$", "rating": 4.4,
              "family_friendly": true, "why": ""}],
  "kids_play_areas": [{"name": "", "type": "park/museum/beach/etc", "note": ""}],
  "safety": {"road": ["driving/route cautions"],
             "natural": ["terrain, seasonal, weather-driven risks"],
             "area": ["neighbourhood / general area-safety notes"],
             "overall": "one-line overall assessment"},
  "packing": ["4-6 short, trip-specific items"]
}"""

_SYSTEM = (
    "You are Wanderwise, an expert AI road-trip concierge for families and "
    "travellers. You receive an origin, destination, travel date and trip style, "
    "plus already-resolved facts (coordinates, real weather for both ends, and an "
    "estimated route). You may call the geocode / weather / route tools for extra "
    "lookups (for example weather at a waypoint). When finished, respond with ONE "
    "JSON object ONLY - no prose, no markdown fences - matching EXACTLY this schema:\n"
    + _PLAN_SCHEMA +
    "\n\nRules:\n"
    "- Base all timing on the estimated drive_hours; pick a sensible start time and "
    "space breakfast/lunch/dinner sensibly across the journey.\n"
    "- Recommend real, well-known towns/areas along a plausible route between the two "
    "points; ratings are your best 1-5 estimate.\n"
    "- Safety MUST cover road, natural (terrain/seasonal/weather) and area-safety.\n"
    "- If the trip style mentions family/kids, bias hotels and stops to be "
    "family-friendly and always fill kids_play_areas.\n"
    "- Keep every 'why'/'note'/'reason' to one short sentence. Be practical."
)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON object found in model output")


def _agent_plan(facts, style, date):
    user = (
        "Plan this road trip.\n\nKNOWN FACTS (already resolved via tools):\n"
        + json.dumps(facts, indent=2)
        + f"\n\nTravel date: {date}\nTrip style: {style}\n\n"
        "You may call tools for additional lookups. When done, output ONLY the JSON "
        "object described in the system prompt."
    )
    messages = [{"role": "user", "content": [{"text": user}]}]
    final_text = None

    for _ in range(5):  # bounded tool-use loop
        resp = _bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": _SYSTEM}],
            messages=messages,
            toolConfig={"tools": TOOL_SPECS},
            inferenceConfig={"maxTokens": 4000, "temperature": 0.5, "topP": 0.9},
        )
        msg = resp["output"]["message"]
        messages.append(msg)

        if resp.get("stopReason") == "tool_use":
            results = []
            for block in msg["content"]:
                if "toolUse" in block:
                    tu = block["toolUse"]
                    try:
                        out = _dispatch(tu["name"], tu["input"])
                    except Exception as exc:  # tool failure -> tell the model
                        out = {"error": str(exc)}
                    results.append({"toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"json": out}]}})
            messages.append({"role": "user", "content": results})
            continue

        final_text = "".join(b.get("text", "") for b in msg["content"] if "text" in b)
        break

    if not final_text:
        raise RuntimeError("agent did not finalize a plan")

    try:
        return _extract_json(final_text)
    except Exception:
        # one corrective retry, no tools, low temperature
        messages.append({"role": "user", "content": [
            {"text": "Return ONLY the JSON object, no prose or code fences."}]})
        resp = _bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": _SYSTEM}],
            messages=messages,
            inferenceConfig={"maxTokens": 4000, "temperature": 0.2},
        )
        text = "".join(b.get("text", "") for b in
                       resp["output"]["message"]["content"] if "text" in b)
        return _extract_json(text)


def run_agent(origin, dest, date, style):
    o = tool_geocode(origin)
    if not o.get("found"):
        return {"ok": False, "error": f"Could not find a location called '{origin}'."}
    d = tool_geocode(dest)
    if not d.get("found"):
        return {"ok": False, "error": f"Could not find a location called '{dest}'."}

    wo = tool_weather(o["lat"], o["lon"], date)
    wd = tool_weather(d["lat"], d["lon"], date)
    rt = tool_route(o["lat"], o["lon"], d["lat"], d["lon"])

    facts = {
        "origin": {"name": o["name"], "country": o.get("country"),
                   "lat": o["lat"], "lon": o["lon"], "weather": wo},
        "destination": {"name": d["name"], "country": d.get("country"),
                        "lat": d["lat"], "lon": d["lon"], "weather": wd},
        "route": rt,
    }
    plan = _agent_plan(facts, style, date)

    return {
        "ok": True,
        "date": date,
        "data": {
            "origin": facts["origin"],
            "destination": facts["destination"],
            "route": rt,
            "weather": {"origin": wo, "destination": wd},
        },
        "plan": plan,
    }


# --------------------------------------------------------------------------- #
# HTTP plumbing (Lambda Function URL, payload format 2.0)
# --------------------------------------------------------------------------- #
def _clamp_date(s):
    today = datetime.date.today()
    try:
        dt = datetime.date.fromisoformat(s) if s else today
    except Exception:
        dt = today
    lo, hi = today, today + datetime.timedelta(days=15)  # Open-Meteo forecast window
    return min(max(dt, lo), hi).isoformat()


def _json(code, obj):
    return {"statusCode": code,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(obj)}


def _html():
    global _HTML
    if _HTML is None:
        with open(os.path.join(os.path.dirname(__file__), "index.html"),
                  encoding="utf-8") as fh:
            _HTML = fh.read()
    return {"statusCode": 200,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": _HTML}


def handler(event, context):
    method = (event.get("requestContext", {})
              .get("http", {}).get("method", "GET")).upper()

    if method == "GET":
        return _html()
    if method != "POST":
        return _json(405, {"ok": False, "error": "method not allowed"})

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        req = json.loads(body)
    except Exception:
        return _json(400, {"ok": False, "error": "invalid JSON body"})

    origin = (req.get("from") or "").strip()
    dest = (req.get("to") or "").strip()
    if not origin or not dest:
        return _json(400, {"ok": False, "error": "Please provide both 'from' and 'to'."})
    date = _clamp_date(req.get("date"))
    style = (req.get("style") or "family road trip").strip()

    try:
        result = run_agent(origin, dest, date, style)
        return _json(200 if result.get("ok") else 422, result)
    except Exception as exc:
        return _json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
