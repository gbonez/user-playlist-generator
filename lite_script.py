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

# ==== LITE SCRIPT CONFIG ====
# This is a real-time version without any caching or data storage
# Each run scans liked songs fresh and generates recommendations

LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")

scope = "playlist-modify-public playlist-modify-private user-library-read user-read-recently-played user-top-read"

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

def validate_track_lite(track, existing_artist_ids=None, liked_songs_artist_ids=None, max_follower_count=None):
    """
    Real-time validation based on current liked songs and playlist state
    No caching or stored data used
    
    Args:
        max_follower_count: Maximum artist follower count (None = no limit)
    """
    if not track or "artists" not in track or not track["artists"]:
        return False

    artist = track["artists"][0]
    aid = artist.get("id")
    artist_name = artist.get("name", "Unknown")

    # 1. Check follower count if limit is set
    if max_follower_count is not None:
        follower_count = artist.get("followers", {}).get("total", 0)
        if follower_count > max_follower_count:
            print(f"[SKIP] Artist '{artist_name}' has {follower_count:,} followers (limit: {max_follower_count:,})")
            return False

    # 2. Check if artist appears in user's liked songs
    if liked_songs_artist_ids and aid in liked_songs_artist_ids:
        print(f"[SKIP] Artist '{artist_name}' appears in liked songs - skipping")
        return False

    # 3. Already in target playlist
    if existing_artist_ids and (aid in existing_artist_ids):
        return False

    return True

def get_similar_tracks_by_audio_features(sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None, limit=10):
    """
    Find similar tracks using Spotify's audio features and recommendations API
    
    Args:
        sp: Spotify client
        seed_track_id: The track ID to use as seed for similarity matching
        existing_artist_ids: Set of artist IDs already in the playlist
        liked_songs_artist_ids: Set of artist IDs from user's liked songs (to exclude)
        max_follower_count: Maximum artist follower count (None = no limit)
        limit: Maximum number of similar tracks to find
    
    Returns:
        List of valid track objects (up to limit)
    """
    try:
        print(f"[INFO] Getting audio features for seed track {seed_track_id[:10]}...")
        
        # Get audio features for the seed track
        features = safe_spotify_call(sp.audio_features, seed_track_id)
        if not features or not features[0]:
            print("[SKIP] Could not get audio features for seed track")
            return []
        
        track_features = features[0]
        
        # Build recommendations using sonic profile
        print(f"[INFO] Finding similar tracks (energy={track_features['energy']:.2f}, danceability={track_features['danceability']:.2f}, valence={track_features['valence']:.2f})...")
        
        recs = safe_spotify_call(
            sp.recommendations,
            seed_tracks=[seed_track_id],
            limit=50,  # Get more to filter through
            target_energy=track_features["energy"],
            target_danceability=track_features["danceability"],
            target_valence=track_features["valence"],
            target_tempo=track_features["tempo"],
            target_acousticness=track_features["acousticness"],
            target_instrumentalness=track_features["instrumentalness"]
        )
        
        if not recs or "tracks" not in recs:
            print("[SKIP] No recommendations returned")
            return []
        
        # Validate and collect tracks
        valid_tracks = []
        for track in recs["tracks"]:
            if len(valid_tracks) >= limit:
                break
                
            if validate_track_lite(track, existing_artist_ids, liked_songs_artist_ids, max_follower_count):
                valid_tracks.append(track)
                print(f"[SUCCESS] Found similar track: {track['name']} by {track['artists'][0]['name']}")
                # Add artist to existing set to avoid duplicates in this batch
                for artist in track["artists"]:
                    if artist.get("id"):
                        existing_artist_ids.add(artist["id"])
        
        print(f"[INFO] Found {len(valid_tracks)} valid similar tracks")
        return valid_tracks
        
    except Exception as e:
        print(f"[ERROR] Error finding similar tracks by audio features: {e}")
        return []

def get_random_liked_track_for_artist(sp, artist_id):
    """
    Get a random liked song from a specific artist
    
    Args:
        sp: Spotify client
        artist_id: The artist ID to find tracks for
    
    Returns:
        Track ID of a random liked song by this artist, or None
    """
    try:
        # Scan through liked songs to find tracks by this artist
        artist_tracks = []
        offset = 0
        limit = 50
        
        while True:
            items = safe_spotify_call(sp.current_user_saved_tracks, limit=limit, offset=offset)
            if not items or not items.get("items"):
                break
            
            for item in items["items"]:
                track = item.get("track")
                if not track:
                    continue
                
                # Check if any artist matches
                for artist in track.get("artists", []):
                    if artist.get("id") == artist_id:
                        artist_tracks.append(track["id"])
                        break
            
            # If we found some tracks, we can stop early (for performance)
            if len(artist_tracks) >= 5:
                break
            
            if len(items["items"]) < limit:
                break
            
            offset += limit
        
        if artist_tracks:
            return random.choice(artist_tracks)
        
        return None
        
    except Exception as e:
        print(f"[ERROR] Error finding liked tracks for artist: {e}")
        return None

