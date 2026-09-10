"""FastAPI entrypoint for the local-first Drivers Manager web app."""

from __future__ import annotations

import logging
import base64
from datetime import date
from core.logging_config import LOG_INFO, LOG_DEBUG, init_logging
from html import escape
import math
from pathlib import Path
import threading
import time
from time import perf_counter
from urllib.parse import quote, urlparse
from urllib.request import Request as UrlRequest, urlopen

import io

import qrcode
import qrcode.image.svg

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.geocoding import search_addresses
from app.mapping import reverse_geocode, build_route_preview
from app.pois import FoodLookupUnavailable, find_food_along_route
from app.costs import sync_fuel_prices
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    clear_password,
    create_session,
    destroy_session,
    is_password_set,
    is_valid_session,
    set_password,
    verify_password,
)
from app.services.notifier import get_notifier_settings, queue_notification_event
from app.time_utils import normalize_time_text, time_text_to_minutes
from app.i18n import SUPPORTED_LANGUAGES, normalize_language, translate
from app.services import (
    SOLVER_CATALOG,
    TRIP_PRESETS,
    apply_group,
    apply_saved_destination,
    build_participants_export_filename,
    build_map_payload,
    cache_optimization_result,
    cache_selection_result,
    clear_all_data,
    delete_pickup_order_rule,
    delete_ride_together_rule,
    delete_group,
    delete_participant,
    delete_saved_destination,
    delete_trip_history_entry,
    create_trip_invite,
    duplicate_last_trip,
    export_participants_csv,
    export_workspace_backup,
    get_app_settings,
    get_cached_optimization_result,
    get_cached_selection_result,
    get_current_dataset_context,
    get_destination,
    get_meetup_spot,
    get_participant,
    get_pickup_order_rules,
    get_ride_together_rules,
    get_trip_invite,
    get_trip_invite_by_token,
    get_trip_response_by_token,
    get_trip_history_entry,
    get_workspace_backup,
    import_all_trip_responses,
    import_trip_response,
    import_workspace_backup,
    import_participants_csv,
    list_groups,
    list_saved_meetup_spots,
    list_participants,
    list_saved_destinations,
    list_trip_invites,
    list_trip_responses,
    list_trip_history,
    load_sample_dataset,
    load_invite_session_into_workspace,
    mark_current_dataset_context_customized,
    resolve_location_input,
    reorder_participants,
    restore_workspace_backup,
    restore_trip_history_entry,
    run_driver_selection,
    run_optimization,
    run_sandbox_optimization,
    save_app_settings,
    save_public_access_url,
    save_public_access_urls,
    save_destination,
    save_destination_favorite,
    save_group,
    save_meetup_spot,
    save_pickup_order_rule,
    save_ride_together_rule,
    save_participant,
    save_trip_response,
    set_participant_trip_active,
    save_trip_history,
    delete_meetup_spot,
)
from core.config import DEFAULT_SOLVER, ESTIMATED_AVERAGE_SPEED_KMH, FUEL_TYPES, FUEL_TYPE_DEFAULT_CONSUMPTION, VEHICLE_TYPES
from core.models import Car, Location, Participant
from core.utils import haversine_distance


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)
FLASH_MESSAGES = {
    "destination-saved": "Destination saved.",
    "destination-favorite-saved": "Destination saved to favorites.",
    "selection-completed": "Driver selection completed.",
    "optimization-completed": "Full optimization completed.",
    "workspace-backup-restored": "Undid the last replace. Your previous trip is back.",
    "history-duplicated": "Latest past trip loaded. Your previous trip was saved first.",
    "history-restored": "Past trip loaded. Your previous trip was saved first.",
    "history-deleted": "Past trip deleted.",
    "invite-workspace-loaded": "Guest session loaded as the current trip. Your previous trip was saved first.",
    "security-updated": "Organizer password saved. Admin pages now require login.",
    "security-removed": "Organizer password removed. The app is open again.",
    "workspace-imported": "Workspace backup imported. Your current trip was saved first.",
}
LANGUAGE_COOKIE_NAME = "dmproject_lang"
STATIC_ASSET_VERSION = "20260910-solvers-merge"
FOOD_STOP_VOTE_OPTIONS = {
    "mcdonalds": "🍟 McDonald's",
    "burger_king": "🍔 Burger King",
    "kfc": "🍗 KFC",
    "kebab": "🌯 Kebab",
    "pizza": "🍕 Pizza",
    "cafe": "☕ Coffee",
    "no_stop": "🚗 No stop, drive straight",
}
APP_PAGES = {
    "home": {"label": "Home", "title": "Current trip", "description": "Where tonight's trip stands.", "path": "/"},
    "plan": {"label": "Plan", "title": "Plan", "description": "Map, people, places, rules, and results.", "path": "/plan"},
    "share": {"label": "Share", "title": "Share", "description": "Guest invites and the plan to send.", "path": "/share"},
    "trips": {"label": "Trips", "title": "Past trips", "description": "Load or delete past trips.", "path": "/trips"},
    "settings": {"label": "Settings", "title": "Settings", "description": "Defaults and notifications.", "path": "/settings"},
}
LEGACY_PAGE_REDIRECTS = {
    "/setup": "/plan?tab=people",
    "/planning": "/plan?tab=results",
    "/guest-links": "/share",
    "/history": "/trips",
}
WORKSPACE_TABS = [
    {"slug": "people", "label": "People"},
    {"slug": "places", "label": "Places"},
    {"slug": "rules", "label": "Rules"},
    {"slug": "results", "label": "Results"},
]
# Which workspace tab a scroll target lives in. None = the map column (no tab change).
SCROLL_TARGET_TABS = {
    "destination-panel": "places",
    "meetup-spots-panel": "places",
    "participants-panel": "people",
    "map-panel": None,
    "driver-selection-section": "results",
    "optimization-results-section": "results",
    "planner-return-planning": "rules",
    "planner-pickup-rules": "rules",
    "planner-ride-together-rules": "rules",
}
SCROLL_TARGET_PAGES = {
    **{target: "plan" for target in SCROLL_TARGET_TABS},
    "guest-invites-panel": "share",
    "advanced-settings-panel": "settings",
    "trip-history-panel": "trips",
}


def build_next_action(onboarding: dict[str, bool], has_plan: bool) -> dict[str, str]:
    """Pick the single most useful next step for the home page."""
    if not onboarding["has_destination"]:
        return {"label": "Set a destination", "href": "/plan?tab=places"}
    if not onboarding["has_participants"]:
        return {"label": "Add people", "href": "/plan?tab=people"}
    if not onboarding["has_driver"]:
        return {"label": "Mark a driver", "href": "/plan?tab=people"}
    if not has_plan:
        return {"label": "Run the plan", "href": "/optimization", "method": "post"}
    return {"label": "Share the plan", "href": "/share"}


app = FastAPI(title="Drivers Manager")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/welcome", response_class=HTMLResponse)
def landing_page(request: Request):
    """Render the marketing/pitch landing page."""
    return templates.TemplateResponse(request, "landing.html", _template_context(request))


