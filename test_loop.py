import json, app
app._get_json = lambda url: {"results":[{"name":"X","country":"Y","latitude":1.0,"longitude":2.0}]} if "geocoding" in url else {"daily":{"time":["2026-08-15"],"weathercode":[0],"temperature_2m_max":[30],"temperature_2m_min":[20],"precipitation_probability_max":[5],"wind_speed_10m_max":[10]}}

calls={"n":0,"tools":[]}
FINAL={"headline":"ok","overview":"o","timing":{},"route":{},"meals":{},"hotels":[],"kids_play_areas":[],"safety":{"road":[],"natural":[],"area":[],"overall":"fine"},"packing":[]}
class FB:
    def converse(self, **kw):
        calls["n"]+=1
        if calls["n"]==1:
            # ask to use the weather tool once
            return {"stopReason":"tool_use","output":{"message":{"role":"assistant","content":[
                {"toolUse":{"toolUseId":"t1","name":"weather","input":{"lat":1.0,"lon":2.0,"date":"2026-08-15"}}}]}}}
        return {"stopReason":"end_turn","output":{"message":{"role":"assistant","content":[{"text":"```json\n"+json.dumps(FINAL)+"\n```"}]}}}
app._bedrock=FB()
plan=app._agent_plan({"origin":{},"destination":{},"route":{}},"solo","2026-08-15")
assert plan["safety"]["overall"]=="fine"
assert calls["n"]==2, calls
print("tool-use loop iterations:",calls["n"],"| parsed fenced JSON OK -> headline:",plan["headline"])
print("LOOP TEST PASSED")
