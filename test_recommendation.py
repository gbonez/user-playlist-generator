import sys
import os
import json
import pathlib
import webbrowser
import urllib.parse
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from lite_script import get_db_connection, find_most_similar_track_in_db, validate_track_lite, safe_spotify_call


# --- CONFIG ---
# Load secrets from secrets.json if env vars are not set
def load_secrets():
    secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
    if os.path.exists(secrets_path):
        with open(secrets_path, 'r') as f:
            return json.load(f)
    return {}


secrets = load_secrets()

SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID') or secrets.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET') or secrets.get('SPOTIFY_CLIENT_SECRET')
BASE_URL = os.environ.get('BASE_URL') or secrets.get('BASE_URL') or 'http://localhost:5001'
SPOTIFY_REDIRECT_URI = f"{BASE_URL}/callback"
SCOPE = "playlist-modify-public playlist-modify-private user-library-read user-read-recently-played user-top-read"

def create_spotify_client():
    """Create authenticated Spotify client with user permissions (db_creation style)"""
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=".spotify_user_playlists_cache"
    )
    return Spotify(auth_manager=auth_manager)

# --- MAIN TEST FUNCTION ---

def get_playlist_tracks_and_artists(sp, playlist_id):
    """Fetch all track IDs and artist IDs from a playlist."""
    track_ids = set()
    artist_ids = set()
    offset = 0
    limit = 100
    while True:
        results = safe_spotify_call(sp.playlist_tracks, playlist_id, offset=offset, limit=limit)
        if not results or not results.get('items'):
            break
        for item in results['items']:
            track = item.get('track')
            if not track:
                continue
            track_ids.add(track['id'])
            for artist in track['artists']:
                artist_ids.add(artist['id'])
        if len(results['items']) < limit:
            break
        offset += limit
    return track_ids, artist_ids