def _safe_next_target(target: str) -> str:
    """Only allow same-site relative redirect targets after login."""
    cleaned = (target or "").strip()
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        return cleaned
    return "/"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    """Render the organizer login page."""
    if not is_password_set() or _is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        _template_context(request, next_target=_safe_next_target(next), login_error=None),
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(""), next_target: str = Form("/")):
    """Verify the organizer password and start a session."""
    if not is_password_set():
        return RedirectResponse("/", status_code=303)
    if not verify_password(password):
        LOG_INFO("failed organizer login attempt")
        return templates.TemplateResponse(
            request,
            "login.html",
            _template_context(
                request,
                next_target=_safe_next_target(next_target),
                login_error=_translate_request(request, "Wrong password. Try again."),
            ),
            status_code=401,
        )
    token = create_session()
    response = RedirectResponse(_safe_next_target(next_target), status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    LOG_INFO("organizer logged in")
    return response


@app.post("/logout")
def logout(request: Request):
    """End the current organizer session."""
    destroy_session(request.cookies.get(SESSION_COOKIE_NAME, ""))
    response = RedirectResponse("/login" if is_password_set() else "/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.post("/settings/security")
def update_security_settings(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    """Set, change, or remove the organizer password."""
    if is_password_set() and not verify_password(current_password):
        return render_home(request, status_code=400, error=_translate_request(request, "Current password is wrong."))
    if new_password:
        if len(new_password) < 8:
            return render_home(request, status_code=400, error=_translate_request(request, "The new password needs at least 8 characters."))
        if new_password != confirm_password:
            return render_home(request, status_code=400, error=_translate_request(request, "The two passwords do not match."))
        set_password(new_password)
        token = create_session()
        response = _redirect_to_page(request, page="settings", flash="security-updated", scroll="security-panel")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=SESSION_TTL_DAYS * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return response
    if not is_password_set():
        return render_home(request, status_code=400, error=_translate_request(request, "Enter a new password to enable protection."))
    clear_password()
    response = _redirect_to_page(request, page="settings", flash="security-removed", scroll="security-panel")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest() -> FileResponse:
    """Serve the PWA manifest from the root so its scope covers the whole app."""
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def pwa_service_worker() -> FileResponse:
    """Serve the service worker from the root so it can control every page."""
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="text/javascript")


@app.on_event("startup")
def on_startup() -> None:
    """Prepare the local SQLite schema."""
    init_logging()
    LOG_INFO("application startup — initialising database")
    init_db()
    LOG_INFO("database ready — starting background fuel-price sync")
    threading.Thread(target=sync_fuel_prices, kwargs={"force": False}, daemon=True).start()
    LOG_INFO("startup complete — server is ready")


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    """Log lightweight request timings for local debugging."""
    start = perf_counter()
    LOG_DEBUG(f"-> {request.method} {request.url.path}")
    response = await call_next(request)
    elapsed = perf_counter() - start
    LOG_INFO(f"{request.method} {request.url.path} -> {response.status_code} in {elapsed:.3f}s")
    return response


def _client_ip(request: Request) -> str:
    """Return the best-effort client IP, honoring common proxy headers."""
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client and request.client.host else "unknown"


def _queue_guest_visit_notification(request: Request, invite: dict[str, object]) -> None:
    """Notify when a guest opens the public invite form."""
    if _public_request_mode(request) != "guest":
        return
    notification_settings = get_notifier_settings()
    if not notification_settings.get("notify_guest_visits", True):
        return
    client_ip = _client_ip(request)
    time_bucket = int(time.time() // 15)
    queue_notification_event(
        event_type="guest_visit",
        title="Guest Link Opened",
        message=f"Someone opened the guest form for '{invite['trip_name']}' from {client_ip}.",
        dedupe_key=f"guest_visit:{invite['id']}:{client_ip}:{time_bucket}",
        dedupe_window_seconds=15,
    )


def _queue_guest_submission_notification(request: Request, invite: dict[str, object], participant_name: str, *, is_update: bool) -> None:
    """Notify when a guest submits or updates their response."""
    if _public_request_mode(request) != "guest":
        return
    notification_settings = get_notifier_settings()
    if not notification_settings.get("notify_guest_submissions", True):
        return
    client_ip = _client_ip(request)
    action_label = "updated" if is_update else "submitted"
    queue_notification_event(
        event_type="guest_submission",
        title=f"Guest Response {action_label.title()}",
        message=f"{participant_name} {action_label} details for '{invite['trip_name']}' from {client_ip}.",
    )


def _queue_admin_public_notification(request: Request, *, activity_label: str) -> None:
    """Notify when the admin public hostname is used, but stay silent on localhost."""
    if _public_request_mode(request) != "admin":
        return
    client_ip = _client_ip(request)
    request_path = request.url.path
    request_method = request.method.upper()
    queue_notification_event(
        event_type=f"admin_public_{activity_label}",
        title="Admin Public Link Activity",
        message=f"{request_method} {request_path} via the admin public link from {client_ip}.",
    )


def _host_from_public_url(public_url: str | None) -> str:
    """Extract a lowercase host from a saved public URL."""
    cleaned = str(public_url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    return parsed.netloc.lower()


def _public_request_mode(request: Request) -> str | None:
    """Classify the request as guest/admin public access based on the saved hosts."""
    sharing = get_app_settings().get("sharing", {})
    guest_host = _host_from_public_url(sharing.get("guest_public_base_url"))
    admin_host = _host_from_public_url(sharing.get("admin_public_base_url"))
    if not guest_host and not admin_host:
        return None
    request_host = request.headers.get("host", "").strip().lower()
    request_host = request_host.split(":", 1)[0]
    if not request_host:
        return None
    if admin_host and request_host == admin_host:
        return "admin"
    if guest_host and request_host == guest_host:
        return "guest"
    return None


def _is_allowed_public_guest_route(request: Request) -> bool:
    """Only expose guest-safe routes through the public tunnel."""
    path = request.url.path
    method = request.method.upper()
    if path == "/":
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/invite/"):
        return True
    if path in {"/manifest.webmanifest", "/sw.js"}:
        return True
    if method == "GET" and path in {"/api/search-address", "/api/reverse-geocode"}:
        return True
    return False


def _normalize_app_page(page: str | None) -> str:
    """Return a supported top-level app page slug."""
    if not page:
        return "home"
    cleaned = str(page).strip().lower()
    return cleaned if cleaned in APP_PAGES else "home"


def _page_from_path(path: str | None) -> str | None:
    """Infer the active app page from a request path."""
    if not path:
        return None
    normalized = path.rstrip("/") or "/"
    for slug, meta in APP_PAGES.items():
        if meta["path"] == normalized:
            return slug
    return None


def _infer_request_page(request: Request, explicit_page: str | None = None, scroll_target: str | None = None) -> str:
    """Best-effort page inference for direct renders and redirect flows."""
    if explicit_page:
        return _normalize_app_page(explicit_page)
    query_page = request.query_params.get("page")
    if query_page:
        return _normalize_app_page(query_page)
    inferred_from_path = _page_from_path(request.url.path)
    if inferred_from_path:
        return inferred_from_path
    if scroll_target and scroll_target in SCROLL_TARGET_PAGES:
        return SCROLL_TARGET_PAGES[scroll_target]
    referer = request.headers.get("referer")
    if referer:
        try:
            referer_page = _page_from_path(urlparse(referer).path)
        except ValueError:
            referer_page = None
        if referer_page:
            return referer_page
    return "home"


def _build_page_url(page: str, flash: str | None = None, scroll: str | None = None) -> str:
    """Build an internal app URL for one of the top-level pages."""
    target_page = _normalize_app_page(page)
    base_path = APP_PAGES[target_page]["path"]
    query_parts: list[str] = []
    if flash:
        query_parts.append(f"flash={quote(flash)}")
    if scroll:
        query_parts.append(f"scroll={quote(scroll)}")
    if query_parts:
        return f"{base_path}?{'&'.join(query_parts)}"
    return str(base_path)


def _get_request_language(request: Request) -> str:
    """Resolve the current UI language from cookies."""
    return normalize_language(request.cookies.get(LANGUAGE_COOKIE_NAME))


def _translate_request(request: Request, key: str, **kwargs: object) -> str:
    """Translate one string for the current request."""
    return translate(_get_request_language(request), key, **kwargs)


def _base_template_context(request: Request) -> dict[str, object]:
    """Shared context for translated templates."""
    current_language = _get_request_language(request)
    language_flags = {
        "en": "🇬🇧",
        "it": "🇮🇹",
        "fr": "🇫🇷",
        "es": "🇪🇸",
    }
    redirect_to = request.url.path
    if request.url.query:
        redirect_to = f"{redirect_to}?{request.url.query}"
    return {
        "request": request,
        "current_language": current_language,
        "current_language_flag": language_flags.get(current_language, "🌐"),
        "supported_languages": [
            {"code": code, "label": label, "flag": language_flags.get(code, "🌐")}
            for code, label in SUPPORTED_LANGUAGES.items()
        ],
        "language_redirect_to": redirect_to,
        "static_asset_version": STATIC_ASSET_VERSION,
        "food_stop_options": FOOD_STOP_VOTE_OPTIONS,
        # Base context, not build_context: the guest invite form needs it too.
        "vehicle_types": VEHICLE_TYPES,
        "auth_enabled": is_password_set(),
        "is_authenticated": _is_authenticated(request),
        "t": lambda key, **kwargs: translate(current_language, key, **kwargs),
    }


def _template_context(request: Request, **extra: object) -> dict[str, object]:
    """Merge template-specific data with shared translated context."""
    return _base_template_context(request) | extra


def _redirect_to_page(
    request: Request,
    page: str | None = None,
    flash: str | None = None,
    scroll: str | None = None,
) -> RedirectResponse:
    """Redirect the user back to the most relevant app page."""
    resolved_page = _infer_request_page(request, explicit_page=page, scroll_target=scroll)
    return RedirectResponse(_build_page_url(resolved_page, flash=flash, scroll=scroll), status_code=303)


@app.post("/language")
def update_language(request: Request, language: str = Form("en"), redirect_to: str = Form("/")):
    """Persist the selected UI language in a cookie and return to the current page."""
    target = redirect_to.strip() or "/"
    if not target.startswith("/"):
        target = "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(LANGUAGE_COOKIE_NAME, normalize_language(language), max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


_LOGIN_EXEMPT_PATHS = {
    "/login",
    "/welcome",
    "/language",
    "/manifest.webmanifest",
    "/sw.js",
    "/api/search-address",
    "/api/reverse-geocode",
}


def _is_authenticated(request: Request) -> bool:
    """Return True when the request carries a valid organizer session."""
    return is_valid_session(request.cookies.get(SESSION_COOKIE_NAME, ""))


def _requires_login(request: Request) -> bool:
    """Decide whether this request must be redirected to the login page."""
    if not is_password_set():
        return False
    if _public_request_mode(request) == "guest":
        return False  # the guest tunnel is already restricted to token routes
    path = request.url.path
    if path.startswith("/static/") or path.startswith("/invite/"):
        return False
    if path in _LOGIN_EXEMPT_PATHS:
        return False
    return not _is_authenticated(request)


@app.middleware("http")
async def require_organizer_login(request: Request, call_next):
    """Gate admin pages behind the organizer password once one is set."""
    if _requires_login(request):
        if request.method.upper() == "GET":
            next_target = request.url.path
            if request.url.query:
                next_target = f"{next_target}?{request.url.query}"
            return RedirectResponse(f"/login?next={quote(next_target)}", status_code=303)
        return HTMLResponse(_translate_request(request, "Please log in first."), status_code=401)
    return await call_next(request)


@app.middleware("http")
async def restrict_public_tunnel_access(request: Request, call_next):
    """Treat the public tunnel as a guest-only portal while localhost stays fully editable."""
    mode = _public_request_mode(request)
    if mode == "admin":
        response = await call_next(request)
        if request.method.upper() == "GET" and request.url.path == "/" and response.status_code < 400:
            _queue_admin_public_notification(request, activity_label="visit")
        elif request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
            _queue_admin_public_notification(request, activity_label="activity")
        return response
    if mode != "guest":
        return await call_next(request)
    if _is_allowed_public_guest_route(request):
        return await call_next(request)
    if request.method.upper() == "GET":
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_translate_request(request, "This public link only allows guest invite access."), status_code=403)


def build_context(request: Request, **extra: object) -> dict[str, object]:
    """Assemble the template context for the main page."""
    participants = list_participants()
    destination = get_destination()
    active_participants = [participant for participant in participants if participant["active_in_trip"]]
    participant_name_by_id = {participant["id"]: participant["name"] for participant in participants}
    pickup_order_rules = [
        {
            "index": index,
            "before_id": rule["before_id"],
            "after_id": rule["after_id"],
            "before_name": participant_name_by_id.get(rule["before_id"], "Unknown participant"),
            "after_name": participant_name_by_id.get(rule["after_id"], "Unknown participant"),
        }
        for index, rule in enumerate(get_pickup_order_rules())
    ]
    ride_together_rules = [
        {
            "index": index,
            "first_id": rule["first_id"],
            "second_id": rule["second_id"],
            "first_name": participant_name_by_id.get(rule["first_id"], "Unknown participant"),
            "second_name": participant_name_by_id.get(rule["second_id"], "Unknown participant"),
        }
        for index, rule in enumerate(get_ride_together_rules())
    ]
    driver_count = sum(1 for participant in participants if participant["has_car"])
    active_driver_count = sum(1 for participant in active_participants if participant["has_car"])
    defaults = get_app_settings()
    guest_public_base_url = defaults.get("sharing", {}).get("guest_public_base_url", "")
    admin_public_base_url = defaults.get("sharing", {}).get("admin_public_base_url", "")
    current_dataset = get_current_dataset_context()
    trip_invites = []
    planner_dataset_status = None
    for invite in list_trip_invites():
        public_link = _build_invite_public_link(str(invite["token"]), guest_public_base_url)
        is_active_dataset = (
            current_dataset is not None
            and current_dataset.get("dataset_type") == "invite"
            and current_dataset.get("dataset_id") == invite["id"]
        )
        synced_response_count = current_dataset.get("response_count") if is_active_dataset else None
        invite = invite | {
            "public_link": public_link,
            "whatsapp_url": f"https://wa.me/?text={quote(public_link)}" if guest_public_base_url else "",
            "is_active_dataset": is_active_dataset,
            "has_new_responses": is_active_dataset and invite.get("response_count", 0) != synced_response_count,
            "synced_response_count": synced_response_count,
        }
        if is_active_dataset:
            planner_dataset_status = {
                "label": "Guest session workspace",
                "detail": (
                    "New guest updates available"
                    if invite["has_new_responses"]
                    else "Synced with latest guest responses"
                ),
                "is_warning": bool(invite["has_new_responses"]),
            }
        trip_invites.append(invite)
    if current_dataset is not None and planner_dataset_status is None:
        planner_dataset_status = {
            "label": "Current trip",
            "detail": (
                f"{current_dataset['dataset_name']} customized locally"
                if current_dataset.get("customized")
                else str(current_dataset["dataset_name"])
            ),
            "is_warning": False,
        }
    active_page = _infer_request_page(
        request,
        explicit_page=extra.get("active_page") if isinstance(extra.get("active_page"), str) else None,
        scroll_target=extra.get("scroll_target") if isinstance(extra.get("scroll_target"), str) else None,
    )
    base = _base_template_context(request)
    onboarding = {
        "has_destination": destination is not None,
        "has_participants": len(active_participants) > 0,
        "has_driver": active_driver_count > 0,
        "has_history": len(list_trip_history()) > 0,
    }
    return {
        "participants": participants,
        "active_participants": active_participants,
        "destination": destination,
        "fuel_types": FUEL_TYPES,
        "fuel_type_defaults": FUEL_TYPE_DEFAULT_CONSUMPTION,
        "driver_count": active_driver_count,
        "passenger_count": len(participants) - driver_count,
        "total_participant_count": len(participants),
        "active_participant_count": len(active_participants),
        "inactive_participant_count": len(participants) - len(active_participants),
        "saved_groups": list_groups(),
        "saved_destinations": list_saved_destinations(),
        "saved_meetup_spots": list_saved_meetup_spots(),
        "current_dataset": current_dataset,
        "trip_invites": trip_invites,
        "guest_public_base_url": guest_public_base_url,
        "admin_public_base_url": admin_public_base_url,
        "pickup_order_rules": pickup_order_rules,
        "ride_together_rules": ride_together_rules,
        "trip_history": list_trip_history(),
        "workspace_backup": get_workspace_backup(),
        "planner_dataset_status": planner_dataset_status,
        "trip_presets": TRIP_PRESETS,
        "settings": defaults,
        "solver_catalog": SOLVER_CATALOG,
        "editing_participant": None,
        "field_errors": {},
        "onboarding": onboarding,
        "onboarding_complete": all(onboarding.values()),
        "next_action": build_next_action(onboarding, extra.get("optimization") is not None),
        "pending_guest_replies": sum(
            max(int(invite.get("response_count") or 0) - int(invite.get("imported_count") or 0), 0)
            for invite in trip_invites
            if invite.get("status") == "open"
        ),
        "active_page": active_page,
        "page_links": [
            {
                "slug": slug,
                "label": meta["label"],
                "title": meta["title"],
                "description": meta["description"],
                "path": meta["path"],
                "is_active": slug == active_page,
            }
            for slug, meta in APP_PAGES.items()
        ],
        "current_page": {
            "slug": active_page,
            **APP_PAGES[active_page],
        },
        # Error re-renders pass only a scroll target, so derive the tab from it:
        # otherwise a failed form is hidden behind the default People panel.
        "active_tab": SCROLL_TARGET_TABS.get(str(extra.get("scroll_target") or "")) or "people",
        "workspace_tabs": WORKSPACE_TABS,
        **base,
        **extra,
    }


def render_home(request: Request, status_code: int = 200, **extra: object):
    """Render the main page with shared context."""
    if isinstance(extra.get("error"), str):
        extra["error"] = _translate_request(request, str(extra["error"]))
    if isinstance(extra.get("success"), str):
        extra["success"] = _translate_request(request, str(extra["success"]))
    return templates.TemplateResponse(
        request,
        "index.html",
        build_context(request, **extra),
        status_code=status_code,
    )


@app.get("/plan")
def plan_page(request: Request):
    """Render the map workspace."""
    return _render_app_page(request, "plan")


@app.get("/share")
def share_page(request: Request):
    """Render guest invites and plan sharing."""
    return _render_app_page(request, "share")


@app.get("/trips")
def trips_page(request: Request):
    """Render past trips."""
    return _render_app_page(request, "trips")


for _old_path, _new_location in LEGACY_PAGE_REDIRECTS.items():
    app.add_api_route(
        _old_path,
        (lambda location: (lambda: RedirectResponse(location, status_code=303)))(_new_location),
        methods=["GET"],
        include_in_schema=False,
    )


@app.get("/settings")
def settings_page(request: Request):
    """Render the settings workspace."""
    return _render_app_page(request, "settings")


def _resolve_share_context(route_set_index: int | None):
    """Return the cached optimization payload plus the chosen trip result to share."""
    optimization = get_cached_optimization_result()
    if optimization is None:
        computed = run_optimization()
        cache_optimization_result(computed)
        optimization = get_cached_optimization_result()
    if optimization is None or not optimization.get("trip_results"):
        return None, None, None
    trip_results = optimization["trip_results"]
    if route_set_index is None or route_set_index < 0 or route_set_index >= len(trip_results):
        route_set_index = next(
            (
                index
                for index, trip in enumerate(trip_results)
                if trip["driver_set_name"] == optimization.get("best_result", {}).get("driver_set_name")
            ),
            0,
        )
    chosen_trip = trip_results[route_set_index]
    return optimization, chosen_trip, route_set_index


def _build_share_summary(chosen_trip: dict[str, object], optimization: dict[str, object], destination: dict[str, object] | None) -> str:
    """Build a concise text summary suitable for WhatsApp sharing."""
    lines = [
        f"Carpool plan: {chosen_trip['driver_set_name']}",
        f"Destination: {destination['name']}" if destination else "Destination: not set",
        f"Fitness: {chosen_trip['fitness']:.4f}",
        f"Distance: {chosen_trip['total_distance_km']:.2f} km",
        f"Estimated cost: EUR {chosen_trip['total_cost_eur']:.2f}",
        "",
        "Routes:",
    ]
    participants = optimization["participants"]
    for assignment in chosen_trip["assignments"]:
        driver_name = participants[assignment["driver_index"]]["name"]
        passenger_names = [participants[index]["name"] for index in assignment["passenger_indices"]]
        rider_text = ", ".join(passenger_names) if passenger_names else "solo drive"
        lines.append(
            f"- {driver_name}: {rider_text} | {assignment['route_distance_km']:.1f} km | EUR {assignment['route_cost_eur']:.2f}"
        )
        if assignment.get("outbound_departure_time"):
            lines.append(f"  departs {assignment['outbound_departure_time']}")
        if assignment.get("destination_arrival_time"):
            lines.append(f"  arrives {assignment['destination_arrival_time']}")
        for stop in assignment.get("pickup_schedule", []):
            lines.append(f"  {stop}")
    if chosen_trip.get("meetup_instructions"):
        lines.extend(["", "Meetup instructions:"])
        for instruction in chosen_trip["meetup_instructions"]:
            lines.append(f"- {instruction}")
    return "\n".join(lines)


def _build_driver_messages(chosen_trip: dict[str, object], optimization: dict[str, object], destination: dict[str, object] | None) -> list[dict[str, str]]:
    """One short WhatsApp-ready message per driver: who to pick up, when, where to."""
    participants = optimization["participants"]
    destination_name = destination["name"] if destination else "the destination"
    messages = []
    for assignment in chosen_trip["assignments"]:
        driver = participants[assignment["driver_index"]]["name"]
        riders = [participants[index]["name"] for index in assignment["passenger_indices"]]
        lines = [f"{driver}, you drive to {destination_name}."]
        lines.append("Pick up: " + (", ".join(riders) if riders else "nobody, you go solo."))
        for stop in assignment.get("pickup_schedule", []):
            lines.append(f"  {stop}")
        if assignment.get("outbound_departure_time"):
            lines.append(f"Leave at {assignment['outbound_departure_time']}.")
        if assignment.get("destination_arrival_time"):
            lines.append(f"Arrive around {assignment['destination_arrival_time']}.")
        lines.append(f"About {assignment['route_distance_km']:.0f} km, EUR {assignment['cost_per_person_eur']:.2f} per person.")
        text = "\n".join(lines)
        messages.append({"driver": driver, "text": text, "whatsapp_url": f"https://wa.me/?text={quote(text)}"})
    return messages


def _render_app_page(request: Request, page: str):
    """Render one top-level app page with whatever planner results are still valid."""
    flash = request.query_params.get("flash", "")
    scroll_target = request.query_params.get("scroll", "")
    wants_results = page in {"home", "plan"}
    optimization = get_cached_optimization_result() if wants_results else None
    # The optimization payload carries scores and driver sets too, so it also
    # feeds the Driver Selection panel; fall back to the selection-only cache.
    selection = optimization or (get_cached_selection_result() if wants_results else None)
    active_tab = request.query_params.get("tab") or SCROLL_TARGET_TABS.get(scroll_target) or "people"
    if active_tab not in {tab["slug"] for tab in WORKSPACE_TABS}:
        active_tab = "people"
    driver_messages: list[dict[str, str]] = []
    share_plan_name = ""
    share_route_set_index = 0
    if page == "share" and get_cached_optimization_result() is not None:
        shared_optimization, chosen_trip, chosen_index = _resolve_share_context(None)
        if chosen_trip is not None:
            driver_messages = _build_driver_messages(chosen_trip, shared_optimization, get_destination())
            share_plan_name = str(chosen_trip["driver_set_name"])
            share_route_set_index = chosen_index
    return render_home(
        request,
        active_page=page,
        success=FLASH_MESSAGES.get(flash),
        scroll_target=scroll_target or None,
        selection=selection,
        optimization=optimization,
        active_tab=active_tab,
        workspace_tabs=WORKSPACE_TABS,
        driver_messages=driver_messages,
        share_plan_name=share_plan_name,
        share_route_set_index=share_route_set_index,
        share_postmark_date=date.today().strftime("%d.%m.%Y"),
    )


def _build_share_map_svg(map_payload: dict[str, object], chosen_route_set_index: int) -> str:
    """Build a self-contained SVG route snapshot for the share report."""
    route_sets = map_payload.get("route_sets") or []
    selected = next(
        (route_set for route_set in route_sets if route_set.get("route_set_index") == chosen_route_set_index),
        route_sets[0] if route_sets else None,
    )
    if not selected:
        return '<svg viewBox="0 0 960 520" xmlns="http://www.w3.org/2000/svg"><rect width="960" height="520" fill="#F5F6F3"/><text x="48" y="72" fill="#5C6570" font-size="28" font-family="Segoe UI, Tahoma, sans-serif">No route preview available.</text></svg>'

    points: list[tuple[float, float]] = []
    for route in selected.get("routes", []):
        for latitude, longitude in route.get("geometry", []):
            points.append((float(latitude), float(longitude)))
        for pickup in route.get("pickup_markers", []):
            points.append((float(pickup["latitude"]), float(pickup["longitude"])))
    destination = map_payload.get("destination")
    if destination:
        points.append((float(destination["latitude"]), float(destination["longitude"])))
    if not points:
        return '<svg viewBox="0 0 960 520" xmlns="http://www.w3.org/2000/svg"><rect width="960" height="520" fill="#F5F6F3"/></svg>'

    min_lat = min(point[0] for point in points)
    max_lat = max(point[0] for point in points)
    min_lon = min(point[1] for point in points)
    max_lon = max(point[1] for point in points)
    lat_span = max(max_lat - min_lat, 0.02)
    lon_span = max(max_lon - min_lon, 0.02)
    min_lat -= lat_span * 0.08
    max_lat += lat_span * 0.08
    min_lon -= lon_span * 0.08
    max_lon += lon_span * 0.08

    width = 960.0
    height = 520.0
    pad_x = 52.0
    pad_y = 38.0
    inner_width = width - (pad_x * 2)
    inner_height = height - (pad_y * 2)

    def project(latitude: float, longitude: float) -> tuple[float, float]:
        x = pad_x + ((longitude - min_lon) / max(max_lon - min_lon, 1e-9)) * inner_width
        y = pad_y + ((max_lat - latitude) / max(max_lat - min_lat, 1e-9)) * inner_height
        return x, y

    basemap_tiles = _build_share_basemap_tiles(min_lat, min_lon, max_lat, max_lon, width, height, pad_x, pad_y)

    svg_parts = [
        '<svg viewBox="0 0 960 520" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Shared route overview">',
        '<defs><pattern id="grid" width="96" height="52" patternUnits="userSpaceOnUse"><path d="M 96 0 L 0 0 0 52" fill="none" stroke="#D3D8D0" stroke-width="1"/></pattern></defs>',
        '<rect width="960" height="520" rx="24" fill="#F5F6F3"/>',
        '<rect x="20" y="20" width="920" height="480" rx="22" fill="#FFFFFF" stroke="#D3D8D0" stroke-width="2"/>',
        '<rect x="32" y="32" width="896" height="456" rx="18" fill="#FFFFFF"/>',
    ]

    if basemap_tiles:
        svg_parts.extend(basemap_tiles)
        svg_parts.append('<rect x="32" y="32" width="896" height="456" rx="18" fill="url(#grid)" opacity="0.12"/>')
    else:
        svg_parts.append('<rect x="32" y="32" width="896" height="456" rx="18" fill="url(#grid)"/>')
        svg_parts.append('<text x="52" y="64" fill="#7c6f5e" font-size="18" font-family="Segoe UI, Tahoma, sans-serif">Route snapshot</text>')

    for route in selected.get("routes", []):
        geometry = route.get("geometry", [])
        if not geometry:
            continue
        path = " ".join(
            f"{'M' if index == 0 else 'L'} {project(float(point[0]), float(point[1]))[0]:.1f} {project(float(point[0]), float(point[1]))[1]:.1f}"
            for index, point in enumerate(geometry)
        )
        color = escape(route.get("color", "#0E7C3F"))
        svg_parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>')
        start_x, start_y = project(float(geometry[0][0]), float(geometry[0][1]))
        driver_name = escape(route.get("driver_name", "Driver"))
        driver_initial = escape(driver_name[:1].upper())
        svg_parts.append(f'<circle cx="{start_x:.1f}" cy="{start_y:.1f}" r="17" fill="{color}" stroke="#ffffff" stroke-width="3"/>')
        svg_parts.append(f'<text x="{start_x:.1f}" y="{start_y + 6:.1f}" text-anchor="middle" fill="#ffffff" font-size="15" font-weight="700" font-family="Segoe UI, Tahoma, sans-serif">{driver_initial}</text>')
        svg_parts.append(f'<text x="{start_x + 24:.1f}" y="{start_y + 5:.1f}" fill="{color}" font-size="16" font-weight="700" font-family="Segoe UI, Tahoma, sans-serif">{driver_name}</text>')
        for pickup in route.get("pickup_markers", []):
            pickup_x, pickup_y = project(float(pickup["latitude"]), float(pickup["longitude"]))
            pickup_order = escape(str(pickup["order"]))
            pickup_name = escape(pickup["name"])
            svg_parts.append(f'<circle cx="{pickup_x:.1f}" cy="{pickup_y:.1f}" r="14" fill="#edae49" stroke="#ffffff" stroke-width="3"/>')
            svg_parts.append(f'<text x="{pickup_x:.1f}" y="{pickup_y + 5:.1f}" text-anchor="middle" fill="#111827" font-size="14" font-weight="700" font-family="Segoe UI, Tahoma, sans-serif">{pickup_order}</text>')
            svg_parts.append(f'<text x="{pickup_x + 18:.1f}" y="{pickup_y - 10:.1f}" fill="#6a5f50" font-size="13" font-family="Segoe UI, Tahoma, sans-serif">{pickup_name}</text>')

    if destination:
        destination_x, destination_y = project(float(destination["latitude"]), float(destination["longitude"]))
        destination_name = escape(destination.get("name", "Destination"))
        svg_parts.append(f'<rect x="{destination_x - 14:.1f}" y="{destination_y - 14:.1f}" width="28" height="28" rx="4" fill="#111827" stroke="#ffffff" stroke-width="3"/>')
        svg_parts.append(f'<path d="M {destination_x - 14:.1f} {destination_y:.1f} H {destination_x + 14:.1f} M {destination_x:.1f} {destination_y - 14:.1f} V {destination_x:.1f}" stroke="#ffffff" stroke-width="3"/>')
        svg_parts.append(f'<text x="{destination_x + 20:.1f}" y="{destination_y + 6:.1f}" fill="#111827" font-size="16" font-weight="700" font-family="Segoe UI, Tahoma, sans-serif">{destination_name}</text>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def _build_sandbox_map_payload(sandbox: dict[str, object] | None) -> dict[str, object] | None:
    """Build a share-style route payload for invite sandbox results."""
    if not sandbox or not sandbox.get("trip_results"):
        return None
    participants = sandbox["participants"]
    destination = sandbox["destination"]
    payload: dict[str, object] = {
        "participants": [
            {
                "name": participant.name,
                "location": {
                    "name": participant.location.name,
                    "latitude": participant.location.latitude,
                    "longitude": participant.location.longitude,
                },
            }
            for participant in participants
        ],
        "destination": {
            "name": destination.name,
            "latitude": destination.latitude,
            "longitude": destination.longitude,
        },
        "route_sets": [],
    }
    palette = ["#0E7C3F", "#0B4F9C", "#7A4A22", "#C8102E", "#2A2D31", "#F2C230"]
    best_result = sandbox["best_result"]
    for trip_index, trip_result in enumerate(sandbox["trip_results"]):
        route_set_routes = []
        total_duration = 0.0
        total_distance = 0.0
        for index, assignment in enumerate(trip_result.assignments):
            driver = participants[assignment.driver_index]
            stops = [driver.location]
            pickup_markers = []
            for order, passenger_index in enumerate(assignment.passenger_indices, start=1):
                passenger = participants[passenger_index]
                stops.append(passenger.location)
                pickup_markers.append(
                    {
                        "order": order,
                        "name": passenger.name,
                        "latitude": passenger.location.latitude,
                        "longitude": passenger.location.longitude,
                    }
                )
            stops.append(destination)
            preview = build_route_preview(stops)
            total_distance += preview["distance_km"]
            if preview["duration_min"] is not None:
                total_duration += preview["duration_min"]
            route_set_routes.append(
                {
                    "driver_name": driver.name,
                    "passenger_names": [participants[passenger_index].name for passenger_index in assignment.passenger_indices],
                    "geometry": preview["geometry"],
                    "distance_km": preview["distance_km"],
                    "duration_min": preview["duration_min"],
                    "color": palette[index % len(palette)],
                    "pickup_markers": pickup_markers,
                }
            )
        payload["route_sets"].append(
            {
                "route_set_index": trip_index,
                "driver_set_name": trip_result.driver_set_name,
                "selected": trip_result.driver_set_name == getattr(best_result, "driver_set_name", ""),
                "fitness": trip_result.fitness,
                "total_distance_km": total_distance,
                "total_duration_min": total_duration if any(route["duration_min"] is not None for route in route_set_routes) else None,
                "total_time_window_violation_min": trip_result.total_time_window_violation_min,
                "routes": route_set_routes,
            }
        )
    return payload


def _build_share_basemap_tiles(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    width: float,
    height: float,
    pad_x: float,
    pad_y: float,
) -> list[str]:
    """Fetch a small stitched OSM tile background and embed it as SVG <image> tiles."""
    zoom = _select_share_tile_zoom(min_lat, min_lon, max_lat, max_lon, width, height, pad_x, pad_y)
    if zoom is None:
        return []

    min_tile_x, min_tile_y = _latlon_to_tile_xy(max_lat, min_lon, zoom)
    max_tile_x, max_tile_y = _latlon_to_tile_xy(min_lat, max_lon, zoom)
    tile_left, tile_right = int(math.floor(min_tile_x)), int(math.floor(max_tile_x))
    tile_top, tile_bottom = int(math.floor(min_tile_y)), int(math.floor(max_tile_y))

    inner_width = width - (pad_x * 2)
    inner_height = height - (pad_y * 2)

    pixel_left, pixel_top = _latlon_to_global_pixels(max_lat, min_lon, zoom)
    pixel_right, pixel_bottom = _latlon_to_global_pixels(min_lat, max_lon, zoom)
    pixel_width = max(pixel_right - pixel_left, 1.0)
    pixel_height = max(pixel_bottom - pixel_top, 1.0)

    pieces: list[str] = []
    for tile_x in range(tile_left, tile_right + 1):
        for tile_y in range(tile_top, tile_bottom + 1):
            data_uri = _fetch_tile_data_uri(tile_x, tile_y, zoom)
            if not data_uri:
                continue
            tile_px_x = tile_x * 256.0
            tile_px_y = tile_y * 256.0
            draw_x = pad_x + ((tile_px_x - pixel_left) / pixel_width) * inner_width
            draw_y = pad_y + ((tile_px_y - pixel_top) / pixel_height) * inner_height
            draw_w = (256.0 / pixel_width) * inner_width
            draw_h = (256.0 / pixel_height) * inner_height
            pieces.append(
                f'<image x="{draw_x:.2f}" y="{draw_y:.2f}" width="{draw_w:.2f}" height="{draw_h:.2f}" href="{data_uri}" preserveAspectRatio="none" opacity="0.92"/>'
            )
    return pieces


def _select_share_tile_zoom(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    width: float,
    height: float,
    pad_x: float,
    pad_y: float,
) -> int | None:
    """Pick a moderate zoom level that fits the route in a small number of tiles."""
    inner_width = width - (pad_x * 2)
    inner_height = height - (pad_y * 2)
    for zoom in range(12, 4, -1):
        left_px, top_px = _latlon_to_global_pixels(max_lat, min_lon, zoom)
        right_px, bottom_px = _latlon_to_global_pixels(min_lat, max_lon, zoom)
        span_x = max(right_px - left_px, 1.0)
        span_y = max(bottom_px - top_px, 1.0)
        tile_count_x = int(math.floor(right_px / 256.0)) - int(math.floor(left_px / 256.0)) + 1
        tile_count_y = int(math.floor(bottom_px / 256.0)) - int(math.floor(top_px / 256.0)) + 1
        if tile_count_x <= 4 and tile_count_y <= 4 and span_x <= inner_width * 2.8 and span_y <= inner_height * 2.8:
            return zoom
    return 6


def _latlon_to_global_pixels(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    """Project a lat/lon point to Web Mercator global pixels."""
    lat_rad = math.radians(max(min(latitude, 85.05112878), -85.05112878))
    n = 256.0 * (2 ** zoom)
    x = (longitude + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n
    return x, y


def _latlon_to_tile_xy(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    """Project lat/lon to fractional slippy-map tile coordinates."""
    x, y = _latlon_to_global_pixels(latitude, longitude, zoom)
    return x / 256.0, y / 256.0


def _fetch_tile_data_uri(tile_x: int, tile_y: int, zoom: int) -> str | None:
    """Fetch one OSM tile as a data URI for embedding in the share SVG."""
    max_tile = 2 ** zoom
    if tile_y < 0 or tile_y >= max_tile:
        return None
    tile_x = tile_x % max_tile
    url = f"https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png"
    request = UrlRequest(url, headers={"User-Agent": "DMProjectLocalApp/0.1 (personal local planner)"})
    try:
        with urlopen(request, timeout=6) as response:  # pragma: no cover - network path
            content = response.read()
    except Exception:  # pragma: no cover - network path
        return None
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def parse_optional_float(raw_value: str) -> float | None:
    """Parse a maybe-empty numeric HTML field."""
    cleaned = raw_value.strip()
    if not cleaned:
        return None
    return float(cleaned)


def parse_optional_time(raw_value: str) -> str | None:
    """Validate a maybe-empty HH:MM field."""
    return normalize_time_text(raw_value)


def _invite_preview_metrics(invite: dict[str, object], responses: list[dict[str, object]], current_response: dict[str, object], mode: str) -> dict[str, object]:
    """Build a lightweight sandbox summary for a guest preview page."""
    guest_location = Location("Guest", float(current_response["latitude"]), float(current_response["longitude"]))
    destination_location = Location("Destination", float(invite["destination_latitude"]), float(invite["destination_longitude"]))
    direct_distance_km = haversine_distance(guest_location, destination_location)
    direct_duration_min = (direct_distance_km / max(ESTIMATED_AVERAGE_SPEED_KMH, 1.0)) * 60.0
    other_responses = [response for response in responses if response["response_token"] != current_response["response_token"]]
    active_driver_count = 0
    if mode == "driver" and current_response.get("has_car"):
        active_driver_count += 1
    for response in other_responses:
        if response.get("has_car") and response.get("role_tag") != "prefers_passenger":
            active_driver_count += 1
    if mode == "driver":
        nearby_people = sorted(
            (
                {
                    "name": response["name"],
                    "distance_km": haversine_distance(
                        guest_location,
                        Location("Other", float(response["latitude"]), float(response["longitude"])),
                    ),
                }
                for response in other_responses
            ),
            key=lambda item: item["distance_km"],
        )[:3]
    else:
        nearby_people = sorted(
            (
                {
                    "name": response["name"],
                    "distance_km": haversine_distance(
                        guest_location,
                        Location("Other", float(response["latitude"]), float(response["longitude"])),
                    ),
                }
                for response in other_responses
                if response.get("has_car") and response.get("role_tag") != "prefers_passenger"
            ),
            key=lambda item: item["distance_km"],
        )[:3]
    return {
        "mode": mode,
        "direct_distance_km": direct_distance_km,
        "direct_duration_min": direct_duration_min,
        "active_driver_count": active_driver_count,
        "submitted_count": len(responses),
        "nearby_people": nearby_people,
        "seats_if_driving": max(int(current_response.get("total_seats") or 0) - 1, 0) if current_response.get("has_car") else 0,
    }


def _normalize_guest_preview_mode(current_response: dict[str, object], requested_mode: str) -> str:
    """Pick a safe preview mode for the current guest."""
    if requested_mode == "driver" and current_response.get("has_car"):
        return "driver"
    if requested_mode == "passenger":
        return "passenger"
    return "driver" if current_response.get("has_car") and current_response.get("role_tag") != "prefers_passenger" else "passenger"


def _build_invite_sandbox_participants(
    invite: dict[str, object],
    responses: list[dict[str, object]],
    current_response: dict[str, object],
    mode: str,
) -> tuple[list[Participant], Location]:
    """Translate guest responses into a transient domain state for sandbox optimization."""
    sandbox_participants: list[Participant] = []
    current_token = str(current_response["response_token"])
    for response in responses:
        is_current = str(response["response_token"]) == current_token
        has_car = bool(response.get("has_car"))
        if is_current and mode == "passenger":
            effective_has_car = False
        elif is_current and mode == "driver":
            effective_has_car = has_car
        else:
            effective_has_car = has_car and response.get("role_tag") != "prefers_passenger"

        car = None
        if effective_has_car:
            fuel_type = str(response.get("fuel_type") or "gasoline")
            vehicle_type = str(response.get("vehicle_type") or "car")
            total_seats = int(response.get("total_seats") or 0)
            if vehicle_type == "motorbike":
                # Rows saved before the seat cap existed may carry more.
                total_seats = min(total_seats, 2)
            if total_seats >= 2:
                car = Car(
                    fuel_type=fuel_type,
                    consumption_l_per_100km=float(
                        response.get("consumption_l_per_100km")
                        or FUEL_TYPE_DEFAULT_CONSUMPTION.get(fuel_type, 7.0)
                    ),
                    total_seats=total_seats,
                    vehicle_type=vehicle_type,
                )

        sandbox_participants.append(
            Participant(
                name=str(response["name"]),
                location=Location(
                    str(response.get("location_name") or response["name"]),
                    float(response["latitude"]),
                    float(response["longitude"]),
                ),
                car=car,
                habit_score=0.0,
                outbound_earliest_time_min=time_text_to_minutes(response.get("outbound_earliest_time")),
                outbound_latest_time_min=time_text_to_minutes(response.get("outbound_latest_time")),
                return_earliest_time_min=time_text_to_minutes(response.get("return_earliest_time")),
                return_latest_time_min=time_text_to_minutes(response.get("return_latest_time")),
            )
        )

    destination = Location(
        str(invite["destination_name"]),
        float(invite["destination_latitude"]),
        float(invite["destination_longitude"]),
        target_arrival_time_min=time_text_to_minutes(invite.get("target_arrival_time")),
    )
    return sandbox_participants, destination


def _build_invite_sandbox_view(
    invite: dict[str, object],
    responses: list[dict[str, object]],
    current_response: dict[str, object],
    mode: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Run a guest-only optimization sandbox without touching the live workspace."""
    try:
        participants, destination = _build_invite_sandbox_participants(invite, responses, current_response, mode)
        sandbox = run_sandbox_optimization(participants, destination, plan_preference="efficiency")
        return sandbox, None
    except ValueError as error:
        return None, str(error)


def _build_invite_public_link(token: str, public_base_url: str | None) -> str:
    """Return the best externally shareable invite link available."""
    base = (public_base_url or "").strip().rstrip("/")
    if not base:
        return f"/invite/{token}"
    return f"{base}/invite/{token}"


@app.get("/invites/{invite_id}/qr.svg", include_in_schema=False)
def invite_qr_code(invite_id: int, request: Request):
    """Return a scannable QR code for the invite's public guest link."""
    invite = get_trip_invite(invite_id)
    if invite is None:
        return PlainTextResponse("Invite not found.", status_code=404)
    guest_public_base_url = get_app_settings().get("sharing", {}).get("guest_public_base_url", "")
    link = _build_invite_public_link(
        str(invite["token"]),
        guest_public_base_url or str(request.base_url).rstrip("/"),
    )
    image = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=16)
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(content=buffer.getvalue(), media_type="image/svg+xml")


@app.get("/")
def home(request: Request):
    """Render the main planner page."""
    if _public_request_mode(request) == "guest":
        open_invites = [invite for invite in list_trip_invites() if invite.get("status") == "open"]
        if len(open_invites) == 1:
            return RedirectResponse(f"/invite/{open_invites[0]['token']}", status_code=303)
        return templates.TemplateResponse(
            request,
            "public_guest_home.html",
            _template_context(
                request,
                public_base_url=get_app_settings().get("sharing", {}).get("guest_public_base_url", ""),
                open_invites=open_invites,
            ),
        )
    LOG_DEBUG("rendering home page")
    return _render_app_page(request, "home")


@app.get("/participants/{participant_id}/edit")
def edit_participant_page(request: Request, participant_id: int):
    """Render the page with a participant preloaded for editing."""
    participant = get_participant(participant_id)
    if participant is None:
        return render_home(request, status_code=404, error="Participant not found.")
    return render_home(request, editing_participant=participant, scroll_target="participants-panel")


@app.post("/participants")
def create_or_update_participant(
    request: Request,
    participant_id: str = Form(""),
    name: str = Form(...),
    address_text: str = Form(""),
    location_name: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    pickup_mode: str = Form("home"),
    pickup_location_name: str = Form(""),
    pickup_address_text: str = Form(""),
    pickup_latitude: str = Form(""),
    pickup_longitude: str = Form(""),
    meetup_spot_id: str = Form(""),
    habit_score: float = Form(0.0),
    has_car: bool = Form(False),
    vehicle_type: str = Form("car"),
    fuel_type: str | None = Form(None),
    consumption_l_per_100km: str = Form(""),
    total_seats: str = Form(""),
    role_tag: str = Form("standard"),
    availability_tag: str = Form("available"),
    pickup_flexible: bool = Form(False),
    force_drive_alone: bool = Form(False),
    clear_time_preferences: bool = Form(False),
    priority_rank: str = Form("100"),
    outbound_earliest_time: str = Form(""),
    outbound_latest_time: str = Form(""),
    return_earliest_time: str = Form(""),
    return_latest_time: str = Form(""),
):
    """Save a participant from the web form."""
    LOG_INFO(f"save participant request — name={name!r}, id={participant_id!r}")
    field_errors: dict[str, str] = {}

    def _participant_form_error():
        """Re-render the People tab with the collected field errors.

        The scroll target matters: without it the POST re-renders as the home
        page, where the participant form (and its errors) never shows.
        """
        return render_home(
            request,
            status_code=400,
            error="Please fix the participant form errors.",
            field_errors={
                key: _translate_request(request, message) for key, message in field_errors.items()
            },
            editing_participant=get_participant(int(participant_id))
            if participant_id.strip().isdigit()
            else None,
            scroll_target="participants-panel",
        )

    try:
        if not name.strip():
            field_errors["name"] = "Name is required."
        resolved_location = resolve_location_input(
            name=location_name,
            address_text=address_text,
            latitude=parse_optional_float(latitude),
            longitude=parse_optional_float(longitude),
            fallback_name=name.strip(),
        )
        normalized_pickup_mode = "meetup" if pickup_mode.strip().lower() == "meetup" else "home"
        selected_meetup_spot = (
            get_meetup_spot(int(meetup_spot_id))
            if meetup_spot_id.strip().isdigit()
            else None
        )
        if normalized_pickup_mode == "meetup":
            if selected_meetup_spot is not None:
                resolved_pickup_location = {
                    "name": selected_meetup_spot["name"],
                    "address_text": selected_meetup_spot.get("address_text"),
                    "latitude": float(selected_meetup_spot["latitude"]),
                    "longitude": float(selected_meetup_spot["longitude"]),
                }
            else:
                resolved_pickup_location = resolve_location_input(
                    name=pickup_location_name,
                    address_text=pickup_address_text,
                    latitude=parse_optional_float(pickup_latitude),
                    longitude=parse_optional_float(pickup_longitude),
                    fallback_name=f"{name.strip()} meetup",
                )
        else:
            resolved_pickup_location = None
        consumption_value = parse_optional_float(consumption_l_per_100km)
        seats_value = int(total_seats.strip()) if total_seats.strip() else None
        if vehicle_type not in VEHICLE_TYPES:
            vehicle_type = "car"
        if has_car and vehicle_type == "motorbike" and seats_value is None:
            seats_value = 2
        priority_value = int(priority_rank.strip()) if priority_rank.strip() else 100
    except ValueError as error:
        if not field_errors:
            field_errors["location"] = str(error)
        return _participant_form_error()
    try:
        outbound_earliest_value = parse_optional_time(outbound_earliest_time)
        outbound_latest_value = parse_optional_time(outbound_latest_time)
        return_earliest_value = parse_optional_time(return_earliest_time)
        return_latest_value = parse_optional_time(return_latest_time)
    except ValueError as error:
        field_errors["outbound_time"] = str(error)
        return _participant_form_error()
    if clear_time_preferences:
        outbound_earliest_value = None
        outbound_latest_value = None
        return_earliest_value = None
        return_latest_value = None

    if force_drive_alone and not has_car:
        field_errors["total_seats"] = "Drive-alone participants need a car."
        return _participant_form_error()
    if has_car and vehicle_type == "motorbike" and seats_value is not None and seats_value > 2:
        field_errors["total_seats"] = "A motorbike seats at most two people."
        return _participant_form_error()
    minimum_seats = 1 if force_drive_alone else 2
    if has_car and (seats_value is None or seats_value < minimum_seats):
        field_errors["total_seats"] = (
            "Solo drivers need at least 1 total seat."
            if force_drive_alone
            else "Drivers need at least 2 total seats."
        )
        return _participant_form_error()
    if field_errors:
        return _participant_form_error()
    if outbound_earliest_value and outbound_latest_value and outbound_earliest_value > outbound_latest_value:
        field_errors["outbound_time"] = "Outbound earliest time must be before outbound latest time."
    if return_earliest_value and return_latest_value and return_earliest_value > return_latest_value:
        field_errors["return_time"] = "Return earliest time must be before return latest time."
    if field_errors:
        return _participant_form_error()

    save_participant(
        participant_id=int(participant_id) if participant_id.strip() else None,
        name=name.strip(),
        address_text=resolved_location["address_text"],
        location_name=resolved_location["name"],
        latitude=resolved_location["latitude"],
        longitude=resolved_location["longitude"],
        pickup_mode=normalized_pickup_mode,
        pickup_location_name=resolved_pickup_location["name"] if resolved_pickup_location else None,
        pickup_address_text=resolved_pickup_location["address_text"] if resolved_pickup_location else None,
        pickup_latitude=resolved_pickup_location["latitude"] if resolved_pickup_location else None,
        pickup_longitude=resolved_pickup_location["longitude"] if resolved_pickup_location else None,
        has_car=has_car,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type if has_car else None,
        consumption_l_per_100km=(
            consumption_value
            if has_car and consumption_value is not None
            else FUEL_TYPE_DEFAULT_CONSUMPTION.get(fuel_type or "", None)
            if has_car
            else None
        ),
        total_seats=seats_value if has_car else None,
        habit_score=habit_score,
        role_tag=role_tag,
        availability_tag=availability_tag,
        pickup_flexible=pickup_flexible,
        force_drive_alone=force_drive_alone if has_car else False,
        priority_rank=priority_value,
        active_in_trip=True,
        outbound_earliest_time=outbound_earliest_value,
        outbound_latest_time=outbound_latest_value,
        return_earliest_time=return_earliest_value,
        return_latest_time=return_latest_value,
    )
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/participants/{participant_id}/delete")
def remove_participant(request: Request, participant_id: int):
    """Delete one participant."""
    delete_participant(participant_id)
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/participants/{participant_id}/trip-toggle")
def toggle_participant_trip_state(request: Request, participant_id: int, active_in_trip: bool = Form(False)):
    """Include or exclude one participant from the current trip planning run."""
    set_participant_trip_active(participant_id, active_in_trip)
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/participants/reorder")
def reorder_participant_rows(order: str = Form(...)):
    """Persist the current draggable participant list order."""
    participant_ids = [int(raw_id) for raw_id in order.split(",") if raw_id.strip()]
    if participant_ids:
        reorder_participants(participant_ids)
    return PlainTextResponse("ok")


@app.post("/destination")
def update_destination(
    request: Request,
    name: str = Form(""),
    address_text: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    target_arrival_time: str = Form(""),
):
    """Store the current destination."""
    LOG_INFO(f"set destination — name={name!r}, address={address_text!r}")
    field_errors: dict[str, str] = {}
    try:
        target_arrival_value = parse_optional_time(target_arrival_time)
        resolved_location = resolve_location_input(
            name=name,
            address_text=address_text,
            latitude=parse_optional_float(latitude),
            longitude=parse_optional_float(longitude),
            fallback_name="Destination",
        )
    except ValueError as error:
        field_errors["destination"] = str(error)
        return render_home(
            request,
            status_code=400,
            error="Please fix the destination form errors.",
            field_errors=field_errors,
            scroll_target="destination-panel",
        )

    save_destination(
        name=resolved_location["name"],
        latitude=resolved_location["latitude"],
        longitude=resolved_location["longitude"],
        address_text=resolved_location["address_text"],
        target_arrival_time=target_arrival_value,
    )
    mark_current_dataset_context_customized()
    return _redirect_to_page(request, page="plan", flash="destination-saved", scroll="destination-panel")


@app.post("/destination/favorite")
def create_destination_favorite(
    request: Request,
    name: str = Form(""),
    address_text: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    target_arrival_time: str = Form(""),
):
    """Save the current destination form values, or the active destination, as a favorite."""
    field_errors: dict[str, str] = {}
    destination = get_destination()
    has_form_values = any(value.strip() for value in (name, address_text, latitude, longitude))

    if has_form_values or destination is None:
        try:
            target_arrival_value = parse_optional_time(target_arrival_time)
            resolved_location = resolve_location_input(
                name=name,
                address_text=address_text,
                latitude=parse_optional_float(latitude),
                longitude=parse_optional_float(longitude),
                fallback_name="Destination",
            )
        except ValueError as error:
            field_errors["destination"] = str(error)
            return render_home(
                request,
                status_code=400,
                error="Please fix the destination form errors.",
                field_errors=field_errors,
                scroll_target="destination-panel",
            )
        save_destination(
            name=resolved_location["name"],
            latitude=resolved_location["latitude"],
            longitude=resolved_location["longitude"],
            address_text=resolved_location["address_text"],
            target_arrival_time=target_arrival_value,
        )
        destination = resolved_location | {"target_arrival_time": target_arrival_value}

    if destination is None:
        return render_home(
            request,
            status_code=400,
            error="Save or enter a destination before adding it to favorites.",
            scroll_target="destination-panel",
        )

    save_destination_favorite(
        name=destination["name"],
        address_text=destination.get("address_text"),
        latitude=destination["latitude"],
        longitude=destination["longitude"],
        target_arrival_time=destination.get("target_arrival_time"),
    )
    return _redirect_to_page(request, page="plan", flash="destination-favorite-saved", scroll="destination-panel")


@app.post("/destination/favorite/{destination_id}/apply")
def use_saved_destination(request: Request, destination_id: int):
    """Apply one favorite destination."""
    try:
        apply_saved_destination(destination_id)
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    mark_current_dataset_context_customized()
    return _redirect_to_page(request, page="plan", scroll="destination-panel")


@app.post("/destination/favorite/{destination_id}/delete")
def remove_saved_destination(request: Request, destination_id: int):
    """Delete one favorite destination."""
    delete_saved_destination(destination_id)
    return _redirect_to_page(request, page="plan", scroll="destination-panel")


@app.post("/meetup-spots")
def create_meetup_spot(
    request: Request,
    name: str = Form(""),
    address_text: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    is_free_parking: bool = Form(False),
    has_ev_charging: bool = Form(False),
    notes: str = Form(""),
):
    """Save a reusable meetup spot."""

    field_errors: dict[str, str] = {}
    try:
        resolved_location = resolve_location_input(
            name=name,
            address_text=address_text,
            latitude=parse_optional_float(latitude),
            longitude=parse_optional_float(longitude),
            fallback_name="Meetup spot",
        )
    except ValueError as error:
        field_errors["meetup_spot"] = str(error)
        return render_home(
            request,
            status_code=400,
            error="Please fix the meetup spot form errors.",
            field_errors=field_errors,
            scroll_target="meetup-spots-panel",
        )
    save_meetup_spot(
        name=resolved_location["name"],
        latitude=resolved_location["latitude"],
        longitude=resolved_location["longitude"],
        address_text=resolved_location["address_text"],
        is_free_parking=is_free_parking,
        has_ev_charging=has_ev_charging,
        notes=notes,
    )
    return _redirect_to_page(request, page="plan", scroll="meetup-spots-panel")


@app.post("/meetup-spots/{spot_id}/delete")
def remove_meetup_spot(request: Request, spot_id: int):
    """Delete a reusable meetup spot."""

    delete_meetup_spot(spot_id)
    return _redirect_to_page(request, page="plan", scroll="meetup-spots-panel")


@app.post("/sample")
def sample_data(request: Request):
    """Load the bundled sample dataset."""
    LOG_INFO("loading sample dataset")
    load_sample_dataset()
    LOG_INFO("sample dataset loaded")
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/reset")
def reset_data(request: Request):
    """Clear the local app state."""
    LOG_INFO("resetting all data")
    clear_all_data()
    return _redirect_to_page(request, page="plan")


@app.post("/selection")
def selection(request: Request):
    """Render the page with current driver-selection results."""
    LOG_INFO("driver selection requested")
    try:
        results = run_driver_selection()
    except ValueError as error:
        LOG_INFO(f"driver selection failed: {error}")
        return render_home(request, status_code=400, error=str(error))
    cache_selection_result(results)
    LOG_INFO("driver selection completed and cached")
    return _redirect_to_page(request, page="plan", flash="selection-completed", scroll="driver-selection-section")


@app.post("/optimization")
def optimization(request: Request):
    """Render the page with full optimization results."""
    LOG_INFO("optimization requested")
    try:
        results = run_optimization()
    except ValueError as error:
        LOG_INFO(f"optimization failed: {error}")
        return render_home(request, status_code=400, error=str(error))
    cache_optimization_result(results)
    LOG_INFO("optimization completed and cached")
    return _redirect_to_page(request, page="plan", flash="optimization-completed", scroll="optimization-results-section")


@app.post("/settings")
async def update_settings(
    request: Request,
    num_ants: int = Form(...),
    num_iterations: int = Form(...),
    alpha: float = Form(...),
    beta: float = Form(...),
    rho: float = Form(...),
    map_latitude: float = Form(...),
    map_longitude: float = Form(...),
    map_zoom: int = Form(...),
    plan_return_separately: str | None = Form(None),
    allow_meetup_pooling: str | None = Form(None),
    max_meetup_self_transfer_km: float = Form(3.0),
    notifications_enabled: str | None = Form(None),
    algorithm: str = Form(DEFAULT_SOLVER),
):
    """Persist application defaults."""
    LOG_INFO(f"saving settings - solver={algorithm}, ants={num_ants}, iter={num_iterations}, alpha={alpha}, beta={beta}, rho={rho}")
    current_settings = get_app_settings()
    submitted_form = await request.form()
    save_app_settings(
        num_ants=num_ants,
        num_iterations=num_iterations,
        alpha=alpha,
        beta=beta,
        rho=rho,
        map_latitude=map_latitude,
        map_longitude=map_longitude,
        map_zoom=map_zoom,
        plan_return_separately=(
            current_settings["optimization"].get("plan_return_separately", False)
            if "plan_return_separately" not in submitted_form
            else str(plan_return_separately).strip().lower() in {"1", "true", "on", "yes"}
        ),
        allow_meetup_pooling=(
            current_settings["optimization"].get("allow_meetup_pooling", True)
            if "allow_meetup_pooling" not in submitted_form
            else str(allow_meetup_pooling).strip().lower() in {"1", "true", "on", "yes"}
        ),
        max_meetup_self_transfer_km=float(
            current_settings["optimization"].get("max_meetup_self_transfer_km", 3.0)
            if "max_meetup_self_transfer_km" not in submitted_form
            else max_meetup_self_transfer_km
        ),
        notifications_enabled=(
            current_settings["notifications"].get("enabled", True)
            if "notifications_enabled" not in submitted_form
            else str(notifications_enabled).strip().lower() in {"1", "true", "on", "yes"}
        ),
        algorithm=algorithm,
    )
    return _redirect_to_page(request, page="settings", scroll="advanced-settings-panel")


@app.post("/settings/return-planning")
def update_return_planning(request: Request, plan_return_separately: bool = Form(False)):
    """Persist only the return-planning preference from the Planner controls."""
    settings = get_app_settings()
    optimization = settings["optimization"]
    map_center = settings["map_center"]
    save_app_settings(
        num_ants=int(optimization["num_ants"]),
        num_iterations=int(optimization["num_iterations"]),
        alpha=float(optimization["alpha"]),
        beta=float(optimization["beta"]),
        rho=float(optimization["rho"]),
        map_latitude=float(map_center["latitude"]),
        map_longitude=float(map_center["longitude"]),
        map_zoom=int(map_center["zoom"]),
        plan_return_separately=plan_return_separately,
        allow_meetup_pooling=bool(optimization.get("allow_meetup_pooling", True)),
        max_meetup_self_transfer_km=float(optimization.get("max_meetup_self_transfer_km", 3.0)),
        notifications_enabled=bool(settings["notifications"].get("enabled", True)),
        algorithm=str(optimization.get("algorithm", DEFAULT_SOLVER)),
    )
    return _redirect_to_page(request, page="plan", scroll="planner-return-planning")


@app.post("/settings/public-share-url")
def update_public_share_url(
    request: Request,
    guest_public_base_url: str = Form(""),
    admin_public_base_url: str = Form(""),
    public_base_url: str = Form(""),
):
    """Persist separate guest/admin public URLs for share links."""
    guest_url = guest_public_base_url or public_base_url
    save_public_access_urls(
        guest_public_base_url=guest_url,
        admin_public_base_url=admin_public_base_url,
    )
    return _redirect_to_page(request, page="share", scroll="guest-invites-panel")


@app.post("/settings/public-share-url/{mode}")
def update_public_share_url_mode(mode: str, public_base_url: str = Form("")):
    """Persist one guest/admin public URL without overwriting the other one."""
    save_public_access_url(mode=mode, public_base_url=public_base_url)
    return PlainTextResponse("OK")


@app.post("/pickup-rules")
def create_pickup_rule(request: Request, before_participant_id: int = Form(...), after_participant_id: int = Form(...)):
    """Store a same-car outbound pickup precedence preference."""
    try:
        save_pickup_order_rule(before_participant_id, after_participant_id)
    except ValueError as error:
        return render_home(
            request,
            status_code=400,
            error=str(error),
            scroll_target="planner-pickup-rules",
        )
    return _redirect_to_page(request, page="plan", scroll="planner-pickup-rules")


@app.post("/pickup-rules/{rule_index}/delete")
def remove_pickup_rule(request: Request, rule_index: int):
    """Delete one stored pickup-order rule."""
    delete_pickup_order_rule(rule_index)
    return _redirect_to_page(request, page="plan", scroll="planner-pickup-rules")


@app.post("/ride-together-rules")
def create_ride_together_rule(request: Request, first_participant_id: int = Form(...), second_participant_id: int = Form(...)):
    """Store a same-car requirement for two participants."""
    try:
        save_ride_together_rule(first_participant_id, second_participant_id)
    except ValueError as error:
        return render_home(
            request,
            status_code=400,
            error=str(error),
            scroll_target="planner-ride-together-rules",
        )
    return _redirect_to_page(request, page="plan", scroll="planner-ride-together-rules")


@app.post("/ride-together-rules/{rule_index}/delete")
def remove_ride_together_rule(request: Request, rule_index: int):
    """Delete one stored same-car requirement."""
    delete_ride_together_rule(rule_index)
    return _redirect_to_page(request, page="plan", scroll="planner-ride-together-rules")


@app.post("/invites")
def create_invite(request: Request, trip_name: str = Form(""), deadline_at: str = Form(""), notes: str = Form("")):
    """Create a guest intake invite for the current destination."""
    if not trip_name.strip():
        return render_home(request, status_code=400, error="Invite name is required.", scroll_target="guest-invites-panel")
    try:
        create_trip_invite(
            trip_name=trip_name.strip(),
            deadline_at=deadline_at.strip() or None,
            notes=notes.strip() or None,
        )
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error), scroll_target="guest-invites-panel")
    return _redirect_to_page(request, page="share", scroll="guest-invites-panel")


@app.get("/invites/{invite_id}", response_class=HTMLResponse)
def review_invite(request: Request, invite_id: int):
    """Review guest submissions for one invite."""
    invite = get_trip_invite(invite_id)
    if invite is None:
        return HTMLResponse(_translate_request(request, "Invite not found."), status_code=404)
    responses = list_trip_responses(invite_id)
    guest_public_base_url = get_app_settings().get("sharing", {}).get("guest_public_base_url", "")
    invite_link = _build_invite_public_link(str(invite["token"]), guest_public_base_url or str(request.base_url).rstrip("/"))
    return templates.TemplateResponse(
        request,
        "invite_review.html",
        _template_context(
            request,
            invite=invite,
            responses=responses,
            invite_link=invite_link,
            whatsapp_url=f"https://wa.me/?text={quote(invite_link)}",
        ),
    )


@app.post("/invites/{invite_id}/responses/{response_id}/import")
def import_invite_response(request: Request, invite_id: int, response_id: int):
    """Import one guest response into the active participant workspace."""
    try:
        import_trip_response(response_id)
        mark_current_dataset_context_customized()
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error), scroll_target="guest-invites-panel")
    return RedirectResponse(f"/invites/{invite_id}", status_code=303)


@app.post("/invites/{invite_id}/import-all")
def import_all_invite_responses(request: Request, invite_id: int):
    """Import every submitted response for one invite."""
    try:
        import_all_trip_responses(invite_id)
        mark_current_dataset_context_customized()
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error), scroll_target="guest-invites-panel")
    return RedirectResponse(f"/invites/{invite_id}", status_code=303)


@app.post("/invites/{invite_id}/use-as-workspace")
def use_invite_as_workspace(request: Request, invite_id: int):
    """Replace the live workspace with one invite session dataset."""
    try:
        load_invite_session_into_workspace(invite_id)
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error), scroll_target="guest-invites-panel")
    return _redirect_to_page(request, page="plan", flash="invite-workspace-loaded", scroll="participants-panel")


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_form(request: Request, token: str, response_token: str = ""):
    """Render the public guest intake form."""
    invite = get_trip_invite_by_token(token)
    if invite is None:
        return HTMLResponse(_translate_request(request, "Invite not found."), status_code=404)
    _queue_guest_visit_notification(request, invite)
    response_data = get_trip_response_by_token(response_token) if response_token else None
    return templates.TemplateResponse(
        request,
        "invite_form.html",
        _template_context(
            request,
            invite=invite,
            response_data=response_data,
            field_error=None,
            saved_meetup_spots=list_saved_meetup_spots(),
        ),
    )


