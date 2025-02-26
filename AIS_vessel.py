import asyncio
import websockets
import sys
import json
import threading
import os
from folium.plugins import MarkerCluster
import webbrowser
import folium
import time
import copy
from datetime import datetime
import signal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from html import escape

# API Configuration
API_KEY = "<Your API Key Here>"  # Replace with your actual key
MAP_FILE = "vessel_map.html"
MAP_UPDATE_INTERVAL = 10  # in seconds
MMSI_FILTER_FILE = "watched_vessels.txt"
PORT = 8080
MAX_MARKERS = 5000  # Optional cap for performance; set to None to disable

# Global variables
vessels = {}
vessels_lock = threading.Lock()
running = True
filter_enabled = False
search_term = ""

async def connect_to_ais_stream():
    """Connect to AISStream.io WebSocket API and process incoming messages"""
    print("Connecting to AISStream.io...")
    backoff = 5
    while running:
        try:
            async with websockets.connect("wss://stream.aisstream.io/v0/stream") as websocket:
                subscribe_message = {
                    "APIKey": API_KEY,
                    # Adjust these coordinates as you wish, you can use maps for determining coordinates
                    # You can add more bounding boxes if needed. Refer below for format.
                    # Format: [[min_lat, min_lon], [max_lat, max_lon]] 
                    "BoundingBoxes": [
                        [[48.155583, -2.926026], [53.684314, 10.279541]], # Northern Europe
                        [[51.373832, 6.060791], [50.104884, 8.665237]], # Rhine river
                        [[50.001780, 8.277969], [47.614036, 7.589722]], # Rhine river
                        [[42.703819, 10.587158], [41.075161, 2.103882]], # Mediterranean
                        [[43.416062, -8.340454], [44.936130, -0.617065]], # Bay of Biscay
                        [[46.166358, -1.210327], [48.232469, 48.232469]], # Central Europe
                        [[46.814774, 1.689119], [47.003679, 8.403625]], # Central Europe
                        [[47.989921, 7.207642], [48.806863, 9.228516]], # Southern Germany
                        [[35.0, -10.0], [60.0, 30.0]] # Europe
                    ],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
                }
                await websocket.send(json.dumps(subscribe_message))
                print("Subscription sent, waiting for vessel data...")
                backoff = 5
                async for message_json in websocket:
                    if not running:
                        break
                    try:
                        message = json.loads(message_json)
                        process_ais_message(message)
                    except Exception as e:
                        print(f"Error processing message: {e}")
        except websockets.exceptions.ConnectionClosedError as e:
            if running:
                print(f"Connection closed: {e}. Reconnecting in {backoff} seconds...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        except Exception as e:
            if running:
                print(f"Unexpected error: {e}. Reconnecting in {backoff} seconds...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

def process_ais_message(message):
    """Process incoming AIS message and update vessels dictionary"""
    message_type = message.get("MessageType")
    metadata = message.get("Metadata", {})
    mmsi = None

    with vessels_lock:
        if message_type == "PositionReport":
            ais_message = message.get("Message", {}).get("PositionReport", {})
            if ais_message:
                mmsi = str(ais_message.get("UserID"))
                if mmsi:
                    if mmsi not in vessels:
                        vessels[mmsi] = {"last_update": time.time()}
                    vessels[mmsi].update({
                        "mmsi": mmsi,
                        "lat": ais_message.get("Latitude"),
                        "lon": ais_message.get("Longitude"),
                        "course": ais_message.get("Course"),
                        "speed": ais_message.get("Speed"),
                        "heading": ais_message.get("TrueHeading"),
                        "last_update": time.time()
                    })
        elif message_type == "ShipStaticData":
            ais_message = message.get("Message", {}).get("ShipStaticData", {})
            if ais_message:
                mmsi = str(ais_message.get("UserID"))
                if mmsi:
                    if mmsi not in vessels:
                        vessels[mmsi] = {"last_update": time.time()}
                    vessels[mmsi].update({
                        "mmsi": mmsi,
                        "name": ais_message.get("Name", "").strip(),
                        "ship_type": ais_message.get("ShipType"),
                        "length": ais_message.get("Length"),
                        "width": ais_message.get("Width"),
                        "callsign": ais_message.get("CallSign", "").strip(),
                        "last_update": time.time()
                    })
                    if "Latitude" in metadata and "Longitude" in metadata:
                        vessels[mmsi].update({
                            "lat": metadata.get("Latitude"),
                            "lon": metadata.get("Longitude")
                        })

def get_ship_type_name(type_code):
    """Convert AIS ship type code to readable name"""
    if not type_code:
        return "Unknown"
    ship_types = {
        (20, 29): "Wing in ground (WIG)",
        (30, 39): "Fishing vessels",
        (40, 49): "High-Speed craft",
        (50, 59): "Special craft",
        (60, 69): "Passenger vessel",
        (70, 79): "Cargo vessel",
        (80, 89): "Tanker",
        (90, 99): "Other"
    }
    for range_tuple, name in ship_types.items():
        if range_tuple[0] <= type_code <= range_tuple[1]:
            return name
    return "Unknown"

def load_filtered_mmsi():
    """Load MMSI numbers from filter file"""
    filtered_mmsi = set()
    try:
        if os.path.exists(MMSI_FILTER_FILE):
            with open(MMSI_FILTER_FILE, 'r') as f:
                for line in f:
                    mmsi = line.strip()
                    if mmsi:
                        filtered_mmsi.add(mmsi)
            print(f"Loaded {len(filtered_mmsi)} vessels to watch from {MMSI_FILTER_FILE}")
        else:
            print(f"Filter file {MMSI_FILTER_FILE} not found. Creating empty watchlist.")
            open(MMSI_FILTER_FILE, 'a').close()
    except Exception as e:
        print(f"Error loading filter file: {e}")
    return filtered_mmsi

def save_mmsi_to_filter(mmsi):
    """Add a new MMSI to the filter file"""
    try:
        with open(MMSI_FILTER_FILE, 'a+') as f:
            f.write(f"{mmsi}\n")
        print(f"Added MMSI {mmsi} to watch list")
        return True
    except Exception as e:
        print(f"Error saving MMSI to filter: {e}")
        return False

def create_map():
    """Create and save a map with vessel markers and filter controls"""
    global filter_enabled, search_term
    with vessels_lock:
        vessels_copy = copy.deepcopy(vessels)

    filtered_mmsi = load_filtered_mmsi() if filter_enabled else set()
    all_positions = []
    vessels_to_show = {}

    current_time = time.time()
    for mmsi, vessel in vessels_copy.items():
        if "lat" not in vessel or "lon" not in vessel or current_time - vessel.get("last_update", 0) > 1800:
            continue
        if filter_enabled and mmsi not in filtered_mmsi:
            continue
        vessels_to_show[mmsi] = vessel
        all_positions.append((vessel.get("lat", 0), vessel.get("lon", 0)))

    # Apply search filter if provided and not empty
    if search_term:
        print(f"Applying search filter for: '{search_term}'")
        filtered_vessels = {}
        for mmsi, vessel in vessels_to_show.items():
            vessel_name = vessel.get("name", "").lower()
            vessel_mmsi = mmsi.lower()
            if search_term.lower() in vessel_name or search_term.lower() in vessel_mmsi:
                filtered_vessels[mmsi] = vessel
        vessels_to_show = filtered_vessels
        all_positions = [(v.get("lat", 0), v.get("lon", 0)) for v in filtered_vessels.values()]
    else:
        print("No search term applied")

    # Optional: Limit number of markers for performance
    if MAX_MARKERS and len(vessels_to_show) > MAX_MARKERS:
        print(f"Limiting to {MAX_MARKERS} most recent vessels out of {len(vessels_to_show)}")
        vessels_to_show = dict(sorted(vessels_to_show.items(), key=lambda x: x[1]["last_update"], reverse=True)[:MAX_MARKERS])
        all_positions = [(v.get("lat", 0), v.get("lon", 0)) for v in vessels_to_show.values()]

    center = [48.0, 10.0] if not all_positions else [
        sum(pos[0] for pos in all_positions) / len(all_positions),
        sum(pos[1] for pos in all_positions) / len(all_positions)
    ]

    m = folium.Map(location=center, zoom_start=6, tiles="cartodb positron")
    marker_cluster = MarkerCluster(
        maxClusterRadius=30,  # Smaller radius for tighter clusters
        spiderfyOnMaxZoom=False,  # Avoid overwhelming at max zoom
        disableClusteringAtZoom=15  # Show individual markers at higher zoom
    ).add_to(m)

    active_vessels = 0
    filtered_vessels = 0

    print(f"Adding {len(vessels_to_show)} markers to the map")
    for mmsi, vessel in vessels_to_show.items():
        active_vessels += 1
        if filter_enabled and mmsi in filtered_mmsi:
            filtered_vessels += 1

        ship_type = vessel.get("ship_type", 0)
        color = "gray"
        if ship_type:
            if 60 <= ship_type <= 69: color = "green"
            elif 70 <= ship_type <= 79: color = "blue"
            elif 80 <= ship_type <= 89: color = "red"

        name = vessel.get("name", "Unknown")
        # Simplified popup to reduce rendering load
        popup_content = f"{name} ({mmsi})<br>Type: {get_ship_type_name(ship_type)}"
        folium.Marker(
            location=[vessel["lat"], vessel["lon"]],
            popup=folium.Popup(popup_content, max_width=200),
            icon=folium.Icon(color=color, icon="ship", prefix="fa"),
            tooltip=f"{name} ({mmsi})"
        ).add_to(marker_cluster)

    legend_html = """
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000; background-color: white; 
                padding: 10px; border: 2px solid grey; border-radius: 5px;">
        <p><b>Vessel Types:</b></p>
        <p><i class="fa fa-ship" style="color:green"></i> Passenger</p>
        <p><i class="fa fa-ship" style="color:blue"></i> Cargo</p>
        <p><i class="fa fa-ship" style="color:red"></i> Tanker</p>
        <p><i class="fa fa-ship" style="color:gray"></i> Other</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    search_term_safe = escape(search_term)
    control_panel = f"""
    <div id="control-panel" style="position: fixed; top: 70px; right: 10px; z-index: 1000; background-color: white; 
                                  padding: 15px; border: 2px solid grey; border-radius: 5px; max-width: 300px;">
        <h5>Filter Controls</h5>
        <div class="form-group">
            <div class="custom-control custom-switch">
                <input type="checkbox" class="custom-control-input" id="filterSwitch" {"checked" if filter_enabled else ""} 
                       onclick="toggleFilter(this.checked)">
                <label class="custom-control-label" for="filterSwitch">Show only watched vessels</label>
            </div>
        </div>
        <div class="form-group">
            <label for="searchInput">Search vessels:</label>
            <input type="text" class="form-control" id="searchInput" placeholder="Name or MMSI" value="{search_term_safe}">
            <div class="mt-2">
                <button class="btn btn-primary btn-sm" onclick="searchVessels(document.getElementById('searchInput').value)">
                    Search
                </button>
                <button class="btn btn-info btn-sm" onclick="findVessel(document.getElementById('searchInput').value)">
                    Find on map
                </button>
                <button class="btn btn-secondary btn-sm" onclick="clearSearch()">Clear</button>
            </div>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(control_panel))

    folium.LayerControl().add_to(m)

    timestamp = datetime.now().strftime("%Y-%m-d %H:%M:%S")
    title_html = f'''
        <div id="status" class="sonar">Capturing data...</div>
        <h3 align="center" style="font-size:2vw; margin: 0;"><b>European Vessel Tracking Map</b></h3>
        <h4 align="center" style="font-size:1vw; margin: 0;"><b><i>Made by <a href="https://github.com/m3m0rydmp" target="_blank">m3m0rydmp</a></i></b></h4>
        <div class="marquee">
            <b>{active_vessels}</b> active vessels shown | Total database: <b>{len(vessels_copy)}</b> vessels | Last updated: {timestamp}
        </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))

    watchlist_modal = """
    <div class="modal fade" id="watchlistModal" tabindex="-1" role="dialog" aria-labelledby="watchlistModalLabel" aria-hidden="true">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="watchlistModalLabel">Manage Watch List</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close" onclick="$('#watchlistModal').modal('hide')">
                    <span aria-hidden="true">×</span>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label for="mmsiInput">Add MMSI to watch list:</label>
                    <div class="input-group">
                        <input type="text" class="form-control" id="mmsiInput" placeholder="Enter MMSI">
                        <div class="input-group-append">
                            <button class="btn btn-primary" onclick="manuallyAddToWatchlist()">Add</button>
                        </div>
                    </div>
                </div>
                <hr>
                <div id="watchlistContainer">
                    <h6>Current Watch List:</h6>
                    <div id="currentWatchlist">Loading...</div>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" onclick="$('#watchlistModal').modal('hide')">Close</button>
            </div>
        </div>
    </div>
</div>
    """
    m.get_root().html.add_child(folium.Element(watchlist_modal))

    custom_css_js = f'''
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://use.fontawesome.com/releases/v5.6.1/css/all.css">
    <script src="https://code.jquery.com/jquery-3.5.1.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/popper.js@1.16.1/dist/umd/popper.min.js"></script>
    <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.min.js"></script>
    <style>
        body {{ background-color: #f0f8ff; }}
        .marquee {{ width: 100%; overflow: hidden; white-space: nowrap; box-sizing: border-box; animation: marquee 15s linear infinite; font-size: 1.5vw; }}
        @keyframes marquee {{ 0% {{ transform: translate(100%, 0); }} 100% {{ transform: translate(-100%, 0); }} }}
        .sonar {{ font-size: 1.5vw; color: green; animation: sonar 2s infinite; position: absolute; top: 10px; left: 10px; }}
        @keyframes sonar {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} 100% {{ opacity: 1; }} }}
        .offline {{ color: red; animation: none; }}
        #control-panel {{ box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
        .btn-primary {{ background-color: #3498db; }}
        .custom-control-input:checked ~ .custom-control-label::before {{ background-color: #3498db; border-color: #3498db; }}
        .watchlist-item {{ display: flex; justify-content: space-between; margin-bottom: 5px; padding: 5px; background-color: #f8f9fa; border-radius: 3px; }}
    </style>
    <script>
        const BASE_URL = 'http://localhost:{PORT}';
        function toggleFilter(isEnabled) {{
            console.log("Toggling filter to: " + isEnabled);
            fetch(`${{BASE_URL}}/toggle_filter?enabled=${{isEnabled}}`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    console.log('Filter toggle successful');
                    location.reload();
                }})
                .catch(error => {{
                    console.error('Error toggling filter:', error);
                    alert('Failed to toggle filter: ' + error.message + '. Ensure server is running on port {PORT}.');
                }});
        }}
        function searchVessels(term) {{
            console.log("Searching for: " + term);
            fetch(`${{BASE_URL}}/search?term=${{encodeURIComponent(term)}}`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    console.log('Search successful');
                    location.reload();
                }})
                .catch(error => {{
                    console.error('Error applying search:', error);
                    alert('Failed to search: ' + error.message + '. Ensure server is running on port {PORT}.');
                }});
        }}
        function clearSearch() {{
            console.log("Clearing search");
            document.getElementById('searchInput').value = '';
            fetch(`${{BASE_URL}}/search?term=`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    console.log('Clear search successful');
                    location.reload();
                }})
                .catch(error => {{
                    console.error('Error clearing search:', error);
                    alert('Failed to clear search: ' + error.message + '. Ensure server is running on port {PORT}.');
                }});
        }}
        function addToWatchlist(mmsi) {{
            console.log("Adding to watchlist: " + mmsi);
            fetch(`${{BASE_URL}}/add_to_watchlist?mmsi=${{mmsi}}`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    console.log('Added to watchlist');
                    alert('Vessel with MMSI ' + mmsi + ' added to watch list');
                    location.reload();
                }})
                .catch(error => console.error('Error adding to watchlist:', error));
        }}
        function manuallyAddToWatchlist() {{
            const mmsi = document.getElementById('mmsiInput').value.trim();
            if (mmsi) {{
                addToWatchlist(mmsi);
                document.getElementById('mmsiInput').value = '';
                loadWatchlist();
            }} else {{
                alert('Please enter a valid MMSI number');
            }}
        }}
        function loadWatchlist() {{
            fetch(`${{BASE_URL}}/get_watchlist`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    return response.json();
                }})
                .then(data => {{
                    const container = document.getElementById('currentWatchlist');
                    if (data.length === 0) {{
                        container.innerHTML = '<p>No vessels in watch list</p>';
                        return;
                    }}
                    let html = '';
                    data.forEach(mmsi => {{
                        html += `
                            <div class="watchlist-item">
                                <span>${{mmsi}}</span>
                                <button class="btn btn-sm btn-danger" onclick="removeFromWatchlist('${{mmsi}}')">
                                    <i class="fa fa-trash"></i>
                                </button>
                            </div>
                        `;
                    }});
                    container.innerHTML = html;
                }})
                .catch(error => {{
                    console.error('Error loading watchlist:', error);
                    document.getElementById('currentWatchlist').innerHTML = '<p class="text-danger">Error loading watch list</p>';
                }});
        }}
        function removeFromWatchlist(mmsi) {{
            fetch(`${{BASE_URL}}/remove_from_watchlist?mmsi=${{mmsi}}`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    console.log('Removed from watchlist');
                    loadWatchlist();
                    location.reload();
                }})
                .catch(error => console.error('Error removing from watchlist:', error));
        }}
        function findVessel(term) {{
            if (!term) {{
                alert('Please enter a vessel name or MMSI to find');
                return;
            }}
            console.log("Finding vessel: " + term);
            fetch(`${{BASE_URL}}/find_vessel?term=${{encodeURIComponent(term)}}`)
                .then(response => {{
                    if (!response.ok) throw new Error('Network error: ' + response.status + ' ' + response.statusText);
                    return response.json();
                }})
                .then(data => {{
                    console.log('Find vessel response:', data);
                    if (data.found) {{
                        const map = document.querySelector('div.folium-map').id;
                        const lat = data.lat;
                        const lon = data.lon;
                        const name = data.name || 'Unknown';
                        eval(`${{map}}.flyTo([${{lat}}, ${{lon}}], 12, {{duration: 1}})`);
                        const highlightMarker = L.circleMarker([lat, lon], {{
                            radius: 20, color: '#ff0000', fillColor: '#ff7700', fillOpacity: 0.3, weight: 2
                        }}).addTo(eval(map));
                        alert(`Found vessel: ${{name}} (MMSI: ${{data.mmsi}})`);
                        setTimeout(() => eval(`${{map}}.removeLayer(highlightMarker)`), 5000);
                    }} else {{
                        alert('Vessel not found in current view.');
                    }}
                }})
                .catch(error => {{
                    console.error('Error finding vessel:', error);
                    alert('Error finding vessel: ' + error.message + '. Ensure server is running on port {PORT}.');
                }});
        }}
    </script>
    '''
    m.get_root().html.add_child(folium.Element(custom_css_js))

    m.save(MAP_FILE)
    print(f"Map updated with {active_vessels} active vessels out of {len(vessels_copy)} total at {timestamp}")
    if filter_enabled:
        print(f"Filter active: Showing {filtered_vessels} watched vessels")
    if search_term:
        print(f"Search active: '{search_term}'")
    else:
        print("No search term active")

def map_updater():
    """Update the map at regular intervals"""
    first_update = True
    while running:
        try:
            create_map()
            save_vessel_data()
            if first_update and os.path.exists(MAP_FILE):
                webbrowser.open("file://" + os.path.realpath(MAP_FILE))
                first_update = False
        except Exception as e:
            print(f"Error updating map: {e}")

        with vessels_lock:
            current_time = time.time()
            vessels_to_remove = [mmsi for mmsi, v in vessels.items() 
                               if current_time - v.get("last_update", 0) > 7200]
            for mmsi in vessels_to_remove:
                vessels.pop(mmsi, None)
            if vessels_to_remove:
                print(f"Removed {len(vessels_to_remove)} inactive vessels from memory")

        time.sleep(MAP_UPDATE_INTERVAL)

def signal_handler(sig, frame):
    global running
    print("Termination requested. This will stop the program from capturing data. Confirm? (Y/N)")
    if input().strip().lower() == "y":
        print(r'''

