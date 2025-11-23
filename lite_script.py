import os
import json
import random
import time
from datetime import datetime, timezone, timedelta
import requests
from spotipy import Spotify
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# Selenium for scraping
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ==== LITE SCRIPT CONFIG ====
# This is a simplified version without database operations, SMS, and whitelist functionality
ARTISTS_FILE = "artists.json"
OUTPUT_FILE = "rolled_tracks.json"

LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")
LASTFM_USERNAME = os.environ.get("LASTFM_USERNAME")

scope = "playlist-modify-public playlist-modify-private user-library-read"

# ==== GLOBAL DRIVER FOR SCRAPING ====
global_driver = None
def get_global_driver():
    global global_driver
    if global_driver is None:
        chrome_bin = os.environ.get("CHROME_BIN")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

        options = webdriver.ChromeOptions()
        if chrome_bin:
            options.binary_location = chrome_bin
        # fallback to legacy headless flag if new not supported
        try:
            options.add_argument("--headless=new")
        except Exception:
            options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # try to use chromedriver-binary if no explicit path provided
        if not chromedriver_path:
            try:
                import chromedriver_binary  # installs and exposes binary_path
                chromedriver_path = getattr(chromedriver_binary, "binary_path", None)
            except Exception:
                chromedriver_path = None

        if not chromedriver_path:
            raise RuntimeError("CHROMEDRIVER_PATH (or chromedriver-binary) is required to start the Chrome driver")

        service = Service(chromedriver_path)
        global_driver = webdriver.Chrome(service=service, options=options)
    return global_driver

def close_global_driver():
    global global_driver
    if global_driver:
        global_driver.quit()
        global_driver = None

# ==== HELPER FUNCTIONS ====
def safe_spotify_call(func, *args, **kwargs):
    """Spotify call wrapper with retries, 404 skip, and None fallback."""
    retries = 3
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status == 404:
                print(f"[404] {getattr(func,'__name__',str(func))} returned 404 - skipping")
                return None
            elif e.http_status == 429:
                wait_time = 2 ** attempt
                print(f"[429] Rate limited. Waiting {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"[ERROR] {getattr(func,'__name__',str(func))}: {e}")
                if attempt == retries - 1:
                    break
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"[ERROR] {getattr(func,'__name__',str(func))}: {e}")
            if attempt == retries - 1:
                break
            time.sleep(2 ** attempt)
    print(f"[FAIL] {getattr(func,'__name__',str(func))} failed after {retries} retries")
    return None

def get_random_track_from_playlist(sp, playlist_id, excluded_artist=None, max_followers=None, source_desc="", artists_data=None, existing_artist_ids=None, liked_songs_artist_ids=None):
    consecutive_invalid = 0
    for attempt in range(1, 21):
        try:
            # Get random track from playlist
            playlist_data = safe_spotify_call(sp.playlist, playlist_id)
            if not playlist_data or playlist_data.get("tracks", {}).get("total", 0) == 0:
                print(f"[SKIP] Empty or invalid playlist {source_desc}")
                return None

            total_tracks = playlist_data["tracks"]["total"]
            random_offset = random.randint(0, max(0, total_tracks - 1))
            
            track_data = safe_spotify_call(sp.playlist_items, playlist_id, offset=random_offset, limit=1)
            if not track_data or not track_data.get("items"):
                consecutive_invalid += 1
                if consecutive_invalid >= 5:
                    print(f"[SKIP] Too many consecutive invalid tracks in {source_desc}")
                    return None
                continue

            track_item = track_data["items"][0]
            track = track_item.get("track")
            
            if not track or track.get("type") != "track":
                consecutive_invalid += 1
                continue

            # Validate track (simplified validation without DB checks)
            if validate_track_lite(track, artists_data, existing_artist_ids, max_followers, sp, liked_songs_artist_ids):
                print(f"[SUCCESS] Found valid track from {source_desc}: {track['name']} by {track['artists'][0]['name']}")
                return track
            else:
                consecutive_invalid += 1

        except Exception as e:
            print(f"[ERROR] Error getting track from {source_desc}: {e}")
            consecutive_invalid += 1

        if consecutive_invalid >= 10:
            print(f"[SKIP] Too many consecutive failures for {source_desc}")
            break

    return None