@app.post("/invite/{token}", response_class=HTMLResponse)
def submit_invite_form(
    request: Request,
    token: str,
    response_token: str = Form(""),
    name: str = Form(""),
    address_text: str = Form(""),
    location_name: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    pickup_mode: str = Form("home"),
    meetup_spot_id: str = Form(""),
    pickup_location_name: str = Form(""),
    pickup_address_text: str = Form(""),
    pickup_latitude: str = Form(""),
    pickup_longitude: str = Form(""),
    pickup_flexible: bool = Form(False),
    has_car: bool = Form(False),
    vehicle_type: str = Form("car"),
    fuel_type: str | None = Form(None),
    consumption_l_per_100km: str = Form(""),
    total_seats: str = Form(""),
    role_tag: str = Form("standard"),
    outbound_earliest_time: str = Form(""),
    outbound_latest_time: str = Form(""),
    return_earliest_time: str = Form(""),
    return_latest_time: str = Form(""),
    food_stop_vote: str = Form(""),
):
    """Store or update one guest response."""
    invite = get_trip_invite_by_token(token)
    if invite is None:
        return HTMLResponse(_translate_request(request, "Invite not found."), status_code=404)
    if invite.get("status") != "open":
        return HTMLResponse(_translate_request(request, "This invite is no longer accepting responses."), status_code=400)
    response_stub = {
        "response_token": response_token,
        "name": name,
        "address_text": address_text,
        "location_name": location_name,
        "latitude": latitude,
        "longitude": longitude,
        "pickup_mode": pickup_mode,
        "meetup_spot_id": meetup_spot_id,
        "pickup_location_name": pickup_location_name,
        "pickup_address_text": pickup_address_text,
        "pickup_latitude": pickup_latitude,
        "pickup_longitude": pickup_longitude,
        "pickup_flexible": pickup_flexible,
        "has_car": has_car,
        "vehicle_type": vehicle_type,
        "fuel_type": fuel_type,
        "consumption_l_per_100km": consumption_l_per_100km,
        "total_seats": total_seats,
        "role_tag": role_tag,
        "outbound_earliest_time": outbound_earliest_time,
        "outbound_latest_time": outbound_latest_time,
        "return_earliest_time": return_earliest_time,
        "return_latest_time": return_latest_time,
        "food_stop_vote": food_stop_vote,
    }
    try:
        resolved_location = resolve_location_input(
            name=location_name,
            address_text=address_text,
            latitude=parse_optional_float(latitude),
            longitude=parse_optional_float(longitude),
            fallback_name=name.strip(),
        )
        if not name.strip():
            raise ValueError("Name is required.")
        normalized_pickup_mode = "meetup" if pickup_mode.strip().lower() == "meetup" else "home"
        selected_meetup_spot = (
            get_meetup_spot(int(meetup_spot_id))
            if meetup_spot_id.strip().isdigit()
            else None
        )
        if normalized_pickup_mode == "meetup":
            if selected_meetup_spot is not None:
                resolved_pickup_location = {
                    "name": selected_meetup_spot["name"],
                    "address_text": selected_meetup_spot.get("address_text"),
                    "latitude": float(selected_meetup_spot["latitude"]),
                    "longitude": float(selected_meetup_spot["longitude"]),
                }
            else:
                resolved_pickup_location = resolve_location_input(
                    name=pickup_location_name,
                    address_text=pickup_address_text,
                    latitude=parse_optional_float(pickup_latitude),
                    longitude=parse_optional_float(pickup_longitude),
                    fallback_name=f"{name.strip()} meetup",
                )
        else:
            resolved_pickup_location = None
        consumption_value = parse_optional_float(consumption_l_per_100km)
        seats_value = int(total_seats.strip()) if total_seats.strip() else None
        outbound_earliest_value = parse_optional_time(outbound_earliest_time)
        outbound_latest_value = parse_optional_time(outbound_latest_time)
        return_earliest_value = parse_optional_time(return_earliest_time)
        return_latest_value = parse_optional_time(return_latest_time)
    except ValueError as error:
        return templates.TemplateResponse(
            request,
            "invite_form.html",
            _template_context(
                request,
                invite=invite,
                response_data=response_stub,
                field_error=_translate_request(request, str(error)),
                saved_meetup_spots=list_saved_meetup_spots(),
            ),
            status_code=400,
        )
    if outbound_earliest_value and outbound_latest_value and outbound_earliest_value > outbound_latest_value:
        return templates.TemplateResponse(
            request,
            "invite_form.html",
            _template_context(
                request,
                invite=invite,
                response_data=response_stub,
                field_error=_translate_request(request, "Outbound earliest time must be before outbound latest time."),
                saved_meetup_spots=list_saved_meetup_spots(),
            ),
            status_code=400,
        )
    if return_earliest_value and return_latest_value and return_earliest_value > return_latest_value:
        return templates.TemplateResponse(
            request,
            "invite_form.html",
            _template_context(
                request,
                invite=invite,
                response_data=response_stub,
                field_error=_translate_request(request, "Return earliest time must be before return latest time."),
                saved_meetup_spots=list_saved_meetup_spots(),
            ),
            status_code=400,
        )
    if has_car and (seats_value is None or seats_value < 2):
        return templates.TemplateResponse(
            request,
            "invite_form.html",
            _template_context(
                request,
                invite=invite,
                response_data=response_stub,
                field_error=_translate_request(request, "Drivers need at least 2 total seats."),
                saved_meetup_spots=list_saved_meetup_spots(),
            ),
            status_code=400,
        )
    if has_car and vehicle_type == "motorbike" and seats_value is not None and seats_value > 2:
        return templates.TemplateResponse(
            request,
            "invite_form.html",
            _template_context(
                request,
                invite=invite,
                response_data=response_stub,
                field_error=_translate_request(request, "A motorbike seats at most two people."),
                saved_meetup_spots=list_saved_meetup_spots(),
            ),
            status_code=400,
        )
    is_update = bool(response_token.strip())
    response = save_trip_response(
        invite_id=int(invite["id"]),
        response_token=response_token.strip() or None,
        name=name.strip(),
        address_text=resolved_location["address_text"],
        location_name=resolved_location["name"],
        latitude=resolved_location["latitude"],
        longitude=resolved_location["longitude"],
        pickup_mode=normalized_pickup_mode,
        pickup_location_name=resolved_pickup_location["name"] if resolved_pickup_location else None,
        pickup_address_text=resolved_pickup_location["address_text"] if resolved_pickup_location else None,
        pickup_latitude=resolved_pickup_location["latitude"] if resolved_pickup_location else None,
        pickup_longitude=resolved_pickup_location["longitude"] if resolved_pickup_location else None,
        pickup_flexible=pickup_flexible,
        has_car=has_car,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type if has_car else None,
        consumption_l_per_100km=(
            consumption_value
            if has_car and consumption_value is not None
            else FUEL_TYPE_DEFAULT_CONSUMPTION.get(fuel_type or "", None)
            if has_car
            else None
        ),
        total_seats=seats_value if has_car else None,
        role_tag=role_tag or "standard",
        outbound_earliest_time=outbound_earliest_value,
        outbound_latest_time=outbound_latest_value,
        return_earliest_time=return_earliest_value,
        return_latest_time=return_latest_value,
        food_stop_vote=(
            food_stop_vote.strip().lower()
            if food_stop_vote.strip().lower() in FOOD_STOP_VOTE_OPTIONS
            else None
        ),
    )
    _queue_guest_submission_notification(request, invite, name.strip(), is_update=is_update)
    return RedirectResponse(f"/invite/{token}/preview?response_token={response['response_token']}", status_code=303)


