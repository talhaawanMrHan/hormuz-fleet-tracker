import asyncio
import json
import os
import httpx
import websockets

BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:8000")
WS = os.environ.get("TEST_WS", BASE.replace("http://", "ws://") + "/ws?role=command")


async def main():
    async with httpx.AsyncClient(base_url=BASE) as client:
        fleet = (await client.get("/api/fleet")).json()["ships"]
        ship = fleet[0]
        print("Testing with ship:", ship["id"], ship["name"], "dest:", ship["destination_port"])
        print("Initial position:", ship["position"], "route legs:", len(ship["route"]))

        # --- WebSocket: watch two ticks of movement ---
        async with websockets.connect(WS) as ws:
            first = json.loads(await ws.recv())
            print("\nWS first message type:", first["type"], "n_ships:", len(first.get("ships", [])))
            second = json.loads(await ws.recv())  # zones_snapshot
            print("WS second message type:", second["type"])
            msg = json.loads(await ws.recv())
            while msg["type"] != "fleet_state":
                msg = json.loads(await ws.recv())
            pos1 = next(s for s in msg["ships"] if s["id"] == ship["id"])["position"]
            msg2 = json.loads(await ws.recv())
            while msg2["type"] != "fleet_state":
                msg2 = json.loads(await ws.recv())
            pos2 = next(s for s in msg2["ships"] if s["id"] == ship["id"])["position"]
            print("Position tick A:", pos1)
            print("Position tick B:", pos2)
            moved = pos1 != pos2
            print("Ship moved between ticks:", moved)

        # --- Zone creation triggers reroute ---
        route = ship["route"]
        mid = route[len(route) // 2]
        zone_polygon = [
            [mid[0] - 0.3, mid[1] - 0.3], [mid[0] + 0.3, mid[1] - 0.3],
            [mid[0] + 0.3, mid[1] + 0.3], [mid[0] - 0.3, mid[1] + 0.3],
        ]
        resp = await client.post("/api/zones", json={"name": "Test Blockade", "polygon": zone_polygon},
                                  headers={"X-Role": "command"})
        print("\nZone create status:", resp.status_code)
        assert resp.status_code == 200, resp.text
        zone = resp.json()
        print("Zone id:", zone["id"])

        edited_polygon = [[p[0] + 0.02, p[1] + 0.02] for p in zone_polygon]
        edit_resp = await client.put(f"/api/zones/{zone['id']}",
                                     json={"name": zone["name"], "polygon": edited_polygon, "kind": zone["kind"]},
                                     headers={"X-Role": "command"})
        assert edit_resp.status_code == 200, edit_resp.text
        print("Zone edit status:", edit_resp.status_code)

        await asyncio.sleep(1.5)
        fleet2 = (await client.get("/api/fleet")).json()["ships"]
        ship2 = next(s for s in fleet2 if s["id"] == ship["id"])
        print("Ship status after zone drawn on its path:", ship2["status"])

        alerts = (await client.get("/api/alerts")).json()["alerts"]
        print("Alert types fired so far:", sorted(set(a["type"] for a in alerts)))

        # --- Command-role enforcement ---
        bad = await client.post("/api/zones", json={"name": "should fail", "polygon": zone_polygon})
        print("\nZone create without X-Role header status (expect 403):", bad.status_code)

        # --- Captain scope enforcement ---
        scope = await client.post("/api/distress", json={"ship_id": fleet2[1]["id"], "message": "scope test"})
        print("Captain distress without role headers status (expect 403):", scope.status_code)

        # --- Directive + captain response flow ---
        ship3_id = fleet2[1]["id"]
        d = await client.post("/api/directives", json={
            "ship_id": ship3_id, "action": "reroute_to_port", "target_port": "MCT-1"
        }, headers={"X-Role": "command"})
        print("\nDirective create status:", d.status_code)
        assert d.status_code == 200, d.text
        directive = d.json()
        cr = await client.post("/api/captain-response", json={
            "directive_id": directive["id"], "ship_id": ship3_id, "response": "accept"
        }, headers={"X-Role": "captain", "X-Ship-ID": ship3_id})
        print("Captain accept status:", cr.status_code)
        assert cr.status_code == 200, cr.text
        new_ship = cr.json()["ship"]
        print("Ship dest after accept:", new_ship["destination_port"], "status:", new_ship["status"])

        # --- Escalate distress via directive ---
        ship4_id = fleet2[2]["id"]
        d2 = await client.post("/api/directives", json={
            "ship_id": ship4_id, "action": "hold_position"
        }, headers={"X-Role": "command"})
        directive2 = d2.json()
        cr2 = await client.post("/api/captain-response", json={
            "directive_id": directive2["id"], "ship_id": ship4_id, "response": "escalate_distress",
            "distress_message": "Engine room fire, 2 crew injured, requesting immediate assistance"
        }, headers={"X-Role": "captain", "X-Ship-ID": ship4_id})
        print("\nEscalate distress status:", cr2.status_code)
        await asyncio.sleep(0.5)
        alerts2 = (await client.get("/api/alerts")).json()["alerts"]
        distress_alerts = [a for a in alerts2 if a["type"] == "distress"]
        print("Distress alert message:", distress_alerts[0]["message"] if distress_alerts else "NONE FOUND")
        print("Distress alert severity (fallback keyword parser since no GROQ key):", distress_alerts[0]["severity"] if distress_alerts else None)

        # --- Route options (bonus) ---
        ro = await client.get(f"/api/ships/{fleet2[3]['id']}/route-options", headers={"X-Role": "command"})
        print("\nRoute options status:", ro.status_code, "n_options:", len(ro.json().get("options", [])))
        for opt in ro.json().get("options", []):
            print("  -", opt["label"], opt["distance_km"], "km, est fuel:", opt["estimated_fuel_percent"], "%")

        # --- Assistance request (bonus) ---
        ar = await client.post("/api/assistance", json={"ship_id": ship4_id, "kind": "medical_aid"},
                               headers={"X-Role": "captain", "X-Ship-ID": ship4_id})
        print("\nAssistance request status:", ar.status_code, "nearby found in alert detail")

        # --- Advisor (bonus, will use fallback since no GROQ key in this sandbox test) ---
        adv = await client.post("/api/advisor", headers={"X-Role": "command"})
        print("\nAdvisor status:", adv.status_code)
        print("Advisor suggestions (fallback):", json.dumps(adv.json(), indent=2)[:500])

        # --- History timeline ---
        await asyncio.sleep(0.5)
        hist = await client.get("/api/history/timeline")
        print("\nHistory timeline entries so far:", len(hist.json()["timestamps_ms"]))

        # --- Stranded edge case: box in a ship's destination entirely ---
        ship5 = fleet2[4]
        import sys
        sys.path.insert(0, ".")
        from app import geo
        approach = geo.port_approach_point(ship5["destination_port"])
        box = [
            [approach[0]-0.5, approach[1]-0.5], [approach[0]+0.5, approach[1]-0.5],
            [approach[0]+0.5, approach[1]+0.5], [approach[0]-0.5, approach[1]+0.5],
        ]
        boxresp = await client.post("/api/zones", json={"name": "Total Blockade", "polygon": box},
                                     headers={"X-Role": "command"})
        await asyncio.sleep(0.3)
        fleet3 = (await client.get("/api/fleet")).json()["ships"]
        ship5_after = next(s for s in fleet3 if s["id"] == ship5["id"])
        print("\nShip boxed in -> status (expect stranded):", ship5_after["status"])

        # cleanup that zone and confirm it retries
        await client.delete(f"/api/zones/{boxresp.json()['id']}", headers={"X-Role": "command"})
        await asyncio.sleep(1.5)
        fleet4 = (await client.get("/api/fleet")).json()["ships"]
        ship5_recovered = next(s for s in fleet4 if s["id"] == ship5["id"])
        print("After zone removed -> status (expect rerouting/normal, NOT stranded):", ship5_recovered["status"])

        print("\nALL TESTS COMPLETED")


asyncio.run(main())