def validate_track_lite(track, artists_data, existing_artist_ids=None, max_followers=None, sp=None, liked_songs_artist_ids=None):
    """
    Simplified validation without database checks
    Now includes check against user's liked songs artists
    """
    if not track or "artists" not in track or not track["artists"]:
        return False

    artist = track["artists"][0]
    aid = artist.get("id")
    name_lower = (artist.get("name") or "").lower()

    # 1. Check if artist appears in user's liked songs (NEW CHECK)
    if liked_songs_artist_ids and aid in liked_songs_artist_ids:
        print(f"[SKIP] Artist '{artist.get('name')}' appears in liked songs - skipping")
        return False

    # 2. Check against local artists.json if provided
    if artists_data:
        artist_entry = artists_data.get(aid)
        if artist_entry and int(artist_entry.get("total_liked", 0)) >= 3:
            return False

    # 3. Already in playlist
    if existing_artist_ids and (aid in existing_artist_ids):
        return False

    # 4. Max followers check
    if max_followers and sp:
        try:
            artist_data = safe_spotify_call(sp.artist, aid)
            if artist_data and artist_data.get("followers", {}).get("total", 0) > max_followers:
                return False
        except:
            pass

    return True

def scrape_artist_playlists(artist_id_or_url):
    driver = get_global_driver()
    playlists = []
    try:
        # Extract artist ID from URL if needed
        if "open.spotify.com" in artist_id_or_url:
            artist_id = artist_id_or_url.split("/")[-1].split("?")[0]
        else:
            artist_id = artist_id_or_url

        url = f"https://open.spotify.com/artist/{artist_id}"
        driver.get(url)
        time.sleep(3)

        # Find playlists featuring this artist
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            # Look for playlist elements
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            playlist_links = soup.find_all('a', href=True)
            
            for link in playlist_links:
                href = link.get('href', '')
                if '/playlist/' in href and href not in [p['url'] for p in playlists]:
                    playlist_id = href.split('/playlist/')[-1].split('?')[0]
                    playlists.append({
                        'id': playlist_id,
                        'url': href
                    })
        except Exception as e:
            print(f"[WARNING] Error scraping playlists for artist {artist_id}: {e}")

    except Exception as e:
        print(f"[ERROR] Error scraping artist playlists: {e}")
    
    return playlists