@app.get("/invite/{token}/preview", response_class=HTMLResponse)
def invite_preview(request: Request, token: str, response_token: str, mode: str = "submitted", route_set_index: int | None = None):
    """Render a guest sandbox preview with a real invite-only optimization run."""
    invite = get_trip_invite_by_token(token)
    if invite is None:
        return HTMLResponse(_translate_request(request, "Invite not found."), status_code=404)
    response_data = get_trip_response_by_token(response_token)
    if response_data is None or int(response_data["invite_id"]) != int(invite["id"]):
        return HTMLResponse(_translate_request(request, "Response not found."), status_code=404)
    sandbox_mode = _normalize_guest_preview_mode(response_data, mode)
    responses = list_trip_responses(int(invite["id"]))
    preview = _invite_preview_metrics(invite, responses, response_data, sandbox_mode)
    sandbox, sandbox_error = _build_invite_sandbox_view(invite, responses, response_data, sandbox_mode)
    sandbox_map_payload = _build_sandbox_map_payload(sandbox)
    chosen_route_set_index = 0
    selected_route_set = None
    selected_trip = None
    share_map_svg = None
    if sandbox and sandbox_map_payload and sandbox.get("trip_results"):
        if route_set_index is not None and 0 <= route_set_index < len(sandbox["trip_results"]):
            chosen_route_set_index = route_set_index
        else:
            chosen_route_set_index = next(
                (
                    index
                    for index, trip in enumerate(sandbox["trip_results"])
                    if trip.driver_set_name == getattr(sandbox.get("best_result"), "driver_set_name", "")
                ),
                0,
            )
        selected_trip = sandbox["trip_results"][chosen_route_set_index]
        selected_route_set = next(
            (route_set for route_set in sandbox_map_payload.get("route_sets", []) if route_set.get("route_set_index") == chosen_route_set_index),
            sandbox_map_payload.get("route_sets", [None])[0] if sandbox_map_payload.get("route_sets") else None,
        )
        share_map_svg = _build_share_map_svg(sandbox_map_payload, chosen_route_set_index)
    return templates.TemplateResponse(
        request,
        "invite_preview.html",
        _template_context(
            request,
            invite=invite,
            response_data=response_data,
            responses=responses,
            preview=preview,
            sandbox_mode=sandbox_mode,
            sandbox=sandbox,
            sandbox_error=sandbox_error,
            sandbox_map_payload=sandbox_map_payload,
            chosen_route_set_index=chosen_route_set_index,
            selected_route_set=selected_route_set,
            selected_trip=selected_trip,
            share_map_svg=share_map_svg,
        ),
    )


