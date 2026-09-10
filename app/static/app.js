(function () {
    const config = window.DMProjectConfig || {
        optimizeOnLoad: false,
        fitMode: "all",
        mapDefaults: { latitude: 44.6471, longitude: 10.9252, zoom: 9 },
        tripPresets: [],
        fuelTypeDefaults: {},
    };
    const mapElement = document.getElementById("planner-map");
    const hasMap = Boolean(mapElement && typeof L !== "undefined");
    const staticVersionSuffix = config.staticVersion ? `?v=${encodeURIComponent(config.staticVersion)}` : "";

    const map = hasMap ? L.map("planner-map") : null;
    const markerCluster = hasMap ? L.markerClusterGroup() : null;
    const routesLayer = hasMap ? L.layerGroup().addTo(map) : null;
    const pickupLayer = hasMap ? L.layerGroup().addTo(map) : null;
    const foodLayer = hasMap ? L.layerGroup().addTo(map) : null;
    const summaryElement = document.getElementById("map-summary");
    const mapTarget = document.getElementById("map-target");
    const refreshButton = document.getElementById("refresh-map");
    const searchInput = document.getElementById("map-search");
    const searchResults = document.getElementById("search-results");
    const fitMode = document.getElementById("fit-mode");
    const routeSetSelect = document.getElementById("route-set-select");
    const foodMode = document.getElementById("food-mode");
    const foodOverlayStatus = document.getElementById("food-overlay-status");
    const routeToggles = document.getElementById("route-toggles");
    const favoriteFilter = document.getElementById("favorite-filter");
    const presetSelect = document.getElementById("preset-name");
    const presetNotes = document.getElementById("preset-notes");
    const participantTableBody = document.getElementById("participant-table-body");
    const destinationAddressInput = document.getElementById("destination-address");
    const destinationAddressResults = document.getElementById("destination-address-results");
    const destinationNameInput = document.getElementById("destination-name");
    const destinationLatitudeInput = document.getElementById("destination-latitude");
    const destinationLongitudeInput = document.getElementById("destination-longitude");
    const participantNameInput = document.getElementById("participant-name");
    const participantAddressInput = document.getElementById("participant-address");
    const participantAddressResults = document.getElementById("participant-address-results");
    const participantLocationNameInput = document.getElementById("participant-location-name");
    const participantLatitudeInput = document.getElementById("participant-latitude");
    const participantLongitudeInput = document.getElementById("participant-longitude");
    const meetupSpotNameInput = document.getElementById("meetup-spot-name");
    const meetupSpotAddressInput = document.getElementById("meetup-spot-address");
    const meetupSpotAddressResults = document.getElementById("meetup-spot-address-results");
    const meetupSpotLatitudeInput = document.getElementById("meetup-spot-latitude");
    const meetupSpotLongitudeInput = document.getElementById("meetup-spot-longitude");
    const scrollTarget = document.querySelector("[data-scroll-target]")?.dataset.scrollTarget;

    let activeSelectionMarker = null;
    let draggedRow = null;
    let searchTimeout = null;
    let lastPayload = null;
    let routeLayers = [];
    let pickupLayerGroups = [];
    let foodMarkers = [];
    let selectedRouteSetIndex = "best";
    let foodOverlayAbortController = null;
    let foodOverlayRequestId = 0;
    let prefillFlashTimeout = null;
    const pendingSelectionKey = "dmproject.pendingSelection";
    const scrollPositionKey = "dmproject.scrollY";
    const foodOverlayCache = new Map();

    if (hasMap) {
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap contributors",
        }).addTo(map);
        map.addLayer(markerCluster);
        map.setView([config.mapDefaults.latitude, config.mapDefaults.longitude], config.mapDefaults.zoom);
    }

    function buildIcon(className, label) {
        if (!hasMap) return null;
        return L.divIcon({
            className: `map-pin ${className}`,
            html: `<div>${label}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            popupAnchor: [0, -14],
        });
    }

    function buildImageIcon(className, imagePath, altText) {
        if (!hasMap) return null;
        return L.divIcon({
            className: `map-pin ${className}`,
            html: `<div><img src="${imagePath}" alt="${altText}" draggable="false"></div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            popupAnchor: [0, -14],
        });
    }

    function staticAsset(path) {
        return `${path}${staticVersionSuffix}`;
    }

    function buildRasterIcon(imagePath, className = "pixel-raster-icon", size = 30) {
        if (!hasMap) return null;
        return L.icon({
            iconUrl: staticAsset(imagePath),
            iconSize: [size, size],
            iconAnchor: [Math.round(size / 2), Math.round(size / 2)],
            popupAnchor: [0, -14],
            className,
        });
    }

    const driverIcon = buildIcon("driver-pin", "D");
    const passengerIcon = buildIcon("passenger-pin", "•");
    const destinationIcon = buildRasterIcon("/static/icon-destination-flag-pixel.png", "pixel-raster-icon destination-raster-icon", 32);
    const selectionIcon = buildIcon("selection-pin", "S");
    const meetupIcon = buildIcon("meetup-pin", "P");
    const kebabIcon = buildRasterIcon("/static/icon-food-kebab-pixel.png");
    const kfcIcon = buildRasterIcon("/static/icon-food-kfc-pixel.png");
    const mcdonaldsIcon = buildRasterIcon("/static/icon-food-mcdonalds-pixel.png");
    const burgerKingIcon = buildRasterIcon("/static/icon-food-burger-king-pixel.png");
    const pizzaIcon = buildRasterIcon("/static/icon-food-pizza-pixel.png");
    const cafeIcon = buildIcon("cafe-pin", "☕");

    function meetupSpotForm() { return document.querySelector('form[action="/meetup-spots"]'); }
    function participantFuelSelect() { return document.querySelector('form[action="/participants"] select[name="fuel_type"]'); }
    function participantConsumptionInput() { return document.querySelector('form[action="/participants"] input[name="consumption_l_per_100km"]'); }
    function participantVehicleSelect() { return document.querySelector('form[action="/participants"] select[name="vehicle_type"]'); }
    function participantSeatsInput() { return document.querySelector('form[action="/participants"] input[name="total_seats"]'); }
    function savePendingSelection(target, latitude, longitude, displayName) {
        window.localStorage.setItem(
            pendingSelectionKey,
            JSON.stringify({ target, latitude, longitude, displayName })
        );
    }

    function loadPendingSelection() {
        try {
            const raw = window.localStorage.getItem(pendingSelectionKey);
            return raw ? JSON.parse(raw) : null;
        } catch (_error) {
            return null;
        }
    }

    function clearPendingSelection() {
        window.localStorage.removeItem(pendingSelectionKey);
    }

    function highlightPrefillTarget(element) {
        if (!element) return;
        element.classList.add("prefill-target");
        if (prefillFlashTimeout) window.clearTimeout(prefillFlashTimeout);
        prefillFlashTimeout = window.setTimeout(() => {
            element.classList.remove("prefill-target");
        }, 1600);
    }

    function focusMeetupSpotForm() {
        if (!meetupSpotAddressInput) return;
        const meetupForm = meetupSpotForm();
        if (meetupForm) {
            meetupForm.scrollIntoView({ behavior: "smooth", block: "center" });
            highlightPrefillTarget(meetupForm);
        }
        meetupSpotAddressInput.focus({ preventScroll: true });
    }

    function getSelectedRouteSet(payload) {
        if (!payload || !payload.route_sets || !payload.route_sets.length) return null;
        if (selectedRouteSetIndex === "best") {
            return payload.route_sets.find((routeSet) => routeSet.selected) || payload.route_sets[0];
        }
        return (
            payload.route_sets.find((routeSet) => String(routeSet.route_set_index) === String(selectedRouteSetIndex)) ||
            payload.route_sets[0]
        );
    }

    function fitToPayload(mode, payload) {
        if (!payload) return;
        const bounds = [];
        if (mode === "destination" && payload.destination) {
            bounds.push([payload.destination.latitude, payload.destination.longitude]);
        } else if (mode === "route") {
            const selectedRouteSet = getSelectedRouteSet(payload);
            if (selectedRouteSet) {
                selectedRouteSet.routes.forEach((route) => {
                    route.geometry.forEach((point) => bounds.push(point));
                });
            }
        } else {
            payload.participants.forEach((participant) => bounds.push([participant.latitude, participant.longitude]));
            if (payload.destination) bounds.push([payload.destination.latitude, payload.destination.longitude]);
        }
        if (bounds.length === 1) map.setView(bounds[0], 13);
        else if (bounds.length > 1) map.fitBounds(bounds, { padding: [30, 30] });
    }

    function applyPointToForm(target, latitude, longitude, displayName, labelText) {
        if (target === "meetup") {
            if (!meetupSpotLatitudeInput || !meetupSpotLongitudeInput) return;
            meetupSpotLatitudeInput.value = latitude;
            meetupSpotLongitudeInput.value = longitude;
            if (displayName && meetupSpotAddressInput) {
                meetupSpotAddressInput.value = displayName;
                if (meetupSpotNameInput && !meetupSpotNameInput.value.trim()) {
                    meetupSpotNameInput.value = labelText || displayName.split(",")[0];
                }
            }
            focusMeetupSpotForm();
            savePendingSelection(target, latitude, longitude, displayName || "");
            return;
        }
        if (target === "destination") {
            if (!destinationLatitudeInput || !destinationLongitudeInput) return;
            destinationLatitudeInput.value = latitude;
            destinationLongitudeInput.value = longitude;
            if (displayName && destinationAddressInput) {
                destinationAddressInput.value = displayName;
                if (destinationNameInput && !destinationNameInput.value.trim()) {
                    destinationNameInput.value = labelText || displayName.split(",")[0];
                }
            }
            savePendingSelection(target, latitude, longitude, displayName || "");
            return;
        }
        if (!participantLatitudeInput || !participantLongitudeInput) return;
        participantLatitudeInput.value = latitude;
        participantLongitudeInput.value = longitude;
        if (displayName && participantAddressInput) {
            participantAddressInput.value = displayName;
            if (participantLocationNameInput && !participantLocationNameInput.value.trim()) {
                participantLocationNameInput.value = labelText || displayName.split(",")[0];
            }
        }
        savePendingSelection(target, latitude, longitude, displayName || "");
    }

    async function enrichPointFromCoordinates(target, latitude, longitude) {
        applyPointToForm(target, latitude, longitude, `${latitude}, ${longitude}`, `${latitude}, ${longitude}`);
        try {
            const response = await fetch(`/api/reverse-geocode?latitude=${latitude}&longitude=${longitude}`);
            const payload = await response.json();
            if (!payload.error) {
                applyPointToForm(target, latitude, longitude, payload.display_name, payload.label);
                if (hasMap && activeSelectionMarker) {
                    activeSelectionMarker.bindPopup(`<strong>Selected ${target}</strong><br>${payload.display_name}`).openPopup();
                }
            }
        } catch (_error) {}
    }

    function placeActiveSelectionMarker(target, latitude, longitude, displayName) {
        if (!hasMap) return;
        if (activeSelectionMarker) map.removeLayer(activeSelectionMarker);
        activeSelectionMarker = L.marker([latitude, longitude], {
            draggable: true,
            icon: selectionIcon,
            title: `Selected ${target}`,
        }).addTo(map);
        activeSelectionMarker.bindPopup(
            displayName
                ? `<strong>Selected ${target}</strong><br>${displayName}`
                : `<strong>Selected ${target}</strong><br>Drag to refine the point`
        ).openPopup();
        savePendingSelection(target, latitude, longitude, displayName || "");
        activeSelectionMarker.on("dragend", async (event) => {
            const position = event.target.getLatLng();
            await enrichPointFromCoordinates(target, position.lat.toFixed(6), position.lng.toFixed(6));
        });
    }

    function renderSearchResults(results, container, onSelect) {
        container.innerHTML = "";
        if (!results.length) {
            container.classList.remove("open");
            return;
        }
        results.forEach((result) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "search-result";
            button.innerHTML = `<strong>${result.label || result.display_name.split(",")[0]}</strong><span>${result.display_name}</span>`;
            button.addEventListener("click", () => {
                onSelect(result);
                container.classList.remove("open");
                container.innerHTML = "";
            });
            container.appendChild(button);
        });
        container.classList.add("open");
    }

    function renderLoadingResults(container) {
        container.innerHTML = '<div class="search-result loading-result"><span>Searching addresses...</span></div>';
        container.classList.add("open");
    }

    function attachEditPopupHandler(marker, participantId) {
        marker.on("popupopen", () => {
            const button = document.querySelector(`[data-edit-id="${participantId}"]`);
            if (button) button.addEventListener("click", () => { window.location.href = `/participants/${participantId}/edit#participants-panel`; });
        });
    }

    function routeToggleMarkup(route, index) {
        const durationText = route.duration_min == null ? "time unavailable" : `${Math.round(route.duration_min)} min`;
        const passengerText = route.passenger_names.length
            ? `${route.passenger_names.length} passenger${route.passenger_names.length === 1 ? "" : "s"}`
            : "Solo drive";
        return `
            <label class="route-toggle">
                <div class="route-toggle-head">
                    <span class="route-toggle-title"><span class="swatch" style="background:${route.color}"></span>${route.driver_name}</span>
                    <input type="checkbox" checked data-route-index="${index}">
                </div>
                <div class="route-toggle-badges">
                    <span class="route-chip">${route.distance_km.toFixed(1)} km</span>
                    <span class="route-chip">${durationText}</span>
                    <span class="route-chip">${passengerText}</span>
                </div>
            </label>
        `;
    }

    function syncRouteSetSelect(routeSets) {
        if (!routeSetSelect) return;
        routeSetSelect.innerHTML = "";
        if (!routeSets || !routeSets.length) {
            routeSetSelect.innerHTML = '<option value="best">Best plan</option>';
            routeSetSelect.disabled = true;
            return;
        }
        routeSetSelect.disabled = false;
        routeSets.forEach((routeSet) => {
            const option = document.createElement("option");
            option.value = String(routeSet.route_set_index);
            option.textContent = `${routeSet.driver_set_name} (${routeSet.total_distance_km.toFixed(1)} km)`;
            if ((selectedRouteSetIndex === "best" && routeSet.selected) || String(selectedRouteSetIndex) === String(routeSet.route_set_index)) {
                option.selected = true;
                selectedRouteSetIndex = String(routeSet.route_set_index);
            }
            routeSetSelect.appendChild(option);
        });
    }

    function drawMap(payload) {
        if (!hasMap) return;
        lastPayload = payload;
        markerCluster.clearLayers();
        routesLayer.clearLayers();
        pickupLayer.clearLayers();
        foodLayer.clearLayers();
        routeLayers = [];
        pickupLayerGroups = [];
        foodMarkers = [];
        routeToggles.innerHTML = "";
        syncRouteSetSelect(payload.route_sets || []);

        payload.participants.forEach((participant) => {
            const marker = L.marker([participant.latitude, participant.longitude], {
                title: participant.name,
                icon: participant.can_drive ? driverIcon : passengerIcon,
                opacity: participant.active_in_trip ? 1 : 0.45,
                zIndexOffset: 1100,
            }).bindPopup(
                `<strong>${participant.name}</strong><br>${participant.location_name}<br>` +
                `${participant.active_in_trip ? "Included in this trip" : "Parked for later"}<br>` +
                `${participant.can_drive ? "Potential driver" : "Passenger"}<br>` +
                `<button type="button" class="popup-edit" data-edit-id="${participant.id}">Edit this participant</button>`
            );
            attachEditPopupHandler(marker, participant.id);
            markerCluster.addLayer(marker);
        });

        (payload.meetup_spots || []).forEach((spot) => {
            const participantText = (spot.participant_names || []).length
                ? `<br>Used by: ${(spot.participant_names || []).join(", ")}`
                : "";
            markerCluster.addLayer(
                L.marker([spot.latitude, spot.longitude], {
                    title: spot.name,
                    icon: meetupIcon,
                    zIndexOffset: 1200,
                }).bindPopup(
                    `<strong>Meetup spot</strong><br>${spot.name}` +
                    `${spot.address_text ? `<br>${spot.address_text}` : ""}` +
                    participantText
                )
            );
        });

        if (payload.destination) {
            markerCluster.addLayer(
                L.marker([payload.destination.latitude, payload.destination.longitude], {
                    title: payload.destination.name,
                    icon: destinationIcon,
                    zIndexOffset: 1500,
                }).bindPopup(`<strong>Active destination in use</strong><br>${payload.destination.name}`)
            );
        }

        const selectedRouteSet = getSelectedRouteSet(payload);
        (payload.route_sets || []).forEach((routeSet) => {
            const isSelectedRouteSet = selectedRouteSet && routeSet.route_set_index === selectedRouteSet.route_set_index;
            routeSet.routes.forEach((route) => {
                const polyline = L.polyline(route.geometry, {
                    color: isSelectedRouteSet ? route.color : "#5C6570",
                    weight: isSelectedRouteSet ? 5 : 4,
                    opacity: isSelectedRouteSet ? 0.82 : 0.2,
                    dashArray: isSelectedRouteSet ? null : "10 8",
                }).bindPopup(
                    `<strong>${routeSet.driver_set_name}</strong><br>` +
                    `${route.driver_name}<br>` +
                    `Passengers: ${route.passenger_names.length ? route.passenger_names.join(", ") : "none"}<br>` +
                    `Distance: ${route.distance_km.toFixed(1)} km<br>` +
                    `Time: ${route.duration_min ? Math.round(route.duration_min) + " min" : "time unavailable"}`
                );
                routesLayer.addLayer(polyline);
                if (isSelectedRouteSet) {
                    routeLayers.push(polyline);
                    const pickupMarkers = [];
                    route.pickup_markers.forEach((pickup) => {
                        const labelIcon = L.divIcon({
                            className: "pickup-order-pin",
                            html: `<div>${pickup.order}</div>`,
                            iconSize: [26, 26],
                            iconAnchor: [13, 13],
                        });
                        const marker = L.marker([pickup.latitude, pickup.longitude], { icon: labelIcon }).bindPopup(
                            `<strong>Pickup ${pickup.order}</strong><br>${pickup.name}`
                        );
                        pickupLayer.addLayer(marker);
                        pickupMarkers.push(marker);
                    });
                    pickupLayerGroups.push(pickupMarkers);
                    routeToggles.insertAdjacentHTML("beforeend", routeToggleMarkup(route, routeLayers.length - 1));
                }
            });
        });

        routeToggles.querySelectorAll("[data-route-index]").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                const routeIndex = Number(checkbox.dataset.routeIndex);
                const routeLayer = routeLayers[routeIndex];
                const pickupMarkers = pickupLayerGroups[routeIndex] || [];
                if (!routeLayer) return;
                if (checkbox.checked) {
                    routesLayer.addLayer(routeLayer);
                    pickupMarkers.forEach((marker) => pickupLayer.addLayer(marker));
                } else {
                    routesLayer.removeLayer(routeLayer);
                    pickupMarkers.forEach((marker) => pickupLayer.removeLayer(marker));
                }
            });
        });

        // #map-summary only exists on /planning; /setup shows the same map without it.
        if (summaryElement) {
            if (selectedRouteSet) {
                summaryElement.classList.remove("muted");
                summaryElement.innerHTML = `<strong>${selectedRouteSet.driver_set_name}</strong><span>Fitness ${selectedRouteSet.fitness.toFixed(3)}</span><span>${selectedRouteSet.total_distance_km.toFixed(1)} km</span><span>${selectedRouteSet.total_duration_min == null ? "time unavailable" : Math.round(selectedRouteSet.total_duration_min) + " min"}</span>`;
            } else {
                summaryElement.classList.add("muted");
                summaryElement.textContent = "Run optimization to preview routes on the map.";
            }
        }

        fitToPayload(fitMode.value || config.fitMode, payload);
        loadFoodOverlay();
    }

    async function loadMap(optimize) {
        if (!hasMap) return;
        const response = await fetch(`/api/map-data?optimize=${optimize ? "true" : "false"}`);
        drawMap(await response.json());
    }

    function setFoodOverlayStatus(message) {
        if (foodOverlayStatus) foodOverlayStatus.textContent = message || "";
    }

    function buildFoodOverlayCacheKey(routeSet, mode) {
        const geometry = routeSet.routes.flatMap((route) => route.geometry);
        const sampled = geometry.filter((_point, index) => index % 8 === 0).slice(0, 80);
        return JSON.stringify({
            mode,
            route_set_index: routeSet.route_set_index,
            sampled,
        });
    }

    function renderFoodOverlay(points) {
        if (!hasMap) return;
        foodLayer.clearLayers();
        foodMarkers = [];
        (points || []).forEach((point) => {
            const iconByKind = {
                kebab: kebabIcon,
                kfc: kfcIcon,
                mcdonalds: mcdonaldsIcon,
                burger_king: burgerKingIcon,
                pizza: pizzaIcon,
                cafe: cafeIcon,
            };
            const icon = iconByKind[point.kind] || kebabIcon;
            const detourLine =
                point.detour_min !== undefined && point.detour_min !== null
                    ? `<br>Detour: ~${point.detour_km.toFixed(1)} km / ${Math.max(1, Math.round(point.detour_min))} min`
                    : "";
            const marker = L.marker([point.latitude, point.longitude], { icon, zIndexOffset: -200 }).bindPopup(
                `<strong>${point.name}</strong><br>${point.kind.toUpperCase()}<br>${point.distance_to_route_km.toFixed(2)} km from route${detourLine}`
            );
            foodLayer.addLayer(marker);
            foodMarkers.push(marker);
        });
    }

    async function loadFoodOverlay() {
        if (!hasMap) return;
        foodLayer.clearLayers();
        foodMarkers = [];
        if (foodOverlayAbortController) {
            foodOverlayAbortController.abort();
            foodOverlayAbortController = null;
        }
        if (!foodMode || foodMode.value === "off" || !lastPayload) {
            setFoodOverlayStatus("");
            return;
        }
        const selectedRouteSet = getSelectedRouteSet(lastPayload);
        if (!selectedRouteSet || !selectedRouteSet.routes.length) {
            setFoodOverlayStatus("");
            return;
        }
        const geometry = selectedRouteSet.routes.flatMap((route) => route.geometry);
        if (!geometry.length) {
            setFoodOverlayStatus("");
            return;
        }
        const selectedMode = foodMode.value;
        const cacheKey = buildFoodOverlayCacheKey(selectedRouteSet, selectedMode);
        if (foodOverlayCache.has(cacheKey)) {
            const cachedPoints = foodOverlayCache.get(cacheKey) || [];
            renderFoodOverlay(cachedPoints);
            setFoodOverlayStatus(
                cachedPoints.length
                    ? `Showing ${cachedPoints.length} ${selectedMode === "all" ? "food stops" : selectedMode.replace("_", " ")} stop${cachedPoints.length === 1 ? "" : "s"}.`
                    : `No ${selectedMode === "all" ? "food" : selectedMode.replace("_", " ")} stops found within 3 km of this route.`
            );
            return;
        }
        setFoodOverlayStatus(`Loading ${selectedMode === "all" ? "food stops" : selectedMode.replace("_", " ")} near the route...`);
        const requestId = ++foodOverlayRequestId;
        foodOverlayAbortController = new AbortController();
        try {
            const response = await fetch("/api/route-food", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: foodOverlayAbortController.signal,
                body: JSON.stringify({ geometry, mode: foodMode.value, max_distance_km: 3.0 }),
            });
            if (!response.ok) throw new Error("Food overlay request failed.");
            const payload = await response.json();
            if (requestId !== foodOverlayRequestId || selectedMode !== foodMode.value) return;
            if (payload.error === "unavailable") {
                setFoodOverlayStatus("Food lookup service is unreachable — try again in a minute.");
                return;
            }
            const results = payload.results || [];
            foodOverlayCache.set(cacheKey, results);
            if (foodOverlayCache.size > 12) {
                const oldestKey = foodOverlayCache.keys().next().value;
                foodOverlayCache.delete(oldestKey);
            }
            renderFoodOverlay(results);
            setFoodOverlayStatus(
                results.length
                    ? `Showing ${results.length} ${selectedMode === "all" ? "food stops" : selectedMode.replace("_", " ")} stop${results.length === 1 ? "" : "s"}.`
                    : `No ${selectedMode === "all" ? "food" : selectedMode.replace("_", " ")} stops found within 3 km of this route.`
            );
        } catch (error) {
            if (error && error.name === "AbortError") return;
            setFoodOverlayStatus("Food overlay is unavailable right now.");
        } finally {
            if (requestId === foodOverlayRequestId) {
                foodOverlayAbortController = null;
            }
        }
    }

    if (hasMap) {
        map.on("click", async (event) => {
            const latitude = event.latlng.lat.toFixed(6);
            const longitude = event.latlng.lng.toFixed(6);
            placeActiveSelectionMarker(mapTarget.value, Number(latitude), Number(longitude), "");
            await enrichPointFromCoordinates(mapTarget.value, latitude, longitude);
        });
    }

    if (hasMap && refreshButton) refreshButton.addEventListener("click", () => loadMap(true));
    if (hasMap && fitMode) fitMode.addEventListener("change", () => fitToPayload(fitMode.value, lastPayload));
    if (hasMap && routeSetSelect) {
        routeSetSelect.addEventListener("change", () => {
            selectedRouteSetIndex = routeSetSelect.value;
            drawMap(lastPayload);
        });
    }
    if (hasMap && foodMode) {
        foodMode.addEventListener("change", () => loadFoodOverlay());
    }

    async function fetchAddressSuggestions(query, container, onSelect) {
        if (query.length < 3) {
            renderSearchResults([], container, onSelect);
            return;
        }
        try {
            const response = await fetch(`/api/search-address?query=${encodeURIComponent(query)}`);
            const payload = await response.json();
            renderSearchResults(payload.results || [], container, onSelect);
        } catch (_error) {
            renderSearchResults([], container, onSelect);
        }
    }

    if (searchInput && searchResults) {
        searchInput.addEventListener("input", () => {
            const query = searchInput.value.trim();
            clearTimeout(searchTimeout);
            if (query.length >= 3) renderLoadingResults(searchResults);
            else renderSearchResults([], searchResults, () => {});
            searchTimeout = window.setTimeout(() => {
                fetchAddressSuggestions(query, searchResults, (result) => {
                    const latitude = Number(result.latitude);
                    const longitude = Number(result.longitude);
                    if (hasMap) map.setView([latitude, longitude], 14);
                    applyPointToForm(mapTarget.value, latitude.toFixed(6), longitude.toFixed(6), result.display_name, result.label);
                    placeActiveSelectionMarker(mapTarget.value, latitude, longitude, result.display_name);
                });
            }, 120);
        });
    }

    function attachFormAutocomplete(input, container, target) {
        if (!input || !container) return;
        let timeout = null;
        input.addEventListener("input", () => {
            const query = input.value.trim();
            clearTimeout(timeout);
            if (query.length >= 3) renderLoadingResults(container);
            else renderSearchResults([], container, () => {});
            timeout = window.setTimeout(() => {
                fetchAddressSuggestions(query, container, (result) => {
                    const latitude = Number(result.latitude);
                    const longitude = Number(result.longitude);
                    if (hasMap) map.setView([latitude, longitude], 14);
                    applyPointToForm(target, latitude.toFixed(6), longitude.toFixed(6), result.display_name, result.label);
                    placeActiveSelectionMarker(target, latitude, longitude, result.display_name);
                });
            }, 120);
        });
    }

    document.addEventListener("click", (event) => {
        if (searchResults && searchInput && !searchResults.contains(event.target) && event.target !== searchInput) {
            searchResults.classList.remove("open");
        }
        if (destinationAddressResults && !destinationAddressResults.contains(event.target) && event.target !== destinationAddressInput) {
            destinationAddressResults.classList.remove("open");
        }
        if (participantAddressResults && !participantAddressResults.contains(event.target) && event.target !== participantAddressInput) {
            participantAddressResults.classList.remove("open");
        }
        if (meetupSpotAddressResults && !meetupSpotAddressResults.contains(event.target) && event.target !== meetupSpotAddressInput) {
            meetupSpotAddressResults.classList.remove("open");
        }
    });

    document.querySelectorAll("[data-confirm]").forEach((element) => {
        element.addEventListener("click", (event) => {
            const message = element.getAttribute("data-confirm") || "Are you sure?";
            if (!window.confirm(message)) event.preventDefault();
        });
    });

    document.querySelectorAll('form[method="post"], form:not([method])').forEach((form) => {
        form.addEventListener("submit", () => {
            window.sessionStorage.setItem(scrollPositionKey, String(window.scrollY));
        });
    });

    if (favoriteFilter) {
        favoriteFilter.addEventListener("input", () => {
            const query = favoriteFilter.value.trim().toLowerCase();
            document.querySelectorAll(".favorite-card").forEach((card) => {
                const haystack = card.getAttribute("data-filter-text") || "";
                card.style.display = !query || haystack.includes(query) ? "" : "none";
            });
        });
    }

    if (presetSelect && presetNotes) {
        const presetMap = {};
        (config.tripPresets || []).forEach((preset) => {
            presetMap[preset.name] = preset;
        });
        presetSelect.addEventListener("change", () => {
            const selected = presetMap[presetSelect.value];
            const tripNameInput = document.querySelector('form[action="/history/save"] [name="trip_name"]');
            const notesInput = document.querySelector('form[action="/history/save"] [name="notes"]');
            if (!selected) {
                presetNotes.textContent = "";
                return;
            }
            presetNotes.textContent = selected.notes || "";
            if (tripNameInput && !tripNameInput.value.trim()) tripNameInput.value = selected.trip_name;
            if (notesInput && !notesInput.value.trim()) notesInput.value = selected.notes;
        });
    }

    function updateOrderBadges() {
        if (!participantTableBody) return;
        participantTableBody.querySelectorAll("tr[data-participant-row]").forEach((row, index) => {
            const badge = row.querySelector(".order-badge");
            if (badge) badge.textContent = String(index + 1);
        });
    }

    async function persistParticipantOrder() {
        if (!participantTableBody) return;
        const order = Array.from(participantTableBody.querySelectorAll("tr[data-participant-row]"))
            .map((row) => row.dataset.participantRow)
            .join(",");
        const body = new URLSearchParams({ order });
        try {
            await fetch("/participants/reorder", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: body.toString(),
            });
        } catch (_error) {}
    }

    if (participantTableBody) {
        participantTableBody.querySelectorAll("tr[data-participant-row]").forEach((row) => {
            row.addEventListener("dragstart", () => {
                draggedRow = row;
                row.classList.add("dragging-row");
            });
            row.addEventListener("dragend", () => {
                row.classList.remove("dragging-row");
                draggedRow = null;
                updateOrderBadges();
                persistParticipantOrder();
            });
            row.addEventListener("dragover", (event) => {
                event.preventDefault();
                if (!draggedRow || draggedRow === row) return;
                const bounds = row.getBoundingClientRect();
                const insertBefore = event.clientY < bounds.top + bounds.height / 2;
                participantTableBody.insertBefore(draggedRow, insertBefore ? row : row.nextSibling);
            });
        });
        updateOrderBadges();
    }

    function showToast(message, isError = false) {
        const toast = document.createElement("div");
        toast.className = `toast ${isError ? "toast-error" : "toast-success"}`;
        toast.textContent = message;
        document.body.appendChild(toast);
        window.setTimeout(() => toast.classList.add("show"), 20);
        window.setTimeout(() => toast.classList.remove("show"), 3400);
    }

    const banner = document.querySelector(".banner.success, .banner.error");
    if (banner) {
        showToast(banner.textContent.trim(), banner.classList.contains("error"));
    }

    const helpToggle = document.getElementById("help-toggle");
    const pageGuide = document.getElementById("page-guide");
    function applyHelp(on, syncGuide) {
        document.body.classList.toggle("show-help", on);
        if (helpToggle) helpToggle.setAttribute("aria-pressed", String(on));
        // Only the button drives the <details>: on load it keeps whatever the
        // template rendered, so a guide the reader collapsed stays collapsed.
        if (pageGuide && syncGuide) pageGuide.open = on;
    }
    if (helpToggle) {
        let helpOn = true; // first visit: hints on
        try {
            helpOn = window.localStorage.getItem("dmproject.help") !== "off";
        } catch (_error) {
            /* storage blocked: stay on */
        }
        applyHelp(helpOn);
        helpToggle.addEventListener("click", () => {
            const next = !document.body.classList.contains("show-help");
            applyHelp(next, true);
            try {
                window.localStorage.setItem("dmproject.help", next ? "on" : "off");
            } catch (_error) {
                /* private mode: choice just won't persist */
            }
        });
    }

    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
            document.documentElement.dataset.theme = nextTheme;
            try {
                window.localStorage.setItem("dmproject.theme", nextTheme);
            } catch (_error) {
                /* private mode: theme just won't persist */
            }
        });
    }

    // Share page: copying or sending a driver message posts an envelope into the strip postbox.
    const postbox = document.getElementById("postbox");
    document.addEventListener("click", (event) => {
        const sender = event.target.closest("[data-post]");
        if (!sender || !postbox || !sender.animate || reducedMotion) return;
        const from = sender.getBoundingClientRect();
        const to = postbox.getBoundingClientRect();
        const letter = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttribute("href", "#pic-envelope");
        letter.appendChild(use);
        letter.setAttribute("class", "pic post-flight");
        letter.style.left = `${from.left}px`;
        letter.style.top = `${from.top}px`;
        document.body.appendChild(letter);
        const dx = to.left + to.width / 2 - 14 - from.left;
        const dy = to.top + to.height * 0.35 - from.top;
        letter
            .animate(
                [
                    { transform: "translate(0, 0) scale(1)", opacity: 1 },
                    { transform: `translate(${dx * 0.5}px, ${dy - 80}px) scale(0.9)`, opacity: 1, offset: 0.55 },
                    { transform: `translate(${dx}px, ${dy}px) scale(0.4)`, opacity: 0 },
                ],
                { duration: 950, easing: "ease-in-out" }
            )
            .onfinish = () => letter.remove();
        postbox.animate(
            [{ transform: "rotate(0)" }, { transform: "rotate(-7deg)" }, { transform: "rotate(5deg)" }, { transform: "rotate(0)" }],
            { duration: 420, delay: 850 }
        );
    });

    document.addEventListener("click", (event) => {
        const copyButton = event.target.closest("[data-copy-text]");
        if (!copyButton) return;
        event.preventDefault();
        const text = copyButton.dataset.copyText || "";
        navigator.clipboard
            .writeText(text)
            .then(() => showToast(copyButton.dataset.copyDone || "Link copied"))
            .catch(() => showToast("Could not copy the link", true));
    });

    document.querySelectorAll('form[action="/destination"], form[action="/participants"]').forEach((form) => {
        form.addEventListener("submit", () => clearPendingSelection());
    });

    const workspaceTabs = Array.from(document.querySelectorAll("[role=tab][data-tab]"));
    function activateWorkspaceTab(slug) {
        if (!workspaceTabs.length) return;
        workspaceTabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === slug)));
        document.querySelectorAll(".ws-panel").forEach((panel) => {
            panel.hidden = panel.id !== `panel-${slug}`;
        });
        // Keep ?tab= in sync so a reload or a shared link opens the same panel.
        const url = new URL(window.location.href);
        url.searchParams.set("tab", slug);
        window.history.replaceState(null, "", url);
        // Leaflet needs a nudge: the map column is resized while a panel is hidden.
        if (map) window.setTimeout(() => map.invalidateSize(), 60);
    }
    workspaceTabs.forEach((tab) => tab.addEventListener("click", () => activateWorkspaceTab(tab.dataset.tab)));

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-show-route-set]");
        if (!button || !routeSetSelect) return;
        routeSetSelect.value = button.dataset.showRouteSet;
        routeSetSelect.dispatchEvent(new Event("change"));
        mapElement?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    if (scrollTarget) {
        const targetElement = document.getElementById(scrollTarget);
        if (targetElement) {
            window.setTimeout(() => {
                targetElement.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 120);
        }
    } else {
        const storedScroll = window.sessionStorage.getItem(scrollPositionKey);
        if (storedScroll) {
            window.setTimeout(() => {
                window.scrollTo(0, Number(storedScroll));
                window.sessionStorage.removeItem(scrollPositionKey);
            }, 80);
        }
    }

    attachFormAutocomplete(destinationAddressInput, destinationAddressResults, "destination");
    attachFormAutocomplete(participantAddressInput, participantAddressResults, "participant");
    attachFormAutocomplete(meetupSpotAddressInput, meetupSpotAddressResults, "meetup");

    const fuelSelect = participantFuelSelect();
    const consumptionInput = participantConsumptionInput();
    if (fuelSelect && consumptionInput) {
        // Empty seed: a value already in the field came from the edited
        // participant, so the fuel default must not overwrite it on load.
        let lastAutoFilledValue = "";
        const applyDefaultConsumption = () => {
            const defaultValue = config.fuelTypeDefaults?.[fuelSelect.value];
            const currentValue = consumptionInput.value.trim();
            if (defaultValue == null) return;
            if (!currentValue || currentValue === lastAutoFilledValue) {
                const nextValue = String(defaultValue);
                consumptionInput.value = nextValue;
                lastAutoFilledValue = nextValue;
            }
        };
        fuelSelect.addEventListener("change", applyDefaultConsumption);
        consumptionInput.addEventListener("input", () => {
            lastAutoFilledValue = consumptionInput.value.trim();
        });
        applyDefaultConsumption();

        const vehicleSelect = participantVehicleSelect();
        const seatsInput = participantSeatsInput();
        if (vehicleSelect) {
            const MOTORBIKE_CONSUMPTION = "4"; // motorbike: typical 4 L/100km
            vehicleSelect.addEventListener("change", () => {
                const currentValue = consumptionInput.value.trim();
                if (vehicleSelect.value === "motorbike") {
                    if (!currentValue || currentValue === lastAutoFilledValue) {
                        consumptionInput.value = MOTORBIKE_CONSUMPTION;
                        lastAutoFilledValue = MOTORBIKE_CONSUMPTION;
                    }
                    if (seatsInput && !seatsInput.value.trim()) seatsInput.value = "2";
                } else if (Number(currentValue) === Number(MOTORBIKE_CONSUMPTION)) {
                    // Numeric compare: Jinja renders the stored 4 as "4.0".
                    lastAutoFilledValue = MOTORBIKE_CONSUMPTION;
                    applyDefaultConsumption();
                }
            });
        }
    }

    // Home road strip: random traffic. Without JS or with reduced motion the CSS loop keeps driving.
    const roadLane = document.querySelector(".road-lane");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (roadLane && roadLane.animate && !reducedMotion) {
        // [symbol, weight, min seconds, max seconds, colour tokens]. Fun ones are rare on purpose.
        const VEHICLES = [
            ["car", 20, 16, 24, ["--sign-green", "--sign-blue", "--sign-red", "--asphalt"]],
            ["van", 10, 18, 26, ["--sign-blue", "--asphalt", "--sign-green"]],
            ["vespa", 10, 18, 26, ["--sign-red", "--sign-blue", "--sign-green"]],
            ["motorbike", 8, 10, 14, ["--asphalt", "--sign-red"]],
            ["taxi", 8, 16, 22, ["--sign-yellow"]],
            ["bicycle", 8, 30, 40, ["--sign-green", "--sign-red", "--sign-blue"]],
            ["bus", 6, 24, 32, ["--sign-yellow", "--sign-blue"]],
            ["truck", 6, 26, 34, ["--sign-blue", "--sign-green", "--sign-red"]],
            ["ape", 6, 26, 34, ["--sign-blue", "--sign-green"]],
            ["tractor", 4, 34, 44, ["--sign-green", "--sign-red"]],
            ["campervan", 4, 22, 30, ["--sign-green", "--sign-brown"]],
            ["clown-car", 1, 20, 30, ["--sign-yellow", "--sign-blue"]],
            ["robotaxi", 1, 10, 14, ["--sign-blue"]],
            ["limo", 1, 30, 40, ["--asphalt"]],
            ["icecream-van", 1, 26, 34, ["--sign-red", "--sign-blue"]],
            ["wienermobile", 1, 22, 30, ["--sign-red"]],
            ["ufo", 1, 12, 18, ["--asphalt"]],
            ["unicycle", 1, 34, 44, ["--sign-red"]],
            ["penny-farthing", 1, 30, 40, ["--asphalt", "--sign-brown"]],
        ];
        const totalWeight = VEHICLES.reduce((sum, vehicle) => sum + vehicle[1], 0);
        const between = (low, high) => low + Math.random() * (high - low);
        const pickOne = (items) => items[Math.floor(Math.random() * items.length)];
        const shift = (x) => `translateX(${x}px)`;
        // Speed profiles: steady; braking for the dinner exit then pulling away; crawling out of traffic then accelerating.
        const PROFILES = [
            (from, to) => [{ transform: shift(from) }, { transform: shift(to) }],
            (from, to) => [
                { transform: shift(from), easing: "ease-out" },
                { offset: 0.6, transform: shift(from + (to - from) * 0.8), easing: "linear" },
                { offset: 0.8, transform: shift(from + (to - from) * 0.88), easing: "ease-in" },
                { transform: shift(to) },
            ],
            (from, to) => [
                { transform: shift(from), easing: "ease-in" },
                { offset: 0.5, transform: shift(from + (to - from) * 0.25), easing: "ease-out" },
                { transform: shift(to) },
            ],
        ];
        function pickVehicle() {
            let roll = Math.random() * totalWeight;
            const vehicle = VEHICLES.find((candidate) => (roll -= candidate[1]) < 0) || VEHICLES[0];
            return { vehicle, seconds: between(vehicle[2], vehicle[3]) };
        }
        // Two lanes: the near one and the overtaking one behind it. A vehicle must not finish before the
        // one ahead in its lane (plus a margin for the eased profiles and its own width), so a fast car
        // either moves out to overtake or, if both lanes are busy, sits behind the slow one.
        const lanes = [
            { bottom: 0, z: 2, leader: null, finishAt: 0, seconds: 0 },
            { bottom: 22, z: 1, leader: null, finishAt: 0, seconds: 0 },
        ];
        // Minimum duration for a newcomer in this lane; Infinity while the leader still blocks the entry.
        function laneNeeds(lane, now) {
            if (!lane.leader || !lane.leader.isConnected) return 0;
            const cleared = lane.leader.getBoundingClientRect().left - roadLane.getBoundingClientRect().left;
            if (cleared < 40) return Infinity;
            return Math.max(0, lane.finishAt - now) + 0.3 * lane.seconds;
        }
        function driveBy({ vehicle, seconds }) {
            const [id, , , , colours] = vehicle;
            const now = performance.now() / 1000;
            const needs = lanes.map((lane) => laneNeeds(lane, now));
            let laneIndex = needs.findIndex((need) => seconds >= need);
            if (laneIndex < 0 && id !== "ufo") {
                laneIndex = needs[1] < needs[0] ? 1 : 0;
                if (!Number.isFinite(needs[laneIndex])) {
                    window.setTimeout(() => driveBy({ vehicle, seconds }), 700);
                    return;
                }
                seconds = needs[laneIndex];
            }
            const lane = lanes[Math.max(laneIndex, 0)];
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
            use.setAttribute("href", `#pic-${id}`);
            svg.appendChild(use);
            svg.setAttribute("class", `pic road-vehicle road-vehicle-${id}`);
            svg.style.color = `var(${pickOne(colours)})`;
            svg.style.zIndex = lane.z;
            if (id !== "ufo") svg.style.bottom = `${lane.bottom}px`;
            roadLane.appendChild(svg);
            const width = svg.getBoundingClientRect().width;
            const run = svg.animate(pickOne(PROFILES)(-width, roadLane.clientWidth), { duration: seconds * 1000, fill: "forwards" });
            run.onfinish = () => svg.remove();
            if (id !== "ufo") Object.assign(lane, { leader: svg, finishAt: now + seconds, seconds });
            window.setTimeout(() => driveBy(pickVehicle()), between(0.8, 4.5) * 1000);
        }
        roadLane.classList.add("road-lane-live");
        roadLane.replaceChildren();
        driveBy(pickVehicle());
    }

    if (hasMap) {
        loadMap(config.optimizeOnLoad);
        const pendingSelection = loadPendingSelection();
        if (pendingSelection) {
            placeActiveSelectionMarker(
                pendingSelection.target,
                Number(pendingSelection.latitude),
                Number(pendingSelection.longitude),
                pendingSelection.displayName || ""
            );
        }
    }

    const solverSelect = document.getElementById("solver-select");
    if (solverSelect) {
        solverSelect.addEventListener("change", () => {
            document.querySelectorAll("#solver-hints .solver-hint").forEach((card) => {
                card.classList.toggle("active", card.dataset.solver === solverSelect.value);
            });
        });
    }
})();
