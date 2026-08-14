# Weekend Creative Challenge: Wanderwise

**Tag:** #creative-expression

## Vision & what the app does

Most trip planners stop at the destination. **Wanderwise** plans the *journey*. You type where
you're starting and where you're going, pick a date and a trip style (family, couples, solo,
friends, or slow travel), and an AI agent composes the whole day for you: a mapped route, real
weather at both ends, breakfast / lunch / dinner stops spaced along the drive, family-friendly
hotels, kids' play areas, an hour-by-hour timeline, a packing list, and a layered safety check
covering road, natural/seasonal, and area-level risks.

The creative output is the **itinerary itself** — a narrated, opinionated travel plan generated
fresh for your exact route, rendered as an interactive dashboard with a live map, weather cards,
a vertical timeline, and rated recommendation cards. Two identical inputs a month apart produce
different, weather-aware plans, because the agent reasons over live conditions each time.

## How you built it

I split the app into two honest layers so the useful parts are always trustworthy:

- A **ground-truth layer** that is fully deterministic: geocoding and real weather come from the
  free Open-Meteo API (no key required), and driving distance/time come from a haversine
  estimator. The map and weather never depend on the model.
- An **AI agent layer**: Amazon Bedrock's **Nova Lite** model running a **Converse tool-use
  loop**. The Lambda hands the agent the already-resolved facts, and the agent can call the same
  `geocode` / `weather` / `route` tools for extra lookups before emitting a strict JSON itinerary.

The biggest design decision was *not* to let the model invent weather or coordinates. Early on I
had Nova produce everything, and the plans were charming but the temperatures were fiction. Moving
weather, geocoding and distance into real tools — and feeding those results back into the prompt —
fixed accuracy while keeping the creative narrative.

The main challenge was getting reliable JSON out of a chatty model. I solved it by pinning an
exact schema in the system prompt, stripping code fences, extracting the outer `{...}`, and adding
one low-temperature corrective retry if parsing fails. A second gotcha: Bedrock needs **two** gates
— an IAM `bedrock:InvokeModel` permission *and* console **Model access** enabled for Nova Lite.
The whole thing is one Lambda behind a Function URL: `GET` serves the UI, `POST` runs the agent —
no API Gateway, no database, no keys.

## AWS services used / architecture overview

- **AWS Lambda** (Python 3.12) behind a **Lambda Function URL** — serves the single-page UI and
  the planning API from one endpoint.
- **Amazon Bedrock — Nova Lite** (Converse API, cross-region inference profile
  `us.amazon.nova-lite-v1:0`) — the trip-planning agent.
- **AWS SAM** for infrastructure-as-code, with an **IAM** role scoped least-privilege to invoke
  Nova Lite only.
- External free data: **Open-Meteo** geocoding + forecast. Map tiles via OpenStreetMap/Leaflet.

Flow: Browser → Lambda Function URL → (deterministic geocode/weather/route) + (Bedrock Nova agent
loop) → a response combining ground-truth data with the AI plan → rendered dashboard.

*(Architecture diagram included in the repo.)*

## What I learned

- **Agents are only as trustworthy as their tools.** Grounding weather, location and distance in
  real APIs turned a cute demo into something genuinely useful — and taught me where to draw the
  line between "let the model reason" and "give the model facts."
- **Structured output is a design problem, not a prompt trick.** A pinned schema plus defensive
  parsing and a corrective retry beat hoping for clean JSON.
- **A Function URL + one Lambda is a fantastic weekend stack.** No API Gateway, no database, and
  the whole app fits comfortably in Free Tier.

## Link to app / repo

Live app: *(paste your `AppUrl` from `sam deploy` here)*
Source: *(paste your public GitHub repo here)*
