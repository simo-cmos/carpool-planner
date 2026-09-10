"""Basic route and service tests for the local web application."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TEST_DATA_DIR = Path(__file__).resolve().parent / ".tmp-db"
TEST_DATA_DIR.mkdir(exist_ok=True)
os.environ["DMPROJECT_DATA_DIR"] = str(TEST_DATA_DIR)

from app import database
from app.costs import _build_mimit_reader, _matches_expected_service_mode, estimate_toll_cost
from app.database import execute, init_db
from app.geocoding import friendly_address_label
from app.main import app
from app.pois import _classify_food, _matches_mode
from app.services.notifier import list_pending_notification_events
from app.services import build_domain_state, clear_all_data, get_app_settings, get_current_dataset_context, get_participant, get_pickup_order_rules, get_ride_together_rules, get_workspace_backup, list_groups, list_participants, list_saved_destinations, list_saved_meetup_spots, list_trip_history, list_trip_invites, list_trip_responses, run_optimization, save_app_settings, save_participant
from app.services.planner import select_best_result
from app.time_utils import time_text_to_minutes
from core.apca import APCAAlgorithm
from core.driver_selection import DriverSelector
from core.models import Car, DriverSet, Location, Participant, RouteAssignment, TripResult


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        execute("DELETE FROM participants")
        execute("DELETE FROM saved_destinations")
        execute("DELETE FROM saved_meetup_spots")
        execute("DELETE FROM saved_groups")
        execute("DELETE FROM trip_responses")
        execute("DELETE FROM trip_invites")
        execute("DELETE FROM trip_history")
        execute("DELETE FROM notification_events")
        execute("DELETE FROM app_settings")
        clear_all_data()
        self.client = TestClient(app)

    def test_save_participant_returns_the_new_row_id(self) -> None:
        new_id = save_participant(
            participant_id=None,
            name="Zoe",
            address_text=None,
            location_name="Carpi",
            latitude=44.7839,
            longitude=15.8853,
            has_car=False,
            fuel_type=None,
            consumption_l_per_100km=None,
            total_seats=None,
            habit_score=0.0,
        )
        self.assertIsInstance(new_id, int)
        self.assertEqual(get_participant(new_id)["name"], "Zoe")
        self.assertEqual(save_participant(
            participant_id=new_id,
            name="Zoe Renamed",
            address_text=None,
            location_name="Carpi",
            latitude=44.7839,
            longitude=15.8853,
            has_car=False,
            fuel_type=None,
            consumption_l_per_100km=None,
            total_seats=None,
            habit_score=0.0,
        ), new_id)

    def test_home_page_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Drivers Manager</title>", response.text)
        self.assertIn('rel="manifest"', response.text)
        self.assertIn("No destination yet", response.text)

    def test_home_next_action_follows_trip_state(self) -> None:
        from app.main import build_next_action

        empty = {"has_destination": False, "has_participants": False, "has_driver": False, "has_history": False}
        self.assertEqual(build_next_action(empty, has_plan=False)["href"], "/plan?tab=places")
        self.assertEqual(build_next_action(empty | {"has_destination": True}, has_plan=False)["href"], "/plan?tab=people")
        ready = {"has_destination": True, "has_participants": True, "has_driver": True, "has_history": False}
        self.assertEqual(build_next_action(ready, has_plan=False), {"label": "Run the plan", "href": "/optimization", "method": "post"})
        self.assertEqual(build_next_action(ready, has_plan=True)["href"], "/share")

    def test_home_shows_destination_sign_and_pending_replies(self) -> None:
        self.client.post("/sample")
        home = self.client.get("/").text
        self.assertIn('id="home-sign"', home)
        self.assertIn("Modena Centro", home)
        self.assertIn("Run the plan", home)
        self.assertIn("Save your first trip", home)
        self.assertNotIn("Roadtrip Control Room", home)

    def test_pages_render_help_guide_and_home_road_strip(self) -> None:
        home = self.client.get("/").text
        self.assertIn('id="help-toggle"', home)
        self.assertIn('id="page-guide"', home)
        self.assertIn("How this page works", home)
        self.assertIn('href="#pic-car"', home)
        self.assertIn('class="road-lane"', home)
        self.assertIn('id="pic-clown-car"', home)
        self.assertIn("Next exit: dinner", home)
        plan = self.client.get("/plan").text
        self.assertIn("Tabs on the right", plan)
        self.assertNotIn("Next exit: dinner", plan)
        self.assertNotIn('id="postbox"', plan)
        share = self.client.get("/share").text
        self.assertIn('id="postbox"', share)
        self.assertIn('class="postino-ride"', share)

    def test_home_counts_pending_guest_replies(self) -> None:
        self.client.post("/sample")
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Cleo",
                "location_name": "Cleo Home",
                "latitude": "44.808553",
                "longitude": "15.814089",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
                "role_tag": "standard",
            },
        )

        home = self.client.get("/").text
        self.assertIn('class="km-marker km-marker-pending"><strong>1</strong>', home)

        self.client.post(f"/invites/{invite['id']}/import-all")
        home = self.client.get("/").text
        self.assertNotIn("km-marker-pending", home)

    def test_database_uses_wal_journal_mode(self) -> None:
        """WAL lets the notifier process read while the web app writes."""
        init_db()
        with database.get_connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")

    def test_plan_page_renders_people_and_places(self) -> None:
        response = self.client.get("/plan")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Participants", response.text)
        self.assertIn("Interactive Map", response.text)
        self.assertIn('option value="meetup"', response.text)
        self.assertIn('id="meetup-spot-form"', response.text)
        self.assertIn('Saved Meetup Spots', response.text)

    def test_plan_page_renders_map_workspace(self) -> None:
        response = self.client.get("/plan")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Interactive Map", response.text)
        self.assertIn('option value="meetup"', response.text)
        self.assertIn("Run driver selection", response.text)
        self.assertIn("/static/style.css?v=", response.text)
        self.assertIn("/static/app.js?v=", response.text)
        self.assertIn("staticVersion:", response.text)

    def test_plan_page_has_all_four_tab_panels(self) -> None:
        html = self.client.get("/plan").text
        for slug in ("people", "places", "rules", "results"):
            self.assertIn(f'id="panel-{slug}"', html)
            self.assertIn(f'data-tab="{slug}"', html)
        self.assertIn('id="panel-places" role="tabpanel" hidden', html)
        self.assertNotIn('id="panel-people" role="tabpanel" hidden', html)

    def test_plan_results_have_show_on_map_buttons(self) -> None:
        self.client.post("/sample")
        self.client.post("/optimization")
        html = self.client.get("/plan?tab=results").text
        self.assertIn('data-show-route-set="0"', html)

    def test_legacy_page_paths_redirect_to_new_pages(self) -> None:
        for old_path, new_location in {
            "/setup": "/plan?tab=people",
            "/planning": "/plan?tab=results",
            "/guest-links": "/share",
            "/history": "/trips",
        }.items():
            response = self.client.get(old_path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, old_path)
            self.assertEqual(response.headers["location"], new_location, old_path)

    def test_new_pages_render(self) -> None:
        for path in ("/", "/plan", "/share", "/trips", "/settings"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('aria-current="page"', response.text, path)

    def test_plan_tab_comes_from_query_or_scroll_target(self) -> None:
        self.assertIn('data-active-tab="rules"', self.client.get("/plan?tab=rules").text)
        self.assertIn('data-active-tab="places"', self.client.get("/plan?scroll=destination-panel").text)
        self.assertIn('data-active-tab="people"', self.client.get("/plan").text)

    def test_unknown_tab_falls_back_to_people(self) -> None:
        self.assertIn('data-active-tab="people"', self.client.get("/plan?tab=bogus").text)

    def test_validation_error_rerenders_on_the_failing_tab(self) -> None:
        self.client.post("/sample")
        participant_id = list_participants()[0]["id"]
        response = self.client.post(
            "/pickup-rules",
            data={"before_participant_id": participant_id, "after_participant_id": participant_id},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('data-active-tab="rules"', response.text)
        self.assertNotIn('id="panel-rules" role="tabpanel" hidden', response.text)

    def test_participant_create_and_edit_flow(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "habit_score": "0",
                "has_car": "true",
                "fuel_type": "gasoline",
                "consumption_l_per_100km": "7",
                "total_seats": "5",
            },
        )
        participant = list_participants()[0]
        response = self.client.post(
            "/participants",
            data={
                "participant_id": str(participant["id"]),
                "name": "Alice Updated",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "habit_score": "1",
                "has_car": "true",
                "fuel_type": "gasoline",
                "consumption_l_per_100km": "7",
                "total_seats": "5",
                "outbound_earliest_time": "08:00",
                "return_latest_time": "23:30",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alice Updated", response.text)
        updated = next(row for row in list_participants() if row["name"] == "Alice Updated")
        self.assertEqual(updated["outbound_earliest_time"], "08:00")
        self.assertEqual(updated["return_latest_time"], "23:30")

    def test_participant_time_preferences_can_be_cleared_back_to_flexible(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "outbound_earliest_time": "08:00",
                "return_latest_time": "23:30",
            },
        )
        participant = list_participants()[0]

        response = self.client.post(
            "/participants",
            data={
                "participant_id": str(participant["id"]),
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "clear_time_preferences": "true",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        updated = list_participants()[0]
        self.assertIsNone(updated["outbound_earliest_time"])
        self.assertIsNone(updated["return_latest_time"])
        self.assertEqual(updated["time_preferences_summary"], "Fully flexible timing")

    def test_participant_can_store_meetup_pickup_override(self) -> None:
        response = self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "pickup_mode": "meetup",
                "pickup_location_name": "Park & Ride Nord",
                "pickup_latitude": "44.7200",
                "pickup_longitude": "10.7000",
                "pickup_flexible": "true",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        participant = list_participants()[0]
        self.assertEqual(participant["pickup_mode"], "meetup")
        self.assertEqual(participant["pickup_location_name"], "Park & Ride Nord")
        self.assertAlmostEqual(participant["pickup_latitude"], 44.7200)

    def test_meetup_spot_can_be_saved(self) -> None:
        response = self.client.post(
            "/meetup-spots",
            data={
                "name": "EV Hub",
                "latitude": "44.7000",
                "longitude": "15.7",
                "is_free_parking": "true",
                "has_ev_charging": "true",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        spots = list_saved_meetup_spots()
        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]["name"], "EV Hub")
        self.assertEqual(spots[0]["has_ev_charging"], 1)

    def test_participant_trip_toggle(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
        )
        participant = list_participants()[0]
        response = self.client.post(
            f"/participants/{participant['id']}/trip-toggle",
            data={"active_in_trip": "false"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Parked", response.text)

    def test_priority_order_does_not_change_algorithm_domain_order(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "First Added",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "priority_rank": "99",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Second Added",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "priority_rank": "1",
            },
        )
        participants, _destination = build_domain_state()
        self.assertEqual([participant.name for participant in participants], ["First Added", "Second Added"])

    def test_participant_reorder_updates_display_order_only(self) -> None:
        self.client.post(
            "/participants",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post(
            "/participants",
            data={"name": "Bob", "location_name": "Modena", "latitude": "44.6471", "longitude": "15.9252"},
        )
        rows = list_participants()
        reordered = f"{rows[1]['id']},{rows[0]['id']}"
        response = self.client.post("/participants/reorder", data={"order": reordered})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([participant["name"] for participant in list_participants()], ["Bob", "Alice"])
        participants, _destination = build_domain_state()
        self.assertEqual([participant.name for participant in participants], ["Alice", "Bob"])

    def test_csv_export_route(self) -> None:
        response = self.client.get("/participants/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("name,address_text,location_name", response.text)
        self.assertIn("active_in_trip", response.text)
        self.assertIn("force_drive_alone", response.text)
        self.assertIn("outbound_earliest_time", response.text)
        self.assertIn("filename=participants.csv", response.headers["content-disposition"])

    def test_csv_export_uses_active_group_name(self) -> None:
        self.client.post(
            "/participants",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post("/groups", data={"group_name": "Friday Dinner Crew"})

        response = self.client.get("/participants/export")

        self.assertEqual(response.status_code, 200)
        self.assertIn("filename=friday-dinner-crew.csv", response.headers["content-disposition"])

    def test_csv_export_keeps_dataset_name_after_workspace_changes(self) -> None:
        self.client.post(
            "/participants",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post("/groups", data={"group_name": "Weekend Crew"})
        participant_id = list_participants()[0]["id"]
        self.client.post(f"/participants/{participant_id}/trip-toggle", data={"active_in_trip": "false"})

        response = self.client.get("/participants/export")

        self.assertEqual(response.status_code, 200)
        self.assertIn("filename=weekend-crew.csv", response.headers["content-disposition"])

    def test_csv_import_validation(self) -> None:
        response = self.client.post(
            "/participants/import",
            files={"csv_file": ("bad.csv", "name,latitude\nAlice,44.1", "text/csv")},
            data={"replace_existing": "false"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("CSV import failed", response.text)

    def test_map_data_endpoint(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
        )
        participant_id = list_participants()[0]["id"]
        self.client.post(
            f"/participants/{participant_id}/trip-toggle",
            data={"active_in_trip": "false"},
        )
        response = self.client.get("/api/map-data?optimize=false")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("participants", payload)
        self.assertIn("settings", payload)
        self.assertEqual(payload["participants"], [])

    def test_map_data_includes_active_meetup_spots(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Luca",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "pickup_mode": "meetup",
                "pickup_location_name": "Park & Ride Nord",
                "pickup_address_text": "Charging Hub",
                "pickup_latitude": "44.7200",
                "pickup_longitude": "10.7000",
            },
        )
        response = self.client.get("/api/map-data?optimize=false")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("meetup_spots", payload)
        self.assertEqual(len(payload["meetup_spots"]), 1)
        self.assertEqual(payload["meetup_spots"][0]["name"], "Park & Ride Nord")
        self.assertEqual(payload["meetup_spots"][0]["participant_names"], ["Luca"])

    def test_static_map_icon_assets_are_served(self) -> None:
        for path in [
            "/static/icon-destination-flag-pixel.png",
            "/static/icon-food-mcdonalds-pixel.png",
            "/static/icon-food-kebab-pixel.png",
            "/static/icon-food-pizza-pixel.png",
            "/static/icon-food-kfc-pixel.png",
            "/static/icon-food-burger-king-pixel.png",
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_static_app_js_builds_map_icons(self) -> None:
        response = self.client.get("/static/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn('buildRasterIcon("/static/icon-food-mcdonalds-pixel.png"', response.text)
        self.assertIn('buildIcon("meetup-pin", "P")', response.text)
        self.assertIn('buildRasterIcon("/static/icon-destination-flag-pixel.png"', response.text)
        self.assertIn('buildIcon("passenger-pin", "•")', response.text)

    def test_optimization_can_add_meetup_pooling_variant(self) -> None:
        self.client.post(
            "/meetup-spots",
            data={
                "name": "Park & Ride Nord",
                "latitude": "44.7100",
                "longitude": "15.78",
                "is_free_parking": "true",
            },
        )
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.7500", "longitude": "15.72"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Driver",
                "location_name": "Driver Home",
                "latitude": "44.7000",
                "longitude": "15.7",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Alice Home",
                "latitude": "44.7120",
                "longitude": "15.792",
                "pickup_flexible": "true",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Bob Home",
                "latitude": "44.7130",
                "longitude": "15.793",
                "pickup_flexible": "true",
            },
        )

        optimization = run_optimization()

        meetup_results = [result for result in optimization["trip_results"] if getattr(result, "plan_variant", "direct") == "meetup"]
        self.assertTrue(meetup_results)
        self.assertTrue(meetup_results[0].meetup_instructions)

    def test_public_invite_visit_queues_notification(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Friday Dinner"})
        invite = list_trip_invites()[0]

        response = self.client.get(f"/invite/{invite['token']}", headers={"host": "example.trycloudflare.com"})

        self.assertEqual(response.status_code, 200)
        events = list_pending_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "guest_visit")

    def test_duplicate_guest_invite_visits_are_deduplicated_briefly(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Friday Dinner"})
        invite = list_trip_invites()[0]

        first = self.client.get(f"/invite/{invite['token']}", headers={"host": "example.trycloudflare.com"})
        second = self.client.get(f"/invite/{invite['token']}", headers={"host": "example.trycloudflare.com"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        events = list_pending_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "guest_visit")

    def test_guest_submission_queues_notification(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Friday Dinner"})
        invite = list_trip_invites()[0]

        response = self.client.post(
            f"/invite/{invite['token']}",
            headers={"host": "example.trycloudflare.com"},
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        events = list_pending_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "guest_submission")

    def test_localhost_invite_visit_does_not_queue_public_notification(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Friday Dinner"})
        invite = list_trip_invites()[0]

        response = self.client.get(f"/invite/{invite['token']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list_pending_notification_events(), [])

    def test_saved_group_and_restore_history_flow(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "habit_score": "0",
                "has_car": "true",
                "fuel_type": "gasoline",
                "consumption_l_per_100km": "7",
                "total_seats": "5",
                "role_tag": "always_driver",
                "availability_tag": "available",
                "pickup_flexible": "true",
                "priority_rank": "1",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Bologna",
                "latitude": "44.4949",
                "longitude": "16.3426",
                "habit_score": "0",
                "priority_rank": "2",
            },
        )
        group_response = self.client.post("/groups", data={"group_name": "Friends"}, follow_redirects=True)
        self.assertEqual(group_response.status_code, 200)
        self.assertIn("Friends", group_response.text)

        optimization_response = self.client.post("/optimization", follow_redirects=True)
        self.assertEqual(optimization_response.status_code, 200)

        history_response = self.client.post(
            "/history/save",
            data={"trip_name": "Test Trip", "trip_date": "2026-03-13", "preset_name": "Friday Dinner", "notes": "smoke"},
            follow_redirects=True,
        )
        self.assertEqual(history_response.status_code, 200)
        self.assertIn("Test Trip", history_response.text)

        self.client.post(f"/participants/{list_participants()[0]['id']}/delete")
        trip_id = list_trip_history()[0]["id"]
        restore_response = self.client.post(f"/history/{trip_id}/restore", follow_redirects=True)
        self.assertEqual(restore_response.status_code, 200)
        self.assertIn("Alice", restore_response.text)
        self.assertIn("backed up automatically", restore_response.text)
        backup = get_workspace_backup()
        self.assertIsNotNone(backup)
        self.assertEqual(backup["reason"], f"restore_trip_history_entry:{trip_id}")

    def test_trip_history_snapshot_can_be_deleted_without_touching_workspace(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "consumption_l_per_100km": "7",
                "total_seats": "5",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Parma",
                "latitude": "44.8015",
                "longitude": "15.3279",
            },
        )
        self.client.post("/optimization", follow_redirects=True)
        self.client.post(
            "/history/save",
            data={"trip_name": "Disposable Trip", "trip_date": "2026-03-13"},
            follow_redirects=True,
        )

        trip_id = list_trip_history()[0]["id"]
        response = self.client.post(f"/history/{trip_id}/delete", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Past trip deleted.", response.text)
        self.assertEqual(list_trip_history(), [])
        self.assertTrue(any(participant["name"] == "Alice" for participant in list_participants()))

    def test_invite_session_can_replace_working_dataset(self) -> None:
        self.client.post(
            "/destination",
            data={
                "name": "Pub",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "target_arrival_time": "20:30",
            },
        )
        self.client.post("/invites", data={"trip_name": "Friday dinner guest form"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
            },
        )
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Bob",
                "location_name": "Parma",
                "latitude": "44.8015",
                "longitude": "15.3279",
            },
        )

        response = self.client.post(f"/invites/{invite['id']}/use-as-workspace", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Guest session loaded as the current trip", response.text)
        self.assertIn("Current trip", response.text)
        self.assertIn("Friday dinner guest form", response.text)
        self.assertIn("Guest session workspace", response.text)
        self.assertIn("Synced with latest guest responses", response.text)
        self.assertEqual([participant["name"] for participant in list_participants()], ["Alice", "Bob"])
        _, destination = build_domain_state()
        self.assertIsNotNone(destination)
        self.assertEqual(destination.target_arrival_time_min, 20 * 60 + 30)
        context = get_current_dataset_context()
        self.assertIsNotNone(context)
        self.assertEqual(context["dataset_type"], "invite")
        self.assertEqual(context["dataset_name"], "Friday dinner guest form")
        self.assertEqual(context["response_count"], 2)

    def test_food_stop_vote_is_stored_and_tallied(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Pub", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Vote night"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "food_stop_vote": "mcdonalds",
            },
        )
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Bob",
                "location_name": "Parma",
                "latitude": "44.8015",
                "longitude": "15.3279",
                "food_stop_vote": "not-a-real-option",
            },
        )

        votes = {row["name"]: row["food_stop_vote"] for row in list_trip_responses(invite["id"])}
        self.assertEqual(votes["Alice"], "mcdonalds")
        self.assertIsNone(votes["Bob"])

        review = self.client.get(f"/invites/{invite['id']}")
        self.assertEqual(review.status_code, 200)
        self.assertIn("Food stop votes", review.text)
        self.assertIn("McDonald", review.text)

    def test_organizer_password_protects_admin_but_not_guests(self) -> None:
        from app.services.auth import clear_password, is_password_set

        try:
            # No password: everything open.
            self.assertFalse(is_password_set())
            self.assertEqual(self.client.get("/setup").status_code, 200)

            # Setting a password keeps the current browser logged in via cookie.
            response = self.client.post(
                "/settings/security",
                data={"new_password": "supersecret1", "confirm_password": "supersecret1"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertTrue(is_password_set())
            self.assertEqual(self.client.get("/setup").status_code, 200)

            # A fresh browser is redirected to the login page.
            fresh = TestClient(app)
            redirect = fresh.get("/setup", follow_redirects=False)
            self.assertEqual(redirect.status_code, 303)
            self.assertTrue(redirect.headers["location"].startswith("/login"))
            self.assertEqual(fresh.post("/participants", data={"name": "X"}).status_code, 401)

            # Wrong password is rejected; the right one starts a session.
            self.assertEqual(fresh.post("/login", data={"password": "nope"}).status_code, 401)
            login = fresh.post(
                "/login",
                data={"password": "supersecret1", "next_target": "/setup"},
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 303)
            self.assertEqual(login.headers["location"], "/setup")
            self.assertEqual(fresh.get("/setup").status_code, 200)

            # Guest invite links keep working without any login.
            self.client.post(
                "/destination",
                data={"name": "Pub", "latitude": "44.6471", "longitude": "15.9252"},
            )
            self.client.post("/invites", data={"trip_name": "Locked night"})
            invite = list_trip_invites()[0]
            anonymous = TestClient(app)
            self.assertEqual(anonymous.get(f"/invite/{invite['token']}").status_code, 200)
            submit = anonymous.post(
                f"/invite/{invite['token']}",
                data={
                    "name": "Guest",
                    "location_name": "Modena",
                    "latitude": "44.6455",
                    "longitude": "15.9245",
                },
                follow_redirects=False,
            )
            self.assertEqual(submit.status_code, 303)

            # Landing page stays public; wrong current password can't change it.
            self.assertEqual(anonymous.get("/welcome").status_code, 200)
            self.assertEqual(
                self.client.post(
                    "/settings/security",
                    data={"current_password": "wrong", "new_password": "otherpass123", "confirm_password": "otherpass123"},
                ).status_code,
                400,
            )

            # Removing the password reopens the app.
            reopen = self.client.post(
                "/settings/security",
                data={"current_password": "supersecret1", "new_password": "", "confirm_password": ""},
                follow_redirects=False,
            )
            self.assertEqual(reopen.status_code, 303)
            self.assertFalse(is_password_set())
            self.assertEqual(TestClient(app).get("/setup").status_code, 200)
        finally:
            clear_password()

    def test_landing_page_renders(self) -> None:
        response = self.client.get("/welcome")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Group carpools, planned in minutes", response.text)
        self.assertIn("Try it with demo data", response.text)

    def test_editing_loaded_invite_marks_dataset_customized(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Pub", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post("/invites", data={"trip_name": "Session"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post(f"/invites/{invite['id']}/use-as-workspace")
        participant = list_participants()[0]

        self.client.post(
            "/participants",
            data={
                "participant_id": str(participant["id"]),
                "name": "Alice Updated",
                "location_name": participant["location_name"],
                "latitude": str(participant["latitude"]),
                "longitude": str(participant["longitude"]),
            },
        )

        context = get_current_dataset_context()
        self.assertIsNotNone(context)
        self.assertTrue(context["customized"])

    def test_destination_target_arrival_time_is_saved(self) -> None:
        response = self.client.post(
            "/destination",
            data={
                "name": "Dinner",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "target_arrival_time": "20:30",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        participants, destination = build_domain_state()
        self.assertEqual(participants, [])
        self.assertIsNotNone(destination)
        self.assertEqual(destination.target_arrival_time_min, 20 * 60 + 30)

    def test_saved_destination_keeps_target_arrival_time(self) -> None:
        response = self.client.post(
            "/destination/favorite",
            data={
                "name": "Dinner",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "target_arrival_time": "20:30",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        favorites = list_saved_destinations()
        self.assertEqual(len(favorites), 1)
        self.assertEqual(favorites[0]["target_arrival_time"], "20:30")
        self.assertIn("Target arrival: 20:30", response.text)

    def test_guest_invite_can_be_created_from_current_destination(self) -> None:
        self.client.post(
            "/destination",
            data={
                "name": "Arrogant Pub",
                "latitude": "44.689953",
                "longitude": "15.648573",
                "target_arrival_time": "20:00",
            },
        )

        response = self.client.post(
            "/invites",
            data={"trip_name": "Pub night intake", "deadline_at": "2026-03-30T18:00"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        invites = list_trip_invites()
        self.assertEqual(len(invites), 1)
        self.assertEqual(invites[0]["trip_name"], "Pub night intake")
        self.assertEqual(invites[0]["target_arrival_time"], "20:00")
        self.assertIn("Review submissions", response.text)

    def test_public_share_url_is_used_for_invite_links(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573"},
        )
        response = self.client.post(
            "/invites",
            data={"trip_name": "Pub night intake"},
            follow_redirects=True,
        )

        self.assertEqual(get_app_settings()["sharing"]["guest_public_base_url"], "https://example.trycloudflare.com")
        self.assertIn("https://example.trycloudflare.com/invite/", response.text)
        self.assertIn("Share on WhatsApp", response.text)

    def test_single_public_share_url_update_preserves_other_mode(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={
                "guest_public_base_url": "https://guest.trycloudflare.com",
                "admin_public_base_url": "https://admin.trycloudflare.com",
            },
        )

        response = self.client.post(
            "/settings/public-share-url/guest",
            data={"public_base_url": "https://new-guest.trycloudflare.com"},
        )

        self.assertEqual(response.status_code, 200)
        sharing = get_app_settings()["sharing"]
        self.assertEqual(sharing["guest_public_base_url"], "https://new-guest.trycloudflare.com")
        self.assertEqual(sharing["admin_public_base_url"], "https://admin.trycloudflare.com")

    def test_public_tunnel_root_redirects_to_single_open_invite(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]

        response = self.client.get("/", headers={"host": "example.trycloudflare.com"}, follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/invite/{invite['token']}")

    def test_public_tunnel_blocks_admin_mutation_routes(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        response = self.client.post(
            "/participants",
            headers={"host": "example.trycloudflare.com"},
            data={
                "name": "Guest Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(list_participants(), [])

    def test_public_tunnel_still_allows_guest_invite_submission(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"guest_public_base_url": "https://example.trycloudflare.com"},
        )
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573", "target_arrival_time": "20:00"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]

        response = self.client.post(
            f"/invite/{invite['token']}",
            headers={"host": "example.trycloudflare.com"},
            data={
                "name": "Guest Cleo",
                "location_name": "Cleo Home",
                "latitude": "44.808553",
                "longitude": "15.814089",
                "role_tag": "standard",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/preview?response_token=", response.headers["location"])
        self.assertEqual(len(list_trip_responses(invite["id"])), 1)

    def test_admin_public_tunnel_keeps_full_access(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"admin_public_base_url": "https://admin.trycloudflare.com"},
        )

        response = self.client.post(
            "/participants",
            headers={"host": "admin.trycloudflare.com"},
            data={
                "name": "Admin Guest",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)

    def test_admin_public_root_queues_notification(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"admin_public_base_url": "https://admin.trycloudflare.com"},
        )

        response = self.client.get("/", headers={"host": "admin.trycloudflare.com"})

        self.assertEqual(response.status_code, 200)
        events = list_pending_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "admin_public_visit")

    def test_admin_public_activity_queues_notification(self) -> None:
        self.client.post(
            "/settings/public-share-url",
            data={"admin_public_base_url": "https://admin.trycloudflare.com"},
        )

        response = self.client.post(
            "/participants",
            headers={"host": "admin.trycloudflare.com"},
            data={
                "name": "Admin Guest",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        events = list_pending_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "admin_public_activity")
        self.assertEqual(len(list_participants()), 1)

    def test_guest_can_submit_response_and_open_preview(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573", "target_arrival_time": "20:00"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Driver",
                "location_name": "Driver Home",
                "latitude": "44.760000",
                "longitude": "15.7",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
                "role_tag": "standard",
            },
        )

        response = self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Cleo",
                "location_name": "Cleo Home",
                "latitude": "44.808553",
                "longitude": "15.814089",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
                "role_tag": "standard",
                "outbound_latest_time": "19:30",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        preview_url = response.headers["location"]
        self.assertIn("/preview?response_token=", preview_url)
        responses = list_trip_responses(invite["id"])
        self.assertEqual(len(responses), 2)
        preview_response = self.client.get(preview_url)
        self.assertEqual(preview_response.status_code, 200)
        self.assertIn("Guest Preview", preview_response.text)
        self.assertIn("Guest Cleo", preview_response.text)
        self.assertIn("Route Preview", preview_response.text)
        self.assertIn("Showing Best Score", preview_response.text)
        self.assertIn("Routing option", preview_response.text)
        self.assertIn("/person", preview_response.text)
        self.assertIn("Best Score", preview_response.text)

    def test_guest_can_choose_meetup_pickup_in_public_form(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573", "target_arrival_time": "20:00"},
        )
        self.client.post(
            "/meetup-spots",
            data={
                "name": "Park & Ride Nord",
                "address_text": "Charging Hub",
                "latitude": "44.750000",
                "longitude": "15.71",
                "has_ev_charging": "true",
            },
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]

        response = self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Meetup",
                "location_name": "Guest Home",
                "latitude": "44.808553",
                "longitude": "15.814089",
                "pickup_mode": "meetup",
                "meetup_spot_id": str(list_saved_meetup_spots()[0]["id"]),
                "pickup_flexible": "true",
                "role_tag": "prefers_passenger",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        saved = list_trip_responses(invite["id"])[0]
        self.assertEqual(saved["pickup_mode"], "meetup")
        self.assertEqual(saved["pickup_location_name"], "Park & Ride Nord")
        self.assertEqual(saved["pickup_address_text"], "Charging Hub")
        self.assertTrue(saved["pickup_flexible"])

    def test_admin_can_import_guest_response_into_current_trip(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573", "target_arrival_time": "20:00"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Ada",
                "location_name": "Ada Home",
                "latitude": "44.574370",
                "longitude": "15.981997",
                "role_tag": "prefers_passenger",
                "outbound_latest_time": "19:20",
            },
        )
        response_row = list_trip_responses(invite["id"])[0]

        response = self.client.post(
            f"/invites/{invite['id']}/responses/{response_row['id']}/import",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(any(participant["name"] == "Guest Ada" for participant in list_participants()))
        imported_row = list_trip_responses(invite["id"])[0]
        self.assertEqual(imported_row["status"], "imported")

    def test_imported_guest_response_preserves_meetup_pickup_override(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Ada",
                "location_name": "Ada Home",
                "latitude": "44.574370",
                "longitude": "15.981997",
                "pickup_mode": "meetup",
                "pickup_location_name": "Free EV Parking",
                "pickup_address_text": "North Hub",
                "pickup_latitude": "44.600000",
                "pickup_longitude": "10.950000",
                "pickup_flexible": "true",
            },
        )
        response_row = list_trip_responses(invite["id"])[0]

        self.client.post(
            f"/invites/{invite['id']}/responses/{response_row['id']}/import",
            follow_redirects=False,
        )

        participant = next(participant for participant in list_participants() if participant["name"] == "Guest Ada")
        self.assertEqual(participant["pickup_mode"], "meetup")
        self.assertEqual(participant["pickup_location_name"], "Free EV Parking")
        self.assertEqual(participant["pickup_address_text"], "North Hub")
        self.assertAlmostEqual(participant["pickup_latitude"], 44.6)
        self.assertAlmostEqual(participant["pickup_longitude"], 10.95)

    def test_fuel_service_mode_matching_uses_servito_for_lpg_and_methane(self) -> None:
        self.assertTrue(_matches_expected_service_mode("gasoline", "self"))
        self.assertTrue(_matches_expected_service_mode("diesel", "impianto stradale self service"))
        self.assertTrue(_matches_expected_service_mode("lpg", "servito"))
        self.assertTrue(_matches_expected_service_mode("methane", "impianto stradale servito"))
        self.assertFalse(_matches_expected_service_mode("lpg", "self"))
        self.assertFalse(_matches_expected_service_mode("methane", "self"))

    def test_selection_and_optimization_use_redirect_flow(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "5",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
            },
        )
        selection_response = self.client.post("/selection", follow_redirects=False)
        self.assertEqual(selection_response.status_code, 303)
        self.assertIn("/plan", selection_response.headers["location"])
        self.assertNotIn("view=", selection_response.headers["location"])

        optimization_response = self.client.post("/optimization", follow_redirects=False)
        self.assertEqual(optimization_response.status_code, 303)
        self.assertIn("/plan", optimization_response.headers["location"])

        rendered = self.client.get(optimization_response.headers["location"])
        self.assertEqual(rendered.status_code, 200)
        self.assertIn("Optimization Results", rendered.text)

        # Cached results survive plain navigation, not just the post-run redirect.
        revisited = self.client.get("/planning")
        self.assertEqual(revisited.status_code, 200)
        self.assertIn("Optimization Results", revisited.text)

    def test_share_report_uses_cached_optimization(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "5",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
            },
        )
        self.client.post("/optimization", follow_redirects=True)

        response = self.client.get("/share/current")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Share Report", response.text)
        self.assertIn("Driver Routes", response.text)
        self.assertIn("Share via WhatsApp", response.text)

    def test_share_report_is_self_contained(self) -> None:
        """The report is downloadable, so its CSS must be inlined, not linked."""
        self.client.post("/sample")
        self.client.post("/optimization", follow_redirects=True)

        response = self.client.get("/share/current")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('href="/static/style.css', response.text)
        self.assertIn("--sign-green", response.text)

    def test_share_report_can_target_specific_plan(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "5",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Carla",
                "location_name": "Parma",
                "latitude": "44.8015",
                "longitude": "15.3279",
                "has_car": "true",
                "fuel_type": "diesel",
                "total_seats": "4",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
            },
        )
        self.client.post("/optimization", follow_redirects=True)
        cached = database.get_setting("optimization_cache", {})
        trip_results = cached.get("payload", {}).get("trip_results", [])
        if len(trip_results) < 2:
            self.skipTest("Test scenario produced fewer than 2 route sets.")

        response = self.client.get("/share/current?route_set_index=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(trip_results[1]["driver_set_name"], response.text)

    def test_share_page_lists_one_message_per_driver(self) -> None:
        self.client.post("/sample")
        self.client.post("/optimization")
        html = self.client.get("/share").text
        self.assertIn("Current plan", html)
        self.assertIn("data-copy-text=", html)
        self.assertIn("https://wa.me/?text=", html)
        from app.main import _build_driver_messages, _resolve_share_context
        optimization, chosen, _index = _resolve_share_context(None)
        messages = _build_driver_messages(chosen, optimization, {"name": "Modena Centro"})
        self.assertEqual(len(messages), len(chosen["assignments"]))
        self.assertTrue(messages[0]["text"].startswith(messages[0]["driver"]))
        self.assertIn("Modena Centro", messages[0]["text"])
        text = messages[0]["text"]
        assignment = chosen["assignments"][0]
        self.assertIn("Pick up:", text)
        self.assertIn(" km, EUR ", text)
        self.assertIn(" per person.", text)
        pickup_schedule = assignment["pickup_schedule"]
        self.assertTrue(pickup_schedule, "sample dataset should produce a non-empty pickup schedule")
        for stop in pickup_schedule:
            self.assertIn(stop, text)
        if assignment.get("outbound_departure_time"):
            self.assertIn(f"Leave at {assignment['outbound_departure_time']}.", text)
        if assignment.get("destination_arrival_time"):
            self.assertIn(f"Arrive around {assignment['destination_arrival_time']}.", text)

    def test_settings_hides_optimizer_parameters_behind_advanced(self) -> None:
        html = self.client.get("/settings").text
        self.assertIn('<details class="advanced-fields" id="advanced-settings-panel">', html)
        self.assertLess(html.index('name="map_latitude"'), html.index('name="num_ants"'))
        self.assertIn('href="/welcome"', html)

    def test_optimization_infers_missing_pickup_timeline(self) -> None:
        from app.services import clear_all_data, load_sample_dataset

        clear_all_data()
        load_sample_dataset()
        optimization = run_optimization()
        for trip in optimization["trip_results"]:
            for assignment in trip.assignments:
                self.assertIsNotNone(assignment.outbound_departure_time)
                self.assertIsNotNone(assignment.destination_arrival_time)
                for item in assignment.pickup_schedule:
                    self.assertIn(" at ", item)

    def test_saved_destination_and_backup_export(self) -> None:
        save_response = self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
            follow_redirects=True,
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertIn("Destination saved.", save_response.text)
        favorite_response = self.client.post(
            "/destination/favorite",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
            follow_redirects=True,
        )
        self.assertEqual(favorite_response.status_code, 200)
        self.assertIn("Destination saved to favorites.", favorite_response.text)
        second_favorite_response = self.client.post("/destination/favorite", follow_redirects=True)
        self.assertEqual(second_favorite_response.status_code, 200)
        self.assertEqual(second_favorite_response.text.count(">Office</strong>"), 1)

        backup_response = self.client.get("/workspace/export")
        self.assertEqual(backup_response.status_code, 200)
        self.assertIn('"workspace"', backup_response.text)

    def test_destination_save_self_initializes_database(self) -> None:
        with sqlite3.connect(database.DB_PATH) as connection:
            connection.executescript(
                """
                DROP TABLE IF EXISTS schema_meta;
                DROP TABLE IF EXISTS participants;
                DROP TABLE IF EXISTS app_settings;
                DROP TABLE IF EXISTS geocode_cache;
                DROP TABLE IF EXISTS reverse_geocode_cache;
                DROP TABLE IF EXISTS saved_destinations;
                DROP TABLE IF EXISTS saved_groups;
                DROP TABLE IF EXISTS trip_history;
                """
            )
        database._SCHEMA_READY = False
        database._INIT_IN_PROGRESS = False

        response = self.client.post(
            "/destination",
            data={"name": "Gym", "latitude": "44.5000", "longitude": "15.5"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Gym", response.text)

    def test_driver_defaults_apply_when_consumption_missing(self) -> None:
        response = self.client.post(
            "/participants",
            data={
                "name": "Default Diesel",
                "location_name": "Parma",
                "latitude": "44.8015",
                "longitude": "15.3279",
                "has_car": "true",
                "fuel_type": "diesel",
                "total_seats": "5",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        participant = next(row for row in list_participants() if row["name"] == "Default Diesel")
        self.assertEqual(participant["consumption_l_per_100km"], 4.9)

    def test_friendly_address_label_prefers_street_and_house_number(self) -> None:
        label = friendly_address_label(
            {
                "display_name": "15, Via Roma, Modena, Emilia-Romagna, Italia",
                "address": {"house_number": "15", "road": "Via Roma", "city": "Modena"},
            }
        )
        self.assertEqual(label, "Via Roma 15, Modena")

    def test_toll_estimate_stays_zero_for_short_urban_route(self) -> None:
        self.assertEqual(estimate_toll_cost(12.0, 28.0), 0.0)

    def test_toll_estimate_grows_for_fast_long_route(self) -> None:
        self.assertGreater(estimate_toll_cost(180.0, 110.0), 0.0)

    def test_toll_estimate_sums_multiple_fast_legs(self) -> None:
        single_leg = estimate_toll_cost(70.0, 80.0)
        multi_leg = estimate_toll_cost(
            70.0,
            80.0,
            leg_distances_km=[28.0, 14.0, 28.0],
            leg_durations_min=[18.0, 44.0, 18.0],
        )
        self.assertGreater(multi_leg, single_leg)

    def test_mimit_reader_skips_preamble_and_detects_header(self) -> None:
        raw = "\n".join(
            [
                "Nota informativa aggiornata al 2026-03-14",
                "descCarburante|isSelf|prezzo",
                "Benzina|1|1,812",
                "Gasolio|1|1,731",
            ]
        )
        reader = _build_mimit_reader(raw)
        rows = list(reader)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["descCarburante"], "Benzina")

    def test_food_classifier_supports_new_modes(self) -> None:
        self.assertEqual(_classify_food({"brand": "McDonald's"}), "mcdonalds")
        self.assertEqual(_classify_food({"name": "Burger King Modena"}), "burger_king")
        self.assertEqual(_classify_food({"amenity": "fast_food", "cuisine": "pizza"}), "pizza")
        self.assertIsNone(_classify_food({"amenity": "restaurant", "cuisine": "pizza"}))
        self.assertTrue(_matches_mode("pizza", "all"))
        self.assertFalse(_matches_mode("pizza", "kebab"))

    def test_shorter_route_gets_higher_fitness(self) -> None:
        participants = [
            Participant(
                name="Driver",
                location=Location("Driver Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(
                name="Near Pickup",
                location=Location("Near Pickup", 44.5200, 15.52),
            ),
            Participant(
                name="Far Pickup",
                location=Location("Far Pickup", 45.0500, 16.05),
            ),
        ]
        destination = Location("Destination", 45.0800, 16.08)
        algorithm = APCAAlgorithm(participants, [0], destination, num_ants=2, num_iterations=2)

        shorter_assignment = {0: [0, 1]}
        longer_assignment = {0: [1, 0]}

        shorter_fit = algorithm._evaluate(shorter_assignment, [])
        longer_fit = algorithm._evaluate(longer_assignment, [])

        self.assertGreater(shorter_fit, longer_fit)

    def test_time_window_preferences_influence_route_fitness(self) -> None:
        participants = [
            Participant(
                name="Driver",
                location=Location("Driver Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
                outbound_earliest_time_min=8 * 60,
                outbound_latest_time_min=8 * 60 + 5,
                return_latest_time_min=23 * 60,
            ),
            Participant(
                name="Early Pickup",
                location=Location("Early Pickup", 44.5900, 15.5),
                outbound_latest_time_min=8 * 60 + 20,
                return_latest_time_min=23 * 60,
            ),
            Participant(
                name="Late Pickup",
                location=Location("Late Pickup", 44.8600, 15.5),
                outbound_earliest_time_min=8 * 60 + 45,
                return_latest_time_min=23 * 60,
            ),
        ]
        destination = Location("Destination", 45.0500, 15.5)
        algorithm = APCAAlgorithm(participants, [0], destination, num_ants=2, num_iterations=2)

        good_order = {0: [0, 1]}
        bad_order = {0: [1, 0]}

        good_fit = algorithm._evaluate(good_order, [])
        bad_fit = algorithm._evaluate(bad_order, [])

        self.assertGreater(good_fit, bad_fit)

    def test_separate_return_plan_can_be_enabled(self) -> None:
        save_app_settings(
            num_ants=8,
            num_iterations=20,
            alpha=1.0,
            beta=2.5,
            rho=0.15,
            map_latitude=44.6471,
            map_longitude=10.9252,
            map_zoom=9,
            plan_return_separately=True,
            notifications_enabled=True,
        )
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Driver One",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
                "return_latest_time": "23:00",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Passenger One",
                "location_name": "Bologna",
                "latitude": "44.4949",
                "longitude": "16.3426",
                "return_latest_time": "23:00",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Passenger Two",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "return_earliest_time": "23:45",
            },
        )

        optimization = run_optimization()
        self.assertTrue(any(result.return_assignments for result in optimization["trip_results"]))

    def test_settings_store_the_selected_solver_and_default_to_local_search(self) -> None:
        self.assertEqual(get_app_settings()["optimization"]["algorithm"], "local_search")
        base = {
            "num_ants": "12", "num_iterations": "30", "alpha": "1.2", "beta": "2.3", "rho": "0.2",
            "map_latitude": "44.6471", "map_longitude": "10.9252", "map_zoom": "10",
        }
        response = self.client.post("/settings", data={**base, "algorithm": "annealing"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(get_app_settings()["optimization"]["algorithm"], "annealing")

        self.client.post("/settings", data={**base, "algorithm": "not-a-solver"}, follow_redirects=False)
        self.assertEqual(get_app_settings()["optimization"]["algorithm"], "local_search")

        page = self.client.get("/settings")
        self.assertIn('id="solver-select"', page.text)
        self.assertIn('data-solver="ant_colony"', page.text)

        # The cached result must remember which solver ran, so the Results badge is right.
        self.client.post("/settings", data={**base, "algorithm": "exhaustive"}, follow_redirects=False)
        self.client.post("/sample", follow_redirects=False)
        self.client.post("/optimization", follow_redirects=False)
        results = self.client.get("/plan?tab=results")
        self.assertIn("Solver Exhaustive", results.text)

    def test_advanced_settings_preserve_return_planning_when_checkbox_is_absent(self) -> None:
        save_app_settings(
            num_ants=8,
            num_iterations=20,
            alpha=1.0,
            beta=2.5,
            rho=0.15,
            map_latitude=44.6471,
            map_longitude=10.9252,
            map_zoom=9,
            plan_return_separately=True,
            notifications_enabled=True,
        )

        response = self.client.post(
            "/settings",
            data={
                "num_ants": "12",
                "num_iterations": "30",
                "alpha": "1.2",
                "beta": "2.3",
                "rho": "0.2",
                "map_latitude": "44.6471",
                "map_longitude": "10.9252",
                "map_zoom": "10",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(get_app_settings()["optimization"]["plan_return_separately"])

    def test_return_planning_toggle_can_be_saved_from_planner_controls(self) -> None:
        response = self.client.post(
            "/settings/return-planning",
            data={"plan_return_separately": "true"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(get_app_settings()["optimization"]["plan_return_separately"])

    def test_pickup_order_rule_is_stored_and_restored_with_workspace(self) -> None:
        self.client.post(
            "/participants",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post(
            "/participants",
            data={"name": "Bob", "location_name": "Modena", "latitude": "44.6471", "longitude": "15.9252"},
        )
        participants = list_participants()
        response = self.client.post(
            "/pickup-rules",
            data={"before_participant_id": str(participants[0]["id"]), "after_participant_id": str(participants[1]["id"])},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        rules = get_pickup_order_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["before_id"], participants[0]["id"])

    def test_ride_together_rule_is_stored(self) -> None:
        self.client.post(
            "/participants",
            data={"name": "Alice", "location_name": "Reggio Emilia", "latitude": "44.6989", "longitude": "15.631"},
        )
        self.client.post(
            "/participants",
            data={"name": "Bob", "location_name": "Modena", "latitude": "44.6471", "longitude": "15.9252"},
        )
        participants = list_participants()
        response = self.client.post(
            "/ride-together-rules",
            data={"first_participant_id": str(participants[0]["id"]), "second_participant_id": str(participants[1]["id"])},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        rules = get_ride_together_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual({rules[0]["first_id"], rules[0]["second_id"]}, {participants[0]["id"], participants[1]["id"]})

    def test_pickup_order_rule_nudges_route_order(self) -> None:
        participants = [
            Participant(
                name="Driver",
                location=Location("Driver Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(
                name="Alice",
                location=Location("Alice Home", 44.5500, 15.5),
            ),
            Participant(
                name="Bob",
                location=Location("Bob Home", 44.5800, 15.5),
            ),
        ]
        destination = Location("Destination", 44.9000, 15.5)
        no_rule_algorithm = APCAAlgorithm(participants, [0], destination, num_ants=2, num_iterations=2)
        rule_algorithm = APCAAlgorithm(
            participants,
            [0],
            destination,
            num_ants=2,
            num_iterations=2,
            pickup_order_rules=[(1, 2)],
        )

        preferred_assignment = {0: [0, 1]}
        reversed_assignment = {0: [1, 0]}

        no_rule_gap = no_rule_algorithm._evaluate(preferred_assignment, []) - no_rule_algorithm._evaluate(reversed_assignment, [])
        rule_gap = rule_algorithm._evaluate(preferred_assignment, []) - rule_algorithm._evaluate(reversed_assignment, [])

        self.assertGreater(rule_gap, no_rule_gap)

    def test_ride_together_rule_penalizes_split_assignments(self) -> None:
        participants = [
            Participant(
                name="Driver A",
                location=Location("Driver A Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(
                name="Driver B",
                location=Location("Driver B Home", 44.5400, 15.54),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(name="Alice", location=Location("Alice Home", 44.5100, 15.51)),
            Participant(name="Bob", location=Location("Bob Home", 44.5120, 15.512)),
        ]
        destination = Location("Destination", 44.7000, 15.7)
        algorithm = APCAAlgorithm(
            participants,
            [0, 1],
            destination,
            num_ants=2,
            num_iterations=2,
            ride_together_rules=[(2, 3)],
        )

        together_assignment = {0: [0, 1], 1: []}
        split_assignment = {0: [0], 1: [1]}

        together_fit = algorithm._evaluate(together_assignment, [])
        split_fit = algorithm._evaluate(split_assignment, [])

        self.assertGreater(together_fit, split_fit)

    def test_driver_selection_includes_detour_aware_candidate_when_extra_driver_is_available(self) -> None:
        participants = [
            Participant(
                name="Driver A",
                location=Location("Driver A Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver B",
                location=Location("Driver B Home", 44.5200, 15.52),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver C",
                location=Location("Driver C Home", 44.5400, 15.54),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Passenger D",
                location=Location("Passenger D Home", 44.5600, 15.56),
            ),
        ]
        destination = Location("Destination", 44.6500, 15.65)

        driver_sets = DriverSelector(participants, destination).select_driver_sets()

        self.assertIn("Detour-Aware", [driver_set.name for driver_set in driver_sets])

    def test_driver_selection_detour_aware_can_cover_remote_outlier(self) -> None:
        participants = [
            Participant(
                name="Local Driver A",
                location=Location("Local Driver A Home", 44.7000, 15.9),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Local Driver B",
                location=Location("Local Driver B Home", 44.7060, 15.906),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Remote Driver",
                location=Location("Remote Driver Home", 44.8300, 15.72),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(
                name="Ada",
                location=Location("Ada Home", 44.8280, 15.722),
            ),
            Participant(
                name="Cluster One",
                location=Location("Cluster One", 44.7080, 15.908),
            ),
            Participant(
                name="Cluster Two",
                location=Location("Cluster Two", 44.7090, 15.91),
            ),
            Participant(
                name="Cluster Three",
                location=Location("Cluster Three", 44.7110, 15.912),
            ),
        ]
        destination = Location("Destination", 44.7350, 15.885)

        driver_sets = DriverSelector(participants, destination).select_driver_sets()
        detour_aware = next(driver_set for driver_set in driver_sets if driver_set.name == "Detour-Aware")

        self.assertEqual(set(detour_aware.driver_indices), {0, 1, 2})

    def test_driver_selection_always_includes_force_drive_alone_participant(self) -> None:
        participants = [
            Participant(
                name="Solo Driver",
                location=Location("Solo Driver Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=1),
                force_drive_alone=True,
            ),
            Participant(
                name="Shared Driver",
                location=Location("Shared Driver Home", 44.5200, 15.52),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(name="Passenger A", location=Location("Passenger A Home", 44.5300, 15.53)),
            Participant(name="Passenger B", location=Location("Passenger B Home", 44.5400, 15.54)),
        ]
        destination = Location("Destination", 44.6500, 15.65)

        driver_sets = DriverSelector(participants, destination).select_driver_sets()

        self.assertTrue(driver_sets)
        for driver_set in driver_sets:
            self.assertIn(0, driver_set.driver_indices)

    def test_select_best_result_detour_aware_prefers_fairer_plan(self) -> None:
        efficient_plan = TripResult(
            driver_set_name="Best Score",
            assignments=[],
            unassigned_passenger_indices=[],
            fitness=9.0,
            total_distance_km=40.0,
            total_cost_eur=10.0,
            max_route_distance_km=28.0,
            route_distance_spread_km=18.0,
            max_route_duration_min=42.0,
            route_duration_spread_min=29.0,
            max_detour_ratio=2.1,
        )
        fairer_plan = TripResult(
            driver_set_name="Detour-Aware",
            assignments=[object(), object(), object()],
            unassigned_passenger_indices=[],
            fitness=8.4,
            total_distance_km=48.0,
            total_cost_eur=12.5,
            max_route_distance_km=15.0,
            route_distance_spread_km=4.0,
            max_route_duration_min=18.0,
            route_duration_spread_min=6.0,
            max_detour_ratio=1.15,
        )
        efficient_plan.assignments = [object(), object()]

        self.assertEqual(select_best_result([efficient_plan, fairer_plan], "efficiency").driver_set_name, "Best Score")
        self.assertEqual(select_best_result([efficient_plan, fairer_plan], "detour_aware").driver_set_name, "Detour-Aware")
        self.assertEqual(select_best_result([efficient_plan, fairer_plan], "balanced_load").driver_set_name, "Detour-Aware")

    def test_select_best_result_detour_aware_prioritizes_route_km_over_time(self) -> None:
        lower_time_but_longer_km = TripResult(
            driver_set_name="Fast But Longer",
            assignments=[object(), object(), object()],
            unassigned_passenger_indices=[],
            fitness=8.8,
            total_distance_km=47.0,
            total_cost_eur=11.0,
            max_route_distance_km=24.0,
            route_distance_spread_km=9.0,
            max_route_duration_min=24.0,
            route_duration_spread_min=5.0,
            max_detour_ratio=1.55,
        )
        lower_km_burden = TripResult(
            driver_set_name="Km First",
            assignments=[object(), object(), object()],
            unassigned_passenger_indices=[],
            fitness=8.5,
            total_distance_km=43.0,
            total_cost_eur=11.4,
            max_route_distance_km=16.0,
            route_distance_spread_km=4.0,
            max_route_duration_min=28.0,
            route_duration_spread_min=8.0,
            max_detour_ratio=1.12,
        )

        self.assertEqual(
            select_best_result([lower_time_but_longer_km, lower_km_burden], "detour_aware").driver_set_name,
            "Km First",
        )

    def test_apca_detour_aware_does_not_need_even_passenger_split(self) -> None:
        participants = [
            Participant(
                name="Driver West",
                location=Location("Driver West Home", 44.7000, 15.8),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver East",
                location=Location("Driver East Home", 44.7000, 16.04),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver South",
                location=Location("Driver South Home", 44.5600, 15.92),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(name="West One", location=Location("West One", 44.7020, 15.805)),
            Participant(name="West Two", location=Location("West Two", 44.7040, 15.808)),
            Participant(name="West Three", location=Location("West Three", 44.7050, 15.812)),
            Participant(name="East One", location=Location("East One", 44.7030, 16.035)),
            Participant(name="East Two", location=Location("East Two", 44.7050, 16.03)),
            Participant(name="South One", location=Location("South One", 44.5750, 15.922)),
        ]
        destination = Location("Destination", 44.7000, 15.92)
        algorithm = APCAAlgorithm(
            participants,
            [0, 1, 2],
            destination,
            num_ants=2,
            num_iterations=2,
            plan_preference="detour_aware",
        )

        natural_but_uneven = {0: [0, 1, 2], 1: [3, 4], 2: [5]}
        more_even_but_worse = {0: [0, 1], 1: [2, 3], 2: [4, 5]}

        natural_fit = algorithm._evaluate(natural_but_uneven, [])
        even_fit = algorithm._evaluate(more_even_but_worse, [])

        self.assertGreater(natural_fit, even_fit)

    def test_apca_detour_aware_prefers_fewer_passengers_on_the_longer_route(self) -> None:
        participants = [
            Participant(
                name="Ivan",
                location=Location("Ivan Home", 44.8300, 15.72),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(
                name="Local Driver",
                location=Location("Local Driver Home", 44.7060, 15.906),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(name="Ada", location=Location("Ada Home", 44.8280, 15.722)),
            Participant(name="Cleo", location=Location("Cleo Home", 44.7090, 15.91)),
            Participant(name="Local Two", location=Location("Local Two Home", 44.7110, 15.912)),
        ]
        destination = Location("Destination", 44.7350, 15.885)
        algorithm = APCAAlgorithm(
            participants,
            [0, 1],
            destination,
            num_ants=2,
            num_iterations=2,
            plan_preference="detour_aware",
        )

        lighter_long_route = {0: [0], 1: [1, 2]}
        overloaded_long_route = {0: [0, 1], 1: [2]}

        lighter_fit = algorithm._evaluate(lighter_long_route, [])
        overloaded_fit = algorithm._evaluate(overloaded_long_route, [])

        self.assertGreater(lighter_fit, overloaded_fit)

    def test_apca_detour_aware_repair_moves_local_passenger_off_remote_car(self) -> None:
        participants = [
            Participant(
                name="Eli",
                location=Location("Eli Home", 44.821291, 15.785103),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=5),
            ),
            Participant(
                name="Ben",
                location=Location("Ben Home", 44.814790, 15.802285),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=5),
            ),
            Participant(
                name="Ivan",
                location=Location("Ivan Home", 44.713882, 15.965606),
                car=Car(fuel_type="diesel", consumption_l_per_100km=4.9, total_seats=4),
            ),
            Participant(name="Cleo", location=Location("Cleo Home", 44.808553, 15.814089)),
            Participant(name="Ada", location=Location("Ada Home", 44.574370, 15.981997)),
            Participant(name="Fay", location=Location("Fay Home", 44.811426, 15.803194)),
            Participant(name="Gus", location=Location("Gus Home", 44.814800, 15.802316)),
        ]
        destination = Location("Destination", 44.755000, 15.72)
        algorithm = APCAAlgorithm(
            participants,
            [0, 1, 2],
            destination,
            num_ants=2,
            num_iterations=2,
            plan_preference="detour_aware",
        )

        bad_assignment = {
            0: [2],
            1: [3],
            2: [1, 0],
        }

        repaired_assignment, repaired_unassigned, repaired_fitness = algorithm._repair_detour_aware_assignment(
            bad_assignment,
            [],
        )

        self.assertEqual(repaired_unassigned, [])
        self.assertNotIn(0, repaired_assignment[2])
        self.assertTrue(any(0 in repaired_assignment[driver_idx] for driver_idx in (0, 1)))
        self.assertGreater(repaired_fitness, algorithm._evaluate(bad_assignment, []))

    def test_target_arrival_guides_flexible_route_timing(self) -> None:
        participants = [
            Participant(
                name="Driver",
                location=Location("Driver Home", 44.7000, 15.7),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=3),
            ),
            Participant(name="Passenger", location=Location("Passenger Home", 44.7050, 15.705)),
        ]
        destination = Location("Destination", 44.7500, 15.75, target_arrival_time_min=21 * 60)
        algorithm = APCAAlgorithm(participants, [0], destination, num_ants=2, num_iterations=2)

        analysis = algorithm._route_time_analysis(0, [0])

        self.assertIsNotNone(analysis["destination_arrival_time"])
        self.assertEqual(analysis["destination_arrival_time"], "21:00")

    def test_time_text_to_minutes_accepts_generated_day_offset_suffix(self) -> None:
        self.assertEqual(time_text_to_minutes("03:15 (+1d)"), (27 * 60) + 15)
        self.assertEqual(time_text_to_minutes("22:05 (-1d)"), -(24 * 60) + (22 * 60) + 5)

    def test_apca_detour_aware_construct_solution_does_not_produce_complex_weights(self) -> None:
        participants = [
            Participant(
                name="Ben",
                location=Location("Ben Home", 44.814790, 15.802285),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=5),
            ),
            Participant(
                name="Fay",
                location=Location("Fay Home", 44.811426, 15.803194),
                car=Car(fuel_type="lpg", consumption_l_per_100km=7.9, total_seats=5),
            ),
            Participant(
                name="Ivan",
                location=Location("Ivan Home", 44.713882, 15.965606),
                car=Car(fuel_type="diesel", consumption_l_per_100km=4.9, total_seats=4),
            ),
            Participant(name="Cleo", location=Location("Cleo Home", 44.808553, 15.814089)),
            Participant(name="Ada", location=Location("Ada Home", 44.574370, 15.981997)),
            Participant(name="Dan", location=Location("Dan Home", 44.807324, 15.807411)),
            Participant(name="Gus", location=Location("Gus Home", 44.814800, 15.802316)),
            Participant(name="Hana", location=Location("Hana Home", 44.812830, 15.802386)),
        ]
        destination = Location("Destination", 44.755000, 15.72)
        algorithm = APCAAlgorithm(
            participants,
            [0, 1, 2],
            destination,
            num_ants=2,
            num_iterations=2,
            beta=2.5,
            plan_preference="detour_aware",
        )
        algorithm._init_pheromones()

        assignment, unassigned = algorithm._construct_solution()

        self.assertIsInstance(assignment, dict)
        self.assertIsInstance(unassigned, list)

    def test_run_optimization_applies_detour_aware_only_to_matching_driver_set(self) -> None:
        save_app_settings(
            num_ants=8,
            num_iterations=20,
            alpha=1.0,
            beta=2.5,
            rho=0.15,
            map_latitude=44.6471,
            map_longitude=10.9252,
            map_zoom=9,
            plan_return_separately=False,
            notifications_enabled=True,
        )
        participants = [
            Participant(
                name="Driver A",
                location=Location("Driver A Home", 44.5000, 15.5),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver B",
                location=Location("Driver B Home", 44.5200, 15.52),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
            Participant(
                name="Driver C",
                location=Location("Driver C Home", 44.5400, 15.54),
                car=Car(fuel_type="gasoline", consumption_l_per_100km=6.7, total_seats=4),
            ),
        ]
        destination = Location("Destination", 44.6500, 15.65)
        selection_payload = {
            "scores": [],
            "driver_sets": [
                DriverSet(name="Best Score", driver_indices=[0, 1], total_seats=6, scores={}),
                DriverSet(name="Detour-Aware", driver_indices=[0, 1, 2], total_seats=9, scores={}),
            ],
            "participants": participants,
            "destination": destination,
        }
        efficient_result = TripResult(
            driver_set_name="Best Score",
            assignments=[
                RouteAssignment(0, [], ["Driver A", "Destination"], 30.0, 6.0, 6.0, route_duration_min=44.0),
                RouteAssignment(1, [], ["Driver B", "Destination"], 11.0, 3.0, 3.0, route_duration_min=12.0),
            ],
            unassigned_passenger_indices=[],
            fitness=9.2,
            total_distance_km=41.0,
            total_cost_eur=9.0,
        )
        balanced_result = TripResult(
            driver_set_name="Detour-Aware",
            assignments=[
                RouteAssignment(0, [], ["Driver A", "Destination"], 14.0, 3.0, 3.0, route_duration_min=18.0),
                RouteAssignment(1, [], ["Driver B", "Destination"], 13.0, 3.0, 3.0, route_duration_min=16.0),
                RouteAssignment(2, [], ["Driver C", "Destination"], 12.0, 3.0, 3.0, route_duration_min=15.0),
            ],
            unassigned_passenger_indices=[],
            fitness=8.5,
            total_distance_km=39.0,
            total_cost_eur=9.0,
        )

        with patch("app.services.planner.run_driver_selection", return_value=selection_payload), \
             patch("app.services.planner.list_participants", return_value=[{"id": 1}, {"id": 2}, {"id": 3}]), \
             patch("app.services.planner.get_pickup_order_rules", return_value=[]), \
             patch("app.services.planner.build_route_matrix", return_value={"distance_km": [], "duration_min": []}), \
             patch("app.services.planner._apply_assignment_route_metrics", side_effect=lambda assignment, *_args: assignment), \
             patch("app.services.planner._hydrate_missing_route_timings", return_value=None), \
             patch("app.services.planner.APCAAlgorithm") as apca_cls:
            apca_cls.return_value.solve.side_effect = [efficient_result, balanced_result]

            optimization = run_optimization()

        first_call = apca_cls.call_args_list[0].kwargs
        second_call = apca_cls.call_args_list[1].kwargs
        self.assertEqual(first_call["plan_preference"], "efficiency")
        self.assertEqual(second_call["plan_preference"], "detour_aware")
        self.assertEqual(optimization["plan_preference"], "efficiency")
        self.assertEqual(optimization["best_result"].driver_set_name, "Best Score")
        detour_aware_result = next(result for result in optimization["trip_results"] if result.driver_set_name == "Detour-Aware")
        self.assertEqual(detour_aware_result.plan_preference_used, "detour_aware")
        self.assertGreater(optimization["best_result"].max_route_duration_min, 0.0)

    def test_duplicate_last_trip_creates_backup(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Saved Destination", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Saved Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "4",
            },
        )
        self.client.post(
            "/participants",
            data={
                "name": "Saved Carla",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
            },
        )
        self.client.post(
            "/history/save",
            data={"trip_name": "Stored Trip"},
            follow_redirects=True,
        )
        self.client.post(
            "/participants",
            data={
                "name": "Current Bob",
                "location_name": "Bologna",
                "latitude": "44.4949",
                "longitude": "16.3426",
            },
        )

        response = self.client.post("/history/duplicate", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Latest past trip loaded.", response.text)

        backup = get_workspace_backup()
        self.assertIsNotNone(backup)
        self.assertEqual(backup["reason"], "duplicate_last_trip")
        backup_names = [participant["name"] for participant in backup["snapshot"]["participants"]]
        self.assertIn("Current Bob", backup_names)

    def test_workspace_backup_round_trips_through_import(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "15.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "15.631",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "5",
            },
        )
        # A second (car-less) participant is needed so run_optimization has a
        # driver+passenger pair to solve — /history/save runs optimization if
        # no cached result exists yet.
        self.client.post(
            "/participants",
            data={
                "name": "Bob",
                "location_name": "Bologna",
                "latitude": "44.4949",
                "longitude": "16.3426",
            },
        )
        self.client.post("/groups", data={"group_name": "Weekend crew"})
        self.client.post("/history/save", data={"trip_name": "Test Trip"})
        exported = self.client.get("/workspace/export").text

        clear_all_data()
        execute("DELETE FROM saved_groups")
        execute("DELETE FROM trip_history")
        self.assertEqual(list_participants(), [])

        response = self.client.post(
            "/workspace/import",
            files={"backup_file": ("dmproject-workspace.json", exported, "application/json")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([person["name"] for person in list_participants()], ["Alice", "Bob"])
        self.assertEqual([group["name"] for group in list_groups()], ["Weekend crew"])
        self.assertEqual([entry["trip_name"] for entry in list_trip_history()], ["Test Trip"])

    def test_workspace_import_rejects_a_foreign_file(self) -> None:
        response = self.client.post(
            "/workspace/import",
            files={"backup_file": ("notes.json", '{"hello": "world"}', "application/json")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a Drivers Manager workspace backup", response.text)


    def test_participant_vehicle_type_round_trips(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Nico",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "habit_score": "0",
                "has_car": "true",
                "vehicle_type": "motorbike",
                "fuel_type": "gasoline",
                "consumption_l_per_100km": "4",
                "total_seats": "2",
            },
        )
        participant = list_participants()[0]
        self.assertEqual(get_participant(participant["id"])["vehicle_type"], "motorbike")
        self.assertIn("Motorbike · gasoline / 2 seats", self.client.get("/plan").text)

    def test_motorbike_rejects_more_than_two_seats(self) -> None:
        response = self.client.post(
            "/participants",
            data={
                "name": "Nico",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "habit_score": "0",
                "has_car": "true",
                "vehicle_type": "motorbike",
                "fuel_type": "gasoline",
                "total_seats": "4",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("A motorbike seats at most two people.", response.text)

    def test_guest_motorbike_rejects_more_than_two_seats(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        response = self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Rider",
                "location_name": "Rider Home",
                "latitude": "44.760000",
                "longitude": "15.7",
                "has_car": "true",
                "vehicle_type": "motorbike",
                "fuel_type": "gasoline",
                "total_seats": "5",
                "role_tag": "standard",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("A motorbike seats at most two people.", response.text)

    def test_vehicle_type_column_is_added_to_a_legacy_database(self) -> None:
        save_participant(
            participant_id=None,
            name="Legacy Luca",
            address_text=None,
            location_name="Carpi",
            latitude=44.7839,
            longitude=15.8853,
            has_car=False,
            fuel_type=None,
            consumption_l_per_100km=None,
            total_seats=None,
            habit_score=0.0,
        )
        database.execute("ALTER TABLE participants DROP COLUMN vehicle_type")
        columns = {row["name"] for row in database.fetch_all("PRAGMA table_info(participants)")}
        self.assertNotIn("vehicle_type", columns)

        init_db()

        columns = {row["name"] for row in database.fetch_all("PRAGMA table_info(participants)")}
        self.assertIn("vehicle_type", columns)
        self.assertEqual(list_participants()[0]["vehicle_type"], "car")

    def test_csv_import_reads_vehicle_type(self) -> None:
        response = self.client.post(
            "/participants/import",
            files={
                "csv_file": (
                    "people.csv",
                    "name,latitude,longitude,has_car,vehicle_type,fuel_type,total_seats\n"
                    "Nico,44.6471,10.9252,true,motorbike,gasoline,2\n",
                    "text/csv",
                )
            },
            data={"replace_existing": "false"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list_participants()[0]["vehicle_type"], "motorbike")

    def test_csv_export_includes_vehicle_type(self) -> None:
        response = self.client.get("/participants/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("vehicle_type", response.text.splitlines()[0])

    def test_guest_motorbike_is_imported_as_motorbike(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Arrogant Pub", "latitude": "44.689953", "longitude": "15.648573"},
        )
        self.client.post("/invites", data={"trip_name": "Pub night intake"})
        invite = list_trip_invites()[0]
        self.client.post(
            f"/invite/{invite['token']}",
            data={
                "name": "Guest Rider",
                "location_name": "Rider Home",
                "latitude": "44.760000",
                "longitude": "15.7",
                "has_car": "true",
                "vehicle_type": "motorbike",
                "fuel_type": "gasoline",
                "total_seats": "2",
                "role_tag": "standard",
            },
        )
        guest_response = list_trip_responses(invite["id"])[0]
        self.assertEqual(guest_response["vehicle_type"], "motorbike")

        self.client.post(f"/invites/{invite['id']}/responses/{guest_response['id']}/import")

        participant = next(row for row in list_participants() if row["name"] == "Guest Rider")
        self.assertEqual(participant["vehicle_type"], "motorbike")

    def test_workspace_export_keeps_vehicle_type(self) -> None:
        self.client.post(
            "/participants",
            data={
                "name": "Nico",
                "location_name": "Modena",
                "latitude": "44.6471",
                "longitude": "15.9252",
                "has_car": "true",
                "vehicle_type": "motorbike",
                "fuel_type": "gasoline",
                "total_seats": "2",
            },
        )
        exported = self.client.get("/workspace/export").text

        clear_all_data()
        self.assertEqual(list_participants(), [])

        response = self.client.post(
            "/workspace/import",
            files={"backup_file": ("dmproject-workspace.json", exported, "application/json")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list_participants()[0]["vehicle_type"], "motorbike")


if __name__ == "__main__":
    unittest.main()