@app.post("/groups")
def create_group(request: Request, group_name: str = Form(...)):
    """Save the current workspace as a reusable participant snapshot."""
    if not group_name.strip():
        return render_home(request, status_code=400, error="Snapshot name is required.")
    save_group(group_name)
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/groups/{group_id}/apply")
def use_group(request: Request, group_id: int):
    """Load one saved participant snapshot."""
    try:
        apply_group(group_id)
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/groups/{group_id}/delete")
def remove_group(request: Request, group_id: int):
    """Delete a saved participant snapshot."""
    delete_group(group_id)
    return _redirect_to_page(request, page="plan", scroll="participants-panel")


@app.post("/history/save")
def create_history_entry(
    request: Request,
    trip_name: str = Form(""),
    trip_date: str = Form(""),
    preset_name: str = Form(""),
    notes: str = Form(""),
):
    """Save the current optimization run to history."""
    LOG_INFO(f"saving trip to history — name={trip_name!r}")
    try:
        save_trip_history(
            trip_name=trip_name,
            trip_date=trip_date or None,
            preset_name=preset_name or None,
            notes=notes or None,
        )
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    return _redirect_to_page(request, page="trips", scroll="trip-history-panel")


@app.post("/history/duplicate")
def duplicate_history_entry(request: Request):
    """Reload the most recent trip into the active workspace."""
    try:
        duplicate_last_trip()
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    return _redirect_to_page(request, page="plan", flash="history-duplicated", scroll="optimization-results-section")