def test_song_recommendation(seed_track_id, max_results=10):
    # Clear console at start
    os.system('clear')
    # Empty output files at start
    output_dir = pathlib.Path(__file__).parent / 'test-output'
    output_dir.mkdir(exist_ok=True)
    valid_path = output_dir / 'valid.json'
    invalid_path = output_dir / 'invalid.json'
    with open(valid_path, 'w') as f:
        json.dump([], f)
    with open(invalid_path, 'w') as f:
        json.dump([], f)
    # Authenticate with Spotify (db_creation style)
    sp = create_spotify_client()

    # Fetch full seed track info
    seed_track = safe_spotify_call(sp.track, seed_track_id)
    if not seed_track:
        print(f"[ERROR] Could not fetch seed track info for {seed_track_id}")
        return
    print(f"Seed track: {seed_track['name']} by {', '.join([a['name'] for a in seed_track['artists']])}")
    seed_artist_ids = {a['id'] for a in seed_track['artists']}

    # Fetch tracks and artists from the target playlist (for exclusion)
    playlist_track_ids, playlist_artist_ids = set(), set()
    if hasattr(test_song_recommendation, 'target_playlist_id') and test_song_recommendation.target_playlist_id:
        playlist_track_ids, playlist_artist_ids = get_playlist_tracks_and_artists(sp, test_song_recommendation.target_playlist_id)

    # Set popularity follower count (default: 25000)
    max_follower_count = getattr(test_song_recommendation, 'max_follower_count', 25000)

    # Connect to DB
    conn = get_db_connection()
    if not conn:
        print("[ERROR] Could not connect to database.")
        return

    # Fetch features from DB
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT tempo_bpm, key_musical, beat_regularity, brightness_hz, treble_hz, fullness_hz, dynamic_range, percussiveness, loudness, warmth, punch, texture, energy, danceability, mood_positive, acousticness, instrumental FROM audio_features WHERE spotify_track_id = %s",
            (seed_track_id,)
        )
        row = cursor.fetchone()
        if not row:
            print(f"[WARN] Seed track {seed_track_id} not found in DB. Attempting to process and add...")
            # Try to process and add seed track using build_audio_features_from_spotify.py
            import subprocess
            script_path = pathlib.Path(__file__).parent / 'db_creation' / 'build_audio_features_from_spotify.py'
            if not script_path.exists():
                print(f"[ERROR] Feature extraction script not found: {script_path}")
                sys.exit(1)
            result = subprocess.run(['python3', str(script_path), '--track', seed_track_id], capture_output=True, text=True)
            print(result.stdout)
            if result.returncode != 0:
                print(f"[ERROR] Failed to process seed track. Details:\n{result.stderr}")
                sys.exit(1)
            # Try fetching again
            cursor.execute(
                "SELECT tempo_bpm, key_musical, beat_regularity, brightness_hz, treble_hz, fullness_hz, dynamic_range, percussiveness, loudness, warmth, punch, texture, energy, danceability, mood_positive, acousticness, instrumental FROM audio_features WHERE spotify_track_id = %s",
                (seed_track_id,)
            )
            row = cursor.fetchone()
            if not row:
                print(f"[ERROR] Seed track could not be processed and added to DB.")
                sys.exit(1)
        features = {
            'tempo': row[0],
            'key_estimate': row[1],
            'beat_strength': row[2],
            'spectral_centroid': row[3],
            'spectral_rolloff': row[4],
            'spectral_bandwidth': row[5],
            'spectral_contrast': row[6],
            'zero_crossing_rate': row[7],
            'rms_energy': row[8],
            'harmonic_mean': row[9],
            'percussive_mean': row[10],
            'mfcc_mean': row[11],
            'energy': row[12],
            'danceability': row[13],
            'valence': row[14],
            'acousticness': row[15],
            'instrumentalness': row[16]
        }
    print(f"Fetched features for seed track.")

    # Find similar tracks
    valid_tracks = []
    invalid_tracks = []
    checked_tracks = set()
    seen_artist_ids = set(playlist_artist_ids)  # Track artists already in playlist
    candidate_batch_size = max_results * 2
    batch_idx = 0
    while len(valid_tracks) < max_results:
        batch_idx += 1
        print(f"\n[INFO] Fetching batch {batch_idx} of candidates...")
        similar_tracks = find_most_similar_track_in_db(conn, features, liked_track_ids=list(playlist_track_ids | checked_tracks), max_results=candidate_batch_size)
        print(f"Found {len(similar_tracks)} similar tracks.")
        if not similar_tracks:
            print("[WARN] No more candidates found.")
            break
        for candidate in similar_tracks:
            if len(valid_tracks) >= max_results:
                break
            candidate_id = candidate['id']
            if candidate_id in checked_tracks:
                continue
            checked_tracks.add(candidate_id)
            candidate_track = safe_spotify_call(sp.track, candidate_id)
            if not candidate_track:
                invalid_tracks.append({
                    'artist': candidate['artist_name'],
                    'song': candidate['track_name'],
                    'distance': candidate['similarity_distance'],
                    'follower_count': None,
                    'failed_checks': ['spotify_fetch']
                })
                continue
            candidate_artist_ids = {a['id'] for a in candidate_track['artists']}
            # Fetch main artist's profile for follower count
            main_artist_id = candidate_track['artists'][0]['id']
            main_artist_profile = safe_spotify_call(sp.artist, main_artist_id)
            if main_artist_profile and 'followers' in main_artist_profile:
                candidate_follower_count = main_artist_profile['followers'].get('total', 0)
            else:
                candidate_follower_count = None
            failed_checks = []
            if candidate_artist_ids & seed_artist_ids:
                failed_checks.append('same_artist')
            if candidate_track['id'] in playlist_track_ids:
                failed_checks.append('track_in_playlist')
            # Only allow one song per artist in playlist
            if candidate_artist_ids & seen_artist_ids:
                failed_checks.append('artist_in_playlist')
            if candidate_follower_count is not None and candidate_follower_count > max_follower_count:
                failed_checks.append('follower_count')
            if failed_checks:
                invalid_tracks.append({
                    'artist': candidate['artist_name'],
                    'song': candidate['track_name'],
                    'distance': candidate['similarity_distance'],
                    'follower_count': candidate_follower_count,
                    'failed_checks': failed_checks
                })
            else:
                valid_tracks.append({
                    'artist': candidate['artist_name'],
                    'song': candidate['track_name'],
                    'distance': candidate['similarity_distance'],
                    'follower_count': candidate_follower_count
                })
                seen_artist_ids.update(candidate_artist_ids)
        if batch_idx > 10:
            print("[WARN] Stopping after 10 batches to avoid infinite loop.")
            break

    conn.close()

    # Output results to JSON files
    output_dir = pathlib.Path(__file__).parent / 'test-output'
    output_dir.mkdir(exist_ok=True)
    valid_path = output_dir / 'valid.json'
    invalid_path = output_dir / 'invalid.json'
    with open(valid_path, 'w') as f:
        json.dump(valid_tracks, f, indent=2)
    with open(invalid_path, 'w') as f:
        json.dump(invalid_tracks, f, indent=2)


    # Add valid songs to target playlist if provided
    if hasattr(test_song_recommendation, 'target_playlist_id') and test_song_recommendation.target_playlist_id:
        print(f"\n[INFO] Adding {len(valid_tracks)} valid songs to playlist {test_song_recommendation.target_playlist_id}...")
        track_uris = []
        for vt in valid_tracks:
            search_res = safe_spotify_call(sp.search, f"track:{vt['song']} artist:{vt['artist']}", type="track", limit=1)
            uri = None
            if search_res and search_res.get('tracks', {}).get('items'):
                uri = search_res['tracks']['items'][0]['uri']
            if uri:
                track_uris.append(uri)
        if track_uris:
            for i in range(0, len(track_uris), 100):
                safe_spotify_call(sp.playlist_add_items, test_song_recommendation.target_playlist_id, track_uris[i:i+100])
            print(f"[INFO] Added {len(track_uris)} tracks to playlist {test_song_recommendation.target_playlist_id}")
        else:
            print("[WARN] No valid track URIs found to add.")

        # --- Check playlist size and add more if needed ---
        def get_playlist_total_tracks(sp, playlist_id):
            playlist = safe_spotify_call(sp.playlist, playlist_id)
            if playlist and 'tracks' in playlist:
                return playlist['tracks'].get('total', 0)
            return 0

        total_added = len(track_uris)
        total_in_playlist = get_playlist_total_tracks(sp, test_song_recommendation.target_playlist_id)
        print(f"[INFO] Playlist now has {total_in_playlist} tracks (added {total_added})")
        attempts = 0
        # If playlist did not grow as expected, keep searching and adding more songs
        while total_in_playlist < total_added and attempts < 10:
            print(f"[WARN] Playlist has {total_in_playlist} tracks, expected at least {total_added}. Searching for more songs...")
            # Find and add one more valid track
            # Use a larger batch to avoid infinite loop if needed
            more_similar_tracks = find_most_similar_track_in_db(conn, features, liked_track_ids=list(playlist_track_ids | checked_tracks), max_results=10)
            found_new = False
            for candidate in more_similar_tracks:
                candidate_id = candidate['id']
                if candidate_id in checked_tracks or candidate_id in playlist_track_ids:
                    continue
                checked_tracks.add(candidate_id)
                candidate_track = safe_spotify_call(sp.track, candidate_id)
                if not candidate_track:
                    continue
                candidate_artist_ids = {a['id'] for a in candidate_track['artists']}
                main_artist_id = candidate_track['artists'][0]['id']
                main_artist_profile = safe_spotify_call(sp.artist, main_artist_id)
                if main_artist_profile and 'followers' in main_artist_profile:
                    candidate_follower_count = main_artist_profile['followers'].get('total', 0)
                else:
                    candidate_follower_count = None
                failed_checks = []
                if candidate_artist_ids & seed_artist_ids:
                    failed_checks.append('same_artist')
                if candidate_track['id'] in playlist_track_ids:
                    failed_checks.append('track_in_playlist')
                if candidate_artist_ids & seen_artist_ids:
                    failed_checks.append('artist_in_playlist')
                if candidate_follower_count is not None and candidate_follower_count > max_follower_count:
                    failed_checks.append('follower_count')
                if not failed_checks:
                    # Add this track
                    search_res = safe_spotify_call(sp.search, f"track:{candidate['track_name']} artist:{candidate['artist_name']}", type="track", limit=1)
                    uri = None
                    if search_res and search_res.get('tracks', {}).get('items'):
                        uri = search_res['tracks']['items'][0]['uri']
                    if uri:
                        safe_spotify_call(sp.playlist_add_items, test_song_recommendation.target_playlist_id, [uri])
                        print(f"[INFO] Added extra track: {candidate['track_name']} by {candidate['artist_name']}")
                        found_new = True
                        seen_artist_ids.update(candidate_artist_ids)
                        break
            if not found_new:
                print("[WARN] Could not find any more valid tracks to add.")
                break
            # Re-check playlist size
            total_in_playlist = get_playlist_total_tracks(sp, test_song_recommendation.target_playlist_id)
            attempts += 1
        if total_in_playlist >= total_added:
            print(f"[INFO] Playlist now has {total_in_playlist} tracks (meets/exceeds expected {total_added})")
        else:
            print(f"[WARN] Playlist still has {total_in_playlist} tracks, less than expected {total_added}.")

    # Clear console and print summary
    os.system('clear')
    total_checked = len(valid_tracks) + len(invalid_tracks)
    print(f"Total songs checked: {total_checked}")
    print(f"Total valid songs found: {len(valid_tracks)}")
    print(f"Total invalid songs found: {len(invalid_tracks)}")
    print(f"\n[INFO] Wrote {len(valid_tracks)} valid tracks to {valid_path}")
    print(f"[INFO] Wrote {len(invalid_tracks)} invalid tracks to {invalid_path}")