def select_track_for_artist_lite(sp, artist_name, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None):
    """
    Real-time track selection using audio features for similarity matching
    
    Strategy:
    1. Get a random liked song from the artist
    2. Use that song's audio features to find 10 similar tracks
    3. If that fails, try Last.fm similar artists as fallback
    
    Args:
        max_follower_count: Maximum artist follower count for recommendations (None = no limit)
    """
    
    # Get artist info
    search_res = safe_spotify_call(sp.search, artist_name, type="artist", limit=1)
    if not search_res or "artists" not in search_res or not search_res["artists"].get("items"):
        print(f"[SKIP] No search results for artist: {artist_name}")
        return None
    
    artist_results = search_res["artists"]["items"]
    artist_id = artist_results[0]["id"]

    # Step 1: Try audio feature matching with a liked song from this artist
    print(f"[INFO] Looking for recommendations similar to '{artist_name}' using audio features...")
    seed_track_id = get_random_liked_track_for_artist(sp, artist_id)
    
    if seed_track_id:
        print(f"[INFO] Using liked track {seed_track_id[:10]}... as seed for audio feature matching")
        similar_tracks = get_similar_tracks_by_audio_features(
            sp, 
            seed_track_id, 
            existing_artist_ids, 
            liked_songs_artist_ids, 
            max_follower_count,
            limit=10
        )
        
        if similar_tracks:
            # Return the first valid track from the batch
            return similar_tracks[0]
    else:
        print(f"[INFO] No liked tracks found for '{artist_name}'")

    # Step 2: Last.fm similar artists as fallback (only if no audio feature matches found)
    print(f"[INFO] No audio feature matches found. Trying Last.fm similar artists for '{artist_name}'...")
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
                        track = select_track_for_artist_lite(sp, sim_name, existing_artist_ids, liked_songs_artist_ids, max_follower_count)
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

def fetch_spotify_listening_data(sp):
    """
    Fetch user listening data from Spotify (recently played + top tracks)
    Combines recency and frequency to build artist play map
    
    Returns:
        dict: {artist_name_lower: play_count} with higher weights for recent + frequent plays
    """
    artist_play_map = {}
    
    try:
        # 1. Get recently played tracks (last 50)
        print("[INFO] Fetching recently played tracks from Spotify...")
        recently_played = safe_spotify_call(sp.current_user_recently_played, limit=50)
        
        if recently_played and "items" in recently_played:
            for item in recently_played["items"]:
                track = item.get("track")
                if not track:
                    continue
                
                for artist in track.get("artists", []):
                    artist_name = artist.get("name", "").lower()
                    if artist_name:
                        # Weight recent plays higher (3x)
                        artist_play_map[artist_name] = artist_play_map.get(artist_name, 0) + 3
            
            print(f"[INFO] Found {len(recently_played['items'])} recently played tracks")
        
        # 2. Get top tracks - short term (last 4 weeks)
        print("[INFO] Fetching top tracks (short term) from Spotify...")
        top_tracks_short = safe_spotify_call(sp.current_user_top_tracks, limit=50, time_range="short_term")
        
        if top_tracks_short and "items" in top_tracks_short:
            for track in top_tracks_short["items"]:
                for artist in track.get("artists", []):
                    artist_name = artist.get("name", "").lower()
                    if artist_name:
                        # Weight short-term tops high (2x)
                        artist_play_map[artist_name] = artist_play_map.get(artist_name, 0) + 2
            
            print(f"[INFO] Found {len(top_tracks_short['items'])} short-term top tracks")
        
        # 3. Get top tracks - medium term (last 6 months)
        print("[INFO] Fetching top tracks (medium term) from Spotify...")
        top_tracks_medium = safe_spotify_call(sp.current_user_top_tracks, limit=50, time_range="medium_term")
        
        if top_tracks_medium and "items" in top_tracks_medium:
            for track in top_tracks_medium["items"]:
                for artist in track.get("artists", []):
                    artist_name = artist.get("name", "").lower()
                    if artist_name:
                        # Weight medium-term tops moderately (1x)
                        artist_play_map[artist_name] = artist_play_map.get(artist_name, 0) + 1
            
            print(f"[INFO] Found {len(top_tracks_medium['items'])} medium-term top tracks")
        
        print(f"[INFO] Built Spotify listening data for {len(artist_play_map)} unique artists")
        return artist_play_map
        
    except Exception as e:
        print(f"[ERROR] Error fetching Spotify listening data: {e}")
        return {}

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