██████  ██    ██ ███████     ██████  ██    ██ ███████          
██   ██  ██  ██  ██          ██   ██  ██  ██  ██               
██████    ████   █████       ██████    ████   █████            
██   ██    ██    ██          ██   ██    ██    ██               
██████     ██    ███████     ██████     ██    ███████ ██ ██ ██  
                                                                                                                     
PLEASE DON'T PRESS ANY BUTTON, IT WILL STILL UPDATE BUT IT WILL CLOSE AFTER
        ''')
        print("Shutting down...")
        running = False
        try:
            with open(MAP_FILE, 'r') as file:
                filedata = file.read()
            filedata = filedata.replace('Capturing data...', 'Tracker offline')
            filedata = filedata.replace('class="sonar"', 'class="offline"')
            with open(MAP_FILE, 'w') as file:
                file.write(filedata)
        except Exception as e:
            print(f"Error updating map file: {e}")
        time.sleep(5)
        sys.exit(0)
    else:
        print("Cancelled.")

def save_vessel_data():
    """Save current vessel data to a JSON file"""
    vessels_data = {}
    with vessels_lock:
        for mmsi, vessel in vessels.items():
            if "lat" in vessel and "lon" in vessel:
                vessels_data[mmsi] = {
                    "mmsi": mmsi,
                    "name": vessel.get("name", "Unknown"),
                    "lat": vessel.get("lat"),
                    "lon": vessel.get("lon"),
                    "ship_type": vessel.get("ship_type"),
                    "last_update": vessel.get("last_update")
                }
    try:
        with open("vessel_data.json", "w") as f:
            json.dump(vessels_data, f)
    except Exception as e:
        print(f"Error saving vessel data: {e}")

class FilterControlHandler(BaseHTTPRequestHandler):
    def _set_headers(self, content_type='text/html'):
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.end_headers()

    def do_GET(self):
        global filter_enabled, search_term
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        print(f"Received request: {self.path}")

        if path == '/toggle_filter':
            if 'enabled' in query:
                filter_enabled = query['enabled'][0].lower() == 'true'
                print(f"Filter set to: {filter_enabled}")
            self._set_headers()
            self.wfile.write(b"OK")
        elif path == '/search':
            search_term = query.get('term', [''])[0] if 'term' in query else ""
            print(f"Search term set to: '{search_term}'")
            self._set_headers()
            self.wfile.write(b"OK")
        elif path == '/add_to_watchlist':
            if 'mmsi' in query:
                save_mmsi_to_filter(query['mmsi'][0])
            self._set_headers()
            self.wfile.write(b"OK")
        elif path == '/get_watchlist':
            filtered_mmsi = list(load_filtered_mmsi())
            self._set_headers('application/json')
            self.wfile.write(json.dumps(filtered_mmsi).encode())
        elif path == '/remove_from_watchlist':
            if 'mmsi' in query:
                mmsi = query['mmsi'][0]
                filtered_mmsi = load_filtered_mmsi()
                if mmsi in filtered_mmsi:
                    filtered_mmsi.remove(mmsi)
                    try:
                        with open(MMSI_FILTER_FILE, 'w') as f:
                            for m in filtered_mmsi:
                                f.write(f"{m}\n")
                        print(f"Removed MMSI {mmsi} from watch list")
                    except Exception as e:
                        print(f"Error updating filter file: {e}")
            self._set_headers()
            self.wfile.write(b"OK")
        elif path == '/find_vessel':
            if 'term' in query:
                search_term = query['term'][0].lower()
                result = {"found": False}
                try:
                    if os.path.exists("vessel_data.json"):
                        with open("vessel_data.json", "r") as f:
                            vessel_data = json.load(f)
                        search_scope = load_filtered_mmsi() if filter_enabled else set(vessel_data.keys())
                        print(f"Searching for '{search_term}' in scope: {len(search_scope)} vessels")
                        for mmsi in search_scope:
                            if mmsi not in vessel_data:
                                continue
                            vessel = vessel_data[mmsi]
                            vessel_name = vessel.get("name", "").lower()
                            if search_term in vessel_name or search_term in mmsi.lower():
                                result = {
                                    "found": True,
                                    "mmsi": mmsi,
                                    "name": vessel.get("name"),
                                    "lat": vessel.get("lat"),
                                    "lon": vessel.get("lon")
                                }
                                print(f"Found vessel: {result}")
                                break
                    else:
                        print("vessel_data.json not found")
                except Exception as e:
                    print(f"Error finding vessel: {e}")
                self._set_headers('application/json')
                self.wfile.write(json.dumps(result).encode())
        elif path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b"This is the AIS Vessel Tracking server. Use vessel_map.html to interact with the map.")
        else:
            self.send_response(404)
            self._set_headers()
            self.wfile.write(b"Invalid request")

    def log_message(self, format, *args):
        return  # Silence default logging, we use print instead

def run_http_server(port=PORT):
    """Run HTTP server for filter control"""
    try:
        server_address = ('0.0.0.0', port)
        httpd = HTTPServer(server_address, FilterControlHandler)
        print(f"Starting control server on port {port}")
        httpd.serve_forever()
    except OSError as e:
        print(f"Error starting server on port {port}: {e}")
        print(f"Filter controls will not work. Free up port {port} or change PORT variable.")

async def main():
    print(r"""
 ________    ________  ______   ______   _________  ______    ______   ________   ___ __ __     
/_______/\  /_______/\/_____/\ /_____/\ /________/\/_____/\  /_____/\ /_______/\ /__//_//_/\    
\::: _  \ \ \__.::._\/\::::_\/_\::::_\/_\__.::.__\/\:::_ \ \ \::::_\/_\::: _  \ \\::\| \| \ \   
 \::(_)  \ \   \::\ \  \:\/___/\\:\/___/\  \::\ \   \:(_) ) )_\:\/___/\\::(_)  \ \\:.      \ \  
  \:: __  \ \  _\::\ \__\_::._\:\\_::._\:\  \::\ \   \: __ `\ \\::___\/_\:: __  \ \\:.\-/\  \ \ 
   \:.\ \  \ \/__\::\__/\ /____\:\ /____\:\  \::\ \   \ \ `\ \ \\:\____/\\:.\ \  \ \\. \  \  \ \
    \__\/\__\/\________\/ \_____\/ \_____\/   \__\/    \_\/ \_\/ \_____\/ \__\/\__\/ \__\/ \__\/
    """)
    time.sleep(2)

    print("Starting AIS Vessel Tracking for European Waters...")
    print("Press Ctrl+C to terminate the program")
    time.sleep(2)

    server_thread = threading.Thread(target=run_http_server, args=(PORT,))
    server_thread.daemon = True
    server_thread.start()

    updater_thread = threading.Thread(target=map_updater)
    updater_thread.daemon = True
    updater_thread.start()

    await connect_to_ais_stream()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    asyncio.run(main())