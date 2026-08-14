import json, types, sys
import app

# ---- stub the network + bedrock so we can test logic offline ----
def fake_get_json(url):
    if "geocoding-api" in url:
        if "Bengaluru" in url:
            return {"results":[{"name":"Bengaluru","admin1":"Karnataka","country":"India","latitude":12.97,"longitude":77.59}]}
        return {"results":[{"name":"Ooty","admin1":"Tamil Nadu","country":"India","latitude":11.41,"longitude":76.69}]}
    if "forecast" in url:
        return {"daily":{"time":["2026-08-15"],"weathercode":[61],"temperature_2m_max":[24.5],
                "temperature_2m_min":[16.1],"precipitation_probability_max":[70],"wind_speed_10m_max":[18.3]}}
    raise RuntimeError("unexpected url "+url)
app._get_json = fake_get_json

PLAN = {"headline":"Hills & filter coffee","overview":"A scenic climb into the Nilgiris.",
 "timing":{"suggested_start":"06:30","estimated_arrival":"13:00","recommended_stay":"2-3 nights"},
 "route":{"summary":"Via Mysuru and the Kallar ghat.","major_roads":["NH948","NH181"],
   "waypoints":[{"name":"Mysuru","reason":"coffee + palace"},{"name":"Bandipur","reason":"forest drive"}]},
 "meals":{"breakfast":{"place":"CTR","town":"Bengaluru","cuisine":"South Indian","rating":4.6,"approx_time":"07:00","why":"Legendary dosa."},
   "lunch":{"place":"RRR","town":"Mysuru","cuisine":"Andhra","rating":4.3,"approx_time":"12:00","why":"Hearty thali."},
   "dinner":{"place":"Willy's","town":"Ooty","cuisine":"Continental","rating":4.4,"approx_time":"19:30","why":"Cosy hill dinner."}},
 "rest_breaks":[{"place":"Bandipur viewpoint","town":"Bandipur","approx_time":"10:30","note":"Stretch + photos."}],
 "hotels":[{"name":"Sterling Ooty","area":"Fern Hill","price_band":"$$","rating":4.2,"family_friendly":True,"why":"Gardens + kids' activities."}],
 "kids_play_areas":[{"name":"Ooty Boat House","type":"lake/park","note":"Pedal boats and pony rides."}],
 "safety":{"road":["Ghat has sharp bends — drive in daylight."],"natural":["Wet-season fog reduces visibility."],
   "area":["Tourist town, generally safe; watch valuables at viewpoints."],"overall":"Low risk with daytime driving."},
 "packing":["Light woollens","Rain jacket","Motion-sickness tablets","Power bank"]}

class FakeBedrock:
    def converse(self, **kw):
        return {"stopReason":"end_turn","output":{"message":{"role":"assistant",
                "content":[{"text": json.dumps(PLAN)}]}}}
app._bedrock = FakeBedrock()

# 1) run_agent end to end
res = app.run_agent("Bengaluru","Ooty","2026-08-15","family road trip with kids")
assert res["ok"], res
assert res["data"]["origin"]["name"]=="Bengaluru"
assert res["data"]["weather"]["destination"]["summary"]=="Light rain"
assert res["data"]["route"]["driving_km"] > 0
assert res["plan"]["headline"]=="Hills & filter coffee"
print("run_agent driving_km =", res["data"]["route"]["driving_km"], "drive_h =", res["data"]["route"]["drive_hours"])

# 2) GET route serves HTML
g = app.handler({"requestContext":{"http":{"method":"GET"}}}, None)
assert g["statusCode"]==200 and "Wanderwise" in g["body"] and "leaflet" in g["body"]
print("GET html bytes =", len(g["body"]))

# 3) POST route returns JSON envelope
p = app.handler({"requestContext":{"http":{"method":"POST"}},
                 "body": json.dumps({"from":"Bengaluru","to":"Ooty","date":"2026-08-15"})}, None)
body = json.loads(p["body"])
assert p["statusCode"]==200 and body["ok"] and body["plan"]["safety"]["overall"]
print("POST status =", p["statusCode"], "| ok =", body["ok"])

# 4) validation: missing field
b = app.handler({"requestContext":{"http":{"method":"POST"}},"body": json.dumps({"from":"","to":"Ooty"})}, None)
assert b["statusCode"]==400
print("empty-from -> status", b["statusCode"])

# 5) date clamp
print("clamp(none) ->", app._clamp_date(None), "| clamp(2000-01-01) ->", app._clamp_date("2000-01-01"))
print("\nALL TESTS PASSED")