@app.post("/history/{trip_id}/restore")
def restore_history_entry(request: Request, trip_id: int):
    """Reload a specific history entry into the live workspace."""
    try:
        restore_trip_history_entry(trip_id)
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    return _redirect_to_page(request, page="plan", flash="history-restored", scroll="participants-panel")


@app.post("/history/{trip_id}/delete")
def delete_history_entry(request: Request, trip_id: int):
    """Delete one saved trip-history snapshot."""
    deleted = delete_trip_history_entry(trip_id)
    if not deleted:
        return render_home(request, status_code=404, error="Saved trip snapshot not found.")
    return _redirect_to_page(request, page="trips", flash="history-deleted", scroll="trip-history-panel")


@app.post("/workspace/restore-backup")
def restore_last_workspace_backup(request: Request):
    """Restore the last automatic workspace backup created before a history overwrite."""
    try:
        restore_workspace_backup()
    except ValueError as error:
        return render_home(request, status_code=400, error=str(error))
    return _redirect_to_page(request, page="plan", flash="workspace-backup-restored", scroll="optimization-results-section")


@app.get("/history/{trip_id}/snapshot", response_class=HTMLResponse)
def trip_snapshot(request: Request, trip_id: int):
    """Render a printable snapshot of a previous trip."""
    entry = get_trip_history_entry(trip_id)
    if entry is None:
        return HTMLResponse(_translate_request(request, "Trip not found."), status_code=404)
    return templates.TemplateResponse(
        request,
        "snapshot.html",
        _template_context(request, entry=entry),
    )