if __name__ == "__main__":
    import re
    def extract_spotify_id(url_or_id, kind):
        if not url_or_id:
            return None
        # If already an ID, return as is
        if re.fullmatch(r'[A-Za-z0-9]{22}', url_or_id):
            return url_or_id
        # Try to extract from URL
        if kind == 'track':
            m = re.search(r'track/([A-Za-z0-9]{22})', url_or_id)
            if m:
                return m.group(1)
        elif kind == 'playlist':
            m = re.search(r'playlist/([A-Za-z0-9]{22})', url_or_id)
            if m:
                return m.group(1)
        return url_or_id  # fallback

    if len(sys.argv) < 2:
        print("Usage: python test_recommendation.py <seed_track_id_or_url> [max_results] [target_playlist_id_or_url] [max_follower_count]")
        sys.exit(1)
    seed_track_id = extract_spotify_id(sys.argv[1], 'track')
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    target_playlist_id = extract_spotify_id(sys.argv[3], 'playlist') if len(sys.argv) > 3 else None
    max_follower_count = int(sys.argv[4]) if len(sys.argv) > 4 else 25000
    # Pass playlist ID and popularity threshold to function via attribute
    test_song_recommendation.target_playlist_id = target_playlist_id
    test_song_recommendation.max_follower_count = max_follower_count
    test_song_recommendation(seed_track_id, max_results)