def build_artist_list_from_liked_songs(sp, artist_play_map=None):
    """
    Build fresh artist list from user's current liked songs
    Returns dict of {artist_id: {name, total_liked, weight}}
    """
    print("[INFO] Building artist list from liked songs...")
    artist_counts = {}
    
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
                        artist_name = artist.get("name")
                        
                        if artist_id and artist_name:
                            if artist_id not in artist_counts:
                                artist_counts[artist_id] = {
                                    "name": artist_name,
                                    "total_liked": 0
                                }
                            artist_counts[artist_id]["total_liked"] += 1
            
            if len(results["items"]) < limit:
                break
            
            offset += limit
            
            if offset % 500 == 0:
                print(f"[INFO] Processed {offset} liked songs...")
                time.sleep(0.5)
        
        print(f"[INFO] Found {len(artist_counts)} unique artists in liked songs")
        
        # Calculate weights based on how many songs user has liked
        for artist_id, info in artist_counts.items():
            total_liked = info["total_liked"]
            artist_name_lower = info["name"].lower()
            
            # Weight formula: fewer liked songs = higher weight (more likely to discover new tracks)
            if total_liked == 1:
                base_weight = 10
            elif total_liked == 2:
                base_weight = 5
            elif total_liked == 3:
                base_weight = 2
            else:
                base_weight = 1
            
            # Boost if in Last.fm history
            if artist_play_map and artist_name_lower in artist_play_map:
                base_weight *= 1.5
            
            info["weight"] = base_weight
        
        return artist_counts
        
    except Exception as e:
        print(f"[ERROR] Error building artist list: {e}")
        return {}

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

def run_lite_script(sp, output_playlist_id, max_songs=10, lastfm_username=None, max_follower_count=None):
    """
    Main lite script function - runs fresh each time with no caching
    Scans liked songs in real-time and generates recommendations
    
    Args:
        max_follower_count: Maximum artist follower count (None = no limit)
                           Popular: None
                           Somewhat Popular: 100000
                           Balanced: 75000  
                           Niche: 50000
                           Very Niche: 25000
    """
    try:
        follower_desc = f"max {max_follower_count:,} followers" if max_follower_count else "no follower limit"
        print(f"[INFO] Starting fresh recommendation run for playlist {output_playlist_id} ({follower_desc})")
        
        # Get listening data for lottery weights
        artist_play_map = {}
        if lastfm_username and LASTFM_API_KEY:
            # Use Last.fm data if username provided
            print("[INFO] Fetching Last.fm recent tracks...")
            recent_tracks = fetch_all_recent_tracks(lastfm_username, LASTFM_API_KEY)
            artist_play_map = build_artist_play_map(recent_tracks)
            print(f"[INFO] Found {len(recent_tracks)} recent tracks, {len(artist_play_map)} unique artists from Last.fm")
        else:
            # Otherwise use Spotify listening data
            print("[INFO] No Last.fm username provided. Using Spotify listening data...")
            artist_play_map = fetch_spotify_listening_data(sp)
        
        # Build fresh artist list from current liked songs
        print("[INFO] Scanning liked songs to build artist list...")
        artists_data = build_artist_list_from_liked_songs(sp, artist_play_map)
        
        if not artists_data:
            print("[ERROR] No artists found in liked songs!")
            return {
                "success": False,
                "error": "No artists found in your liked songs. Please add some liked songs first.",
                "tracks_added": 0,
                "tracks_removed": 0
            }
        
        # Fetch liked songs artist IDs for exclusion (same data, different format for efficient lookup)
        liked_songs_artist_ids = set(artists_data.keys())
        print(f"[INFO] Will exclude {len(liked_songs_artist_ids)} artists from liked songs")
        
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
        print(f"[INFO] Found {len(existing_artist_ids)} existing artists in target playlist")
        
        # Select artists and find tracks using weighted lottery
        selected_tracks = []
        attempts = 0
        max_attempts = max_songs * 5  # Allow more attempts than target
        
        # Build weight lists for weighted selection
        artist_ids = list(artists_data.keys())
        artist_weights = [artists_data[aid]["weight"] for aid in artist_ids]
        
        while len(selected_tracks) < max_songs and attempts < max_attempts:
            attempts += 1
            
            try:
                # Weighted random selection from liked songs artists
                selected_aid = random.choices(artist_ids, weights=artist_weights, k=1)[0]
                artist_info = artists_data[selected_aid]
                artist_name = artist_info.get("name", "")
                
                print(f"[INFO] Attempt {attempts}: Searching for similar tracks to '{artist_name}' (liked {artist_info['total_liked']} songs)")
                
                # Find tracks by similar artists (NOT by the selected artist themselves)
                track = select_track_for_artist_lite(sp, artist_name, existing_artist_ids, liked_songs_artist_ids, max_follower_count)
                
                if track:
                    selected_tracks.append(track)
                    # Add artist to existing set to avoid duplicates
                    for artist in track["artists"]:
                        if artist.get("id"):
                            existing_artist_ids.add(artist["id"])
                    print(f"[SUCCESS] Found track {len(selected_tracks)}/{max_songs}: {track['name']} by {track['artists'][0]['name']}")
                else:
                    # Reduce weight for this artist if no track found
                    idx = artist_ids.index(selected_aid)
                    artist_weights[idx] *= 0.5
                    
            except Exception as e:
                print(f"[ERROR] Error selecting track: {e}")
                continue
        
        # Add all selected tracks to playlist in one batch after discovery is complete
        if selected_tracks:
            print(f"[INFO] Discovery complete! Adding {len(selected_tracks)} tracks to playlist...")
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