@app.get("/participants/export")
def export_participants():
    """Download the participant list as CSV."""
    return PlainTextResponse(
        export_participants_csv(),
        headers={"Content-Disposition": f"attachment; filename={build_participants_export_filename()}"},
        media_type="text/csv",
    )


@app.get("/workspace/export")
def export_workspace():
    """Download the full local workspace as JSON."""
    return PlainTextResponse(
        export_workspace_backup(),
        headers={"Content-Disposition": "attachment; filename=dmproject-workspace.json"},
        media_type="application/json",
    )


@app.post("/workspace/import")
async def import_workspace(request: Request, backup_file: UploadFile = File(...)):
    """Restore a previously exported workspace backup file."""
    content = await backup_file.read()
    try:
        result = import_workspace_backup(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        LOG_INFO(f"workspace import rejected: {error}")
        return render_home(
            request,
            status_code=400,
            error=str(error) or "Workspace import failed.",
            scroll_target="destination-panel",
        )
    LOG_INFO(f"workspace import restored {result}")
    return _redirect_to_page(request, page="plan", flash="workspace-imported", scroll="participants-panel")


@app.get("/share/current", response_class=HTMLResponse)
def share_current_plan(request: Request, download: bool = False, route_set_index: int | None = None):
    """Open or export a printable share report for the current optimized plan."""
    LOG_INFO(f"share report requested — download={download}, route_set={route_set_index}")
    try:
        optimization, chosen_trip, chosen_index = _resolve_share_context(route_set_index)
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    if optimization is None or chosen_trip is None or chosen_index is None:
        return HTMLResponse(_translate_request(request, "No optimized plan is available yet."), status_code=400)
    map_payload = build_map_payload(include_routes=True)
    destination = get_destination()
    share_summary = _build_share_summary(chosen_trip, optimization, destination)
    share_map_svg = _build_share_map_svg(map_payload, chosen_index)
    selected_route_set = next(
        (route_set for route_set in map_payload.get("route_sets", []) if route_set.get("route_set_index") == chosen_index),
        map_payload.get("route_sets", [None])[0] if map_payload.get("route_sets") else None,
    )
    context = _template_context(
        request,
        optimization=optimization,
        chosen_trip=chosen_trip,
        chosen_route_set_index=chosen_index,
        map_payload=map_payload,
        selected_route_set=selected_route_set,
        destination=destination,
        share_summary=share_summary,
        share_map_svg=share_map_svg,
        whatsapp_url=f"https://wa.me/?text={quote(share_summary)}",
        # Inlined so the downloaded copy still renders with no server.
        shared_css=(BASE_DIR / "static" / "style.css").read_text(encoding="utf-8"),
    )
    template = templates.get_template("share_plan.html")
    html = template.render(context)
    headers = None
    if download:
        slug = str(chosen_trip["driver_set_name"]).lower().replace(" ", "-")
        headers = {
            "Content-Disposition": f"attachment; filename=dmproject-share-{slug}.html",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        }
    return HTMLResponse(html, headers=headers)


@app.post("/participants/import")
async def import_participants(
    request: Request,
    csv_file: UploadFile = File(...),
    replace_existing: bool = Form(False),
):
    """Import participants from a CSV file."""
    LOG_INFO(f"importing CSV — filename={csv_file.filename!r}, replace={replace_existing}")
    content = await csv_file.read()
    try:
        result = import_participants_csv(
            content.decode("utf-8"), replace_existing=replace_existing
        )
    except Exception as error:
        return render_home(
            request,
            status_code=400,
            error="CSV import failed.",
            field_errors={"csv_file": str(error)},
            scroll_target="participants-panel",
        )
    return render_home(request, success=f"Imported {result['count']} participants from CSV.")


@app.get("/api/map-data")
def map_data(optimize: bool = False):
    """Return participants, destination, and optional route geometry for the map."""
    LOG_DEBUG(f"map data requested — optimize={optimize}")
    return JSONResponse(build_map_payload(include_routes=optimize))


@app.get("/api/reverse-geocode")
def reverse_geocode_endpoint(latitude: float, longitude: float):
    """Resolve a clicked map point into a display address."""
    try:
        payload = reverse_geocode(latitude, longitude)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse(payload)


@app.get("/api/search-address")
def search_address_endpoint(query: str):
    """Return autocomplete suggestions for the map search box."""
    try:
        payload = {"results": search_addresses(query)}
    except ValueError as error:
        return JSONResponse({"error": str(error), "results": []}, status_code=400)
    return JSONResponse(payload)


@app.post("/api/route-food")
async def route_food_endpoint(request: Request):
    """Return selected food locations close to the selected route."""
    payload = await request.json()
    geometry = payload.get("geometry") or []
    mode = str(payload.get("mode") or "off").strip().lower()
    max_distance_km = float(payload.get("max_distance_km") or 3.0)
    if mode not in {"off", "kebab", "kfc", "mcdonalds", "burger_king", "pizza", "cafe", "all"}:
        return JSONResponse({"error": "Unsupported food overlay mode.", "results": []}, status_code=400)
    try:
        results = find_food_along_route(geometry, mode, max_distance_km=max_distance_km)
    except FoodLookupUnavailable:
        return JSONResponse({"error": "unavailable", "results": []})
    return JSONResponse({"results": results})