def select_track_for_artist_lite(sp, artist_name, artists_data, existing_artist_ids, liked_songs_artist_ids=None):
    """
    Simplified track selection without database operations
    """
    track = None
    
    # Get artist info
    search_res = safe_spotify_call(sp.search, artist_name, type="artist", limit=1)
    if not search_res or "artists" not in search_res or not search_res["artists"].get("items"):
        print(f"[SKIP] No search results for artist: {artist_name}")
        return None
    
    artist_results = search_res["artists"]["items"]
    artist_id = artist_results[0]["id"]

    # Step 1: Try artist playlists
    print(f"[INFO] Looking for tracks by '{artist_name}' in artist playlists...")
    scraped_playlists = scrape_artist_playlists(artist_id)
    
    for pl in scraped_playlists[:5]:  # Limit to first 5 playlists
        try:
            track = get_random_track_from_playlist(
                sp, pl['id'], 
                excluded_artist=None,
                max_followers=None,
                source_desc=f"artist playlist {pl['id'][:10]}...",
                artists_data=artists_data,
                existing_artist_ids=existing_artist_ids,
                liked_songs_artist_ids=liked_songs_artist_ids
            )
            if track:
                return track
        except Exception as e:
            print(f"[ERROR] Error checking playlist {pl['id']}: {e}")
            continue

    # Step 2: User playlists via API
    print(f"[INFO] No valid tracks found in artist playlists for '{artist_name}'. Trying user playlists...")
    
    candidate_playlists = []
    search_limit = 50
    max_search_pages = 2  # Reduced for lite version
    
    for page in range(max_search_pages):
        offset = page * search_limit
        try:
            search_result = safe_spotify_call(
                sp.search, 
                f'"{artist_name}"', 
                type="playlist", 
                limit=search_limit, 
                offset=offset
            )
            if not search_result or "playlists" not in search_result:
                break
                
            playlists_data = search_result["playlists"]
            items = playlists_data.get("items", [])
            if not items:
                break
                
            for playlist_item in items:
                pid = playlist_item.get("id")
                if pid:
                    candidate_playlists.append(pid)
                    
        except Exception as e:
            print(f"[ERROR] Error searching playlists: {e}")
            break
    
    if candidate_playlists:
        random.shuffle(candidate_playlists)
        for pid in candidate_playlists[:10]:  # Limit for lite version
            try:
                track = get_random_track_from_playlist(
                    sp, pid,
                    excluded_artist=None,
                    max_followers=None, 
                    source_desc=f"user playlist {pid[:10]}...",
                    artists_data=artists_data,
                    existing_artist_ids=existing_artist_ids,
                    liked_songs_artist_ids=liked_songs_artist_ids
                )
                if track:
                    return track
            except Exception as e:
                continue

    # Step 3: Last.fm similar artists (simplified)
    print(f"[INFO] Trying Last.fm similar artists for '{artist_name}'...")
    if LASTFM_API_KEY:
        try:
            url = "http://ws.audioscrobbler.com/2.0/"
            params = {
                "method": "artist.getsimilar", 
                "artist": artist_name, 
                "api_key": LASTFM_API_KEY, 
                "format": "json", 
                "limit": 5
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "similarartists" in data and "artist" in data["similarartists"]:
                similar_artists = data["similarartists"]["artist"]
                random.shuffle(similar_artists)
                
                for sim_artist in similar_artists[:3]:  # Reduced for lite version
                    sim_name = sim_artist.get("name", "").strip()
                    if sim_name:
                        track = select_track_for_artist_lite(sp, sim_name, artists_data, existing_artist_ids, liked_songs_artist_ids)
                        if track:
                            return track
        except Exception as e:
            print(f"[ERROR] Last.fm lookup failed: {e}")

    return None

def fetch_all_recent_tracks(username=None, api_key=None):
    """Simplified recent tracks fetching"""
    if not username or not api_key:
        return []
    
    recent_tracks = []
    page = 1
    
    try:
        while page <= 5:  # Limit pages for lite version
            url = "http://ws.audioscrobbler.com/2.0/"
            params = {
                "method": "user.getrecenttracks",
                "user": username,
                "api_key": api_key,
                "format": "json",
                "page": page,
                "limit": 200
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "recenttracks" not in data or "track" not in data["recenttracks"]:
                break
                
            tracks = data["recenttracks"]["track"]
            if not tracks:
                break
                
            recent_tracks.extend(tracks)
            page += 1
            
    except Exception as e:
        print(f"[ERROR] Error fetching recent tracks: {e}")
    
    return recent_tracks

def build_artist_play_map(recent_tracks, days_limit=365):
    """Build simplified play map"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_limit)
    artist_play_map = {}
    
    for t in recent_tracks:
        try:
            if isinstance(t, dict) and "artist" in t and "#text" in t["artist"]:
                artist_name = t["artist"]["#text"].lower()
                
                # Simple date parsing
                date_str = t.get("date", {}).get("#text", "")
                if date_str:
                    # This is a simplified date parsing - in production you'd want more robust parsing
                    artist_play_map[artist_name] = artist_play_map.get(artist_name, 0) + 1
        except:
            continue
    
    return artist_play_map

def calculate_weights_lite(all_artists, artist_play_map):
    """Simplified weight calculation"""
    weights = {}
    
    for aid, info in all_artists.items():
        artist_name = info.get("name", "").lower()
        total_liked = int(info.get("total_liked", 0))
        
        # Simple scoring - less liked artists get higher weights
        if total_liked == 0:
            base_weight = 10
        elif total_liked == 1:
            base_weight = 5
        elif total_liked == 2:
            base_weight = 2
        else:
            base_weight = 1
        
        # Boost if in Last.fm history
        if artist_name in artist_play_map:
            base_weight *= 1.5
        
        weights[aid] = base_weight
    
    return weights

def load_artists_lite():
    """Load artists from local JSON file"""
    if os.path.exists(ARTISTS_FILE):
        try:
            with open(ARTISTS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_artists_lite(artists_data):
    """Save artists to local JSON file"""
    try:
        with open(ARTISTS_FILE, 'w') as f:
            json.dump(artists_data, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Could not save artists file: {e}")

def remove_old_tracks_from_playlist(sp, playlist_id, days_old=7):
    """Remove old tracks from playlist"""
    print(f"[INFO] Removing tracks older than {days_old} days from playlist...")
    
    try:
        now = datetime.now(timezone.utc)
        uris_to_remove = []
        
        # Get all tracks from playlist
        offset = 0
        limit = 100
        
        while True:
            tracks_data = safe_spotify_call(sp.playlist_items, playlist_id, offset=offset, limit=limit)
            if not tracks_data or not tracks_data.get("items"):
                break
            
            for item in tracks_data["items"]:
                added_at_str = item.get("added_at")
                track = item.get("track")
                
                if added_at_str and track and track.get("uri"):
                    try:
                        added_at = datetime.fromisoformat(added_at_str.replace("Z", "+00:00"))
                        age = now - added_at
                        
                        if age.days >= days_old:
                            uris_to_remove.append(track["uri"])
                    except:
                        continue
            
            if len(tracks_data["items"]) < limit:
                break
            offset += limit
        
        # Remove old tracks in batches
        if uris_to_remove:
            batch_size = 50
            removed_count = 0
            
            for i in range(0, len(uris_to_remove), batch_size):
                batch = uris_to_remove[i:i + batch_size]
                result = safe_spotify_call(sp.playlist_remove_all_occurrences_of_items, playlist_id, batch)
                if result:
                    removed_count += len(batch)
            
            print(f"[INFO] Removed {removed_count} old tracks from playlist")
            return removed_count
        else:
            print("[INFO] No old tracks found to remove")
            return 0
            
    except Exception as e:
        print(f"[ERROR] Error removing old tracks: {e}")
        return 0

def build_existing_artist_ids(tracks):
    """Build set of existing artist IDs in playlist"""
    ids = set()
    for t in tracks:
        if t and "track" in t and t["track"] and "artists" in t["track"]:
            for artist in t["track"]["artists"]:
                if artist.get("id"):
                    ids.add(artist["id"])
    return ids

def fetch_liked_songs_artist_ids(sp):
    """
    Fetch all artist IDs from user's liked songs
    Returns a set of artist IDs to exclude from recommendations
    """
    print("[INFO] Fetching user's liked songs to build exclusion list...")
    liked_artist_ids = set()
    
    try:
        offset = 0
        limit = 50
        
        while True:
            results = safe_spotify_call(sp.current_user_saved_tracks, limit=limit, offset=offset)
            if not results or not results.get("items"):
                break
            
            for item in results["items"]:
                track = item.get("track")
                if track and "artists" in track:
                    for artist in track["artists"]:
                        artist_id = artist.get("id")
                        if artist_id:
                            liked_artist_ids.add(artist_id)
            
            # Check if we've reached the end
            if len(results["items"]) < limit:
                break
            
            offset += limit
            
            # Add a small delay to avoid rate limiting
            if offset % 500 == 0:
                print(f"[INFO] Fetched {offset} liked songs so far...")
                time.sleep(0.5)
        
        print(f"[INFO] Found {len(liked_artist_ids)} unique artists in {offset} liked songs")
        return liked_artist_ids
        
    except Exception as e:
        print(f"[ERROR] Error fetching liked songs: {e}")
        return set()  # Return empty set on error, don't fail the entire process

def run_lite_script(sp, output_playlist_id, max_songs=10, lastfm_username=None):
    """
    Main lite script function that can be called from the web app
    """
    try:
        print(f"[INFO] Starting lite script run for playlist {output_playlist_id}")
        
        # Load existing artists data
        artists_data = load_artists_lite()
        print(f"[INFO] Loaded {len(artists_data)} artists from cache")
        
        # Fetch user's liked songs to build exclusion list
        liked_songs_artist_ids = fetch_liked_songs_artist_ids(sp)
        
        # Get Last.fm data if available
        recent_tracks = []
        artist_play_map = {}
        if lastfm_username and LASTFM_API_KEY:
            print("[INFO] Fetching Last.fm recent tracks...")
            recent_tracks = fetch_all_recent_tracks(lastfm_username, LASTFM_API_KEY)
            artist_play_map = build_artist_play_map(recent_tracks)
            print(f"[INFO] Found {len(recent_tracks)} recent tracks, {len(artist_play_map)} unique artists")
        
        # Remove old tracks from playlist
        removed_count = remove_old_tracks_from_playlist(sp, output_playlist_id, days_old=7)
        
        # Get current playlist tracks to avoid duplicates
        playlist_items = []
        offset = 0
        while True:
            items = safe_spotify_call(sp.playlist_items, output_playlist_id, offset=offset, limit=100)
            if not items or not items.get("items"):
                break
            playlist_items.extend(items["items"])
            if len(items["items"]) < 100:
                break
            offset += 100
        
        existing_artist_ids = build_existing_artist_ids(playlist_items)
        print(f"[INFO] Found {len(existing_artist_ids)} existing artists in playlist")
        
        # Calculate weights for artist selection
        if artists_data:
            weights = calculate_weights_lite(artists_data, artist_play_map)
            
            # Select artists and find tracks
            selected_tracks = []
            attempts = 0
            max_attempts = max_songs * 5  # Allow more attempts than target
            
            while len(selected_tracks) < max_songs and attempts < max_attempts:
                attempts += 1
                
                # Weighted random selection
                if weights:
                    artist_ids = list(weights.keys())
                    weight_values = list(weights.values())
                    
                    try:
                        selected_aid = random.choices(artist_ids, weights=weight_values, k=1)[0]
                        artist_info = artists_data[selected_aid]
                        artist_name = artist_info.get("name", "")
                        
                        print(f"[INFO] Attempt {attempts}: Searching for tracks by '{artist_name}'")
                        
                        track = select_track_for_artist_lite(sp, artist_name, artists_data, existing_artist_ids, liked_songs_artist_ids)
                        if track:
                            selected_tracks.append(track)
                            # Add artist to existing set to avoid duplicates
                            for artist in track["artists"]:
                                if artist.get("id"):
                                    existing_artist_ids.add(artist["id"])
                            print(f"[SUCCESS] Added track {len(selected_tracks)}/{max_songs}: {track['name']} by {artist_name}")
                        else:
                            # Reduce weight for this artist if no track found
                            weights[selected_aid] *= 0.5
                            
                    except Exception as e:
                        print(f"[ERROR] Error selecting track: {e}")
                        continue
                else:
                    print("[WARNING] No artists available for selection")
                    break
            
            # Add selected tracks to playlist
            if selected_tracks:
                track_uris = [track["uri"] for track in selected_tracks]
                try:
                    result = safe_spotify_call(sp.playlist_add_items, output_playlist_id, track_uris)
                    if result:
                        print(f"[SUCCESS] Added {len(selected_tracks)} new tracks to playlist")
                    else:
                        print("[ERROR] Failed to add tracks to playlist")
                except Exception as e:
                    print(f"[ERROR] Error adding tracks to playlist: {e}")
            else:
                print("[WARNING] No tracks were selected")
        
        # Close selenium driver
        close_global_driver()
        
        result = {
            "success": True,
            "tracks_added": len(selected_tracks) if 'selected_tracks' in locals() else 0,
            "tracks_removed": removed_count,
            "playlist_id": output_playlist_id
        }
        
        print(f"[INFO] Lite script completed successfully: {result}")
        return result
        
    except Exception as e:
        print(f"[FATAL ERROR] Lite script failed: {e}")
        close_global_driver()
        return {
            "success": False,
            "error": str(e),
            "tracks_added": 0,
            "tracks_removed": 0
        }

# For testing purposes
if __name__ == "__main__":
    # This would be used for standalone testing
    print("Lite script loaded successfully")