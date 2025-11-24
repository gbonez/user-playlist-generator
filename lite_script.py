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
import psycopg2
import sys

# Add db creation directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), 'db creation'))
try:
    from build_audio_features_youtube import (
        search_youtube, 
        download_and_analyze_audio, 
        extract_audio_features,
        YouTubeRateLimitError
    )
    AUDIO_FEATURES_AVAILABLE = True
except ImportError:
    print("[WARN] Could not import audio feature extraction modules - similarity matching disabled")
    AUDIO_FEATURES_AVAILABLE = False

# ==== LITE SCRIPT CONFIG ====
# This is a real-time version without any caching or data storage
# Each run scans liked songs fresh and generates recommendations

LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY")

# Load database URL from secrets
def load_database_url():
    """Load DATABASE_URL from secrets.json or environment"""
    secrets_paths = [
        'secrets.json',
        os.path.join(os.path.dirname(__file__), 'secrets.json')
    ]
    
    for path in secrets_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                secrets = json.load(f)
                return secrets.get('DATABASE_PUBLIC_URL') or secrets.get('DATABASE_URL')
    
    return os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PUBLIC_URL')

DATABASE_URL = load_database_url()

scope = "playlist-modify-public playlist-modify-private user-library-read user-read-recently-played user-top-read"

# ==== DATABASE HELPER FUNCTIONS ====

def get_db_connection():
    """Get Postgres database connection"""
    try:
        if not DATABASE_URL:
            print("[WARN] No DATABASE_URL found - similarity matching disabled")
            return None
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"[WARN] Failed to connect to database: {e} - similarity matching disabled")
        return None

def add_track_to_audio_features_db(conn, track_id, artist_name, track_name, spotify_uri, popularity, features, youtube_title):
    """Add a track's audio features to the database"""
    insert_sql = """
    INSERT INTO audio_features (
        spotify_track_id, artist_name, track_name,
        tempo_bpm, key_musical, beat_regularity,
        brightness_hz, treble_hz, fullness_hz, dynamic_range,
        percussiveness, loudness,
        warmth, punch,
        texture,
        energy, danceability, mood_positive, acousticness, instrumental,
        popularity, spotify_uri, youtube_match
    ) VALUES (
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s,
        %s, %s,
        %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s
    )
    ON CONFLICT (spotify_track_id) DO NOTHING
    RETURNING id
    """
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(insert_sql, (
                track_id,
                artist_name,
                track_name,
                # Rhythm
                round(features.get('tempo', 0), 6),
                features.get('key_estimate', 0),
                round(features.get('beat_strength', 0), 6),
                # Spectral
                round(features.get('spectral_centroid', 0), 6),
                round(features.get('spectral_rolloff', 0), 6),
                round(features.get('spectral_bandwidth', 0), 6),
                round(features.get('spectral_contrast', 0), 6),
                # Temporal
                round(features.get('zero_crossing_rate', 0), 6),
                round(features.get('rms_energy', 0), 6),
                # Harmonic/Percussive
                round(features.get('harmonic_mean', 0), 6),
                round(features.get('percussive_mean', 0), 6),
                # Timbral
                round(features.get('mfcc_mean', 0), 6),
                # Computed
                round(features.get('energy', 0), 6),
                round(features.get('danceability', 0), 6),
                round(features.get('valence', 0), 6),
                round(features.get('acousticness', 0), 6),
                round(features.get('instrumentalness', 0), 6),
                # Metadata
                popularity,
                spotify_uri,
                youtube_title
            ))
            conn.commit()
            result = cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"[ERROR] Failed to insert track into database: {e}")
        conn.rollback()
        return None

def find_most_similar_track_in_db(conn, features, liked_track_ids, max_results=10):
    """
    Find the most mathematically similar tracks in the database
    Uses Euclidean distance across all audio feature columns
    Excludes tracks the user has already liked
    Returns multiple results so we can validate them
    """
    if not features:
        return []
    
    # Build the exclusion list for SQL
    exclusion_clause = ""
    if liked_track_ids:
        placeholders = ','.join(['%s'] * len(liked_track_ids))
        exclusion_clause = f"AND spotify_track_id NOT IN ({placeholders})"
    
    # Calculate similarity using weighted Euclidean distance
    # Weights are adjusted based on feature importance for similarity
    similarity_sql = f"""
    SELECT 
        spotify_track_id,
        artist_name,
        track_name,
        spotify_uri,
        popularity,
        youtube_match,
        -- Calculate weighted Euclidean distance
        SQRT(
            POW((tempo_bpm - %s) / 200.0, 2) * 0.8 +           -- Tempo (normalized by 200 bpm, weight 0.8)
            POW(beat_regularity - %s, 2) * 1.2 +                -- Beat regularity (weight 1.2)
            POW((brightness_hz - %s) / 5000.0, 2) * 1.0 +       -- Brightness (normalized, weight 1.0)
            POW((treble_hz - %s) / 10000.0, 2) * 0.7 +          -- Treble (normalized, weight 0.7)
            POW((fullness_hz - %s) / 5000.0, 2) * 0.6 +         -- Fullness (normalized, weight 0.6)
            POW((dynamic_range - %s) / 40.0, 2) * 0.9 +         -- Dynamic range (normalized, weight 0.9)
            POW(percussiveness - %s, 2) * 0.8 +                 -- Percussiveness (weight 0.8)
            POW(loudness - %s, 2) * 0.7 +                       -- Loudness (weight 0.7)
            POW(warmth - %s, 2) * 1.0 +                         -- Warmth/harmonic (weight 1.0)
            POW(punch - %s, 2) * 0.8 +                          -- Punch/percussive (weight 0.8)
            POW(texture - %s, 2) * 0.9 +                        -- Texture/MFCC (weight 0.9)
            POW(energy - %s, 2) * 1.5 +                         -- Energy (weight 1.5 - very important)
            POW(danceability - %s, 2) * 1.3 +                   -- Danceability (weight 1.3 - important)
            POW(mood_positive - %s, 2) * 1.2 +                  -- Valence/mood (weight 1.2 - important)
            POW(acousticness - %s, 2) * 1.0 +                   -- Acousticness (weight 1.0)
            POW(instrumental - %s, 2) * 0.8                     -- Instrumentalness (weight 0.8)
        ) AS similarity_distance
    FROM audio_features
    WHERE spotify_track_id IS NOT NULL
    {exclusion_clause}
    ORDER BY similarity_distance ASC
    LIMIT %s
    """
    
    try:
        with conn.cursor() as cursor:
            params = [
                features.get('tempo', 0),
                features.get('beat_strength', 0),
                features.get('spectral_centroid', 0),
                features.get('spectral_rolloff', 0),
                features.get('spectral_bandwidth', 0),
                features.get('spectral_contrast', 0),
                features.get('zero_crossing_rate', 0),
                features.get('rms_energy', 0),
                features.get('harmonic_mean', 0),
                features.get('percussive_mean', 0),
                features.get('mfcc_mean', 0),
                features.get('energy', 0),
                features.get('danceability', 0),
                features.get('valence', 0),
                features.get('acousticness', 0),
                features.get('instrumentalness', 0)
            ]
            
            # Add liked track IDs to params if they exist
            if liked_track_ids:
                params.extend(liked_track_ids)
            
            # Add limit
            params.append(max_results)
            
            cursor.execute(similarity_sql, params)
            results = cursor.fetchall()
            
            similar_tracks = []
            for result in results:
                similar_tracks.append({
                    'id': result[0],
                    'artist_name': result[1],
                    'track_name': result[2],
                    'uri': result[3],
                    'popularity': result[4],
                    'youtube_match': result[5],
                    'similarity_distance': result[6]
                })
            
            return similar_tracks
            
    except Exception as e:
        print(f"[ERROR] Failed to find similar tracks: {e}")
        return []

# ==== HELPER FUNCTIONS ====
def get_lastfm_track_genres(artist_name, track_name):
    """
    Get genre tags for a track from Last.fm
    Returns list of genre strings (lowercase)
    """
    if not LASTFM_API_KEY:
        return []
    
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "track.getInfo",
            "artist": artist_name,
            "track": track_name,
            "api_key": LASTFM_API_KEY,
            "format": "json"
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if "track" in data and "toptags" in data["track"] and "tag" in data["track"]["toptags"]:
            tags = data["track"]["toptags"]["tag"]
            # Extract tag names and normalize to lowercase
            genres = [tag["name"].lower() for tag in tags if "name" in tag]
            return genres[:10]  # Return top 10 tags
        
        return []
        
    except Exception as e:
        print(f"[WARN] Could not fetch Last.fm genres: {e}")
        return []

def compare_genres(seed_genres, candidate_genres):
    """
    Compare two genre lists and return True if they share at least one genre
    Returns: (bool: has_match, list: shared_genres)
    """
    if not seed_genres or not candidate_genres:
        # If either track has no genre data, we can't validate - skip this check
        return (None, [])
    
    # Convert to sets for efficient comparison
    seed_set = set(seed_genres)
    candidate_set = set(candidate_genres)
    
    # Find intersection
    shared = seed_set & candidate_set
    
    return (len(shared) > 0, list(shared))

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

def get_similar_tracks_by_audio_features_db(sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids=None, liked_track_ids=None, max_follower_count=None):
    """
    Find similar tracks using the audio features database and YouTube/librosa analysis
    
    Strategy:
    1. Get the seed track info from Spotify
    2. Search for it on YouTube and analyze with librosa
    3. Add audio features to database
    4. Query database for most mathematically similar track (that user hasn't liked)
    5. Return that track
    
    Args:
        sp: Spotify client
        seed_track_id: The track ID to use as seed for similarity matching
        existing_artist_ids: Set of artist IDs already in the playlist
        liked_songs_artist_ids: Set of artist IDs from user's liked songs (to exclude)
        liked_track_ids: List of track IDs the user has liked (to exclude from results)
        max_follower_count: Maximum artist follower count (None = no limit)
    
    Returns:
        Track object if found, None otherwise
    """
    if not AUDIO_FEATURES_AVAILABLE or not DATABASE_URL:
        print("[SKIP] Audio features analysis not available, falling back to Spotify API")
        return get_similar_tracks_by_audio_features_spotify_fallback(
            sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids, max_follower_count
        )
    
    try:
        print(f"[INFO] [DB-SIMILARITY] Analyzing seed track {seed_track_id[:10]}... with YouTube + librosa")
        
        # Get seed track info from Spotify
        seed_track = safe_spotify_call(sp.track, seed_track_id)
        if not seed_track:
            print("[SKIP] Could not get seed track info")
            return None
        
        track_name = seed_track['name']
        artist_name = ', '.join([a['name'] for a in seed_track['artists']])
        
        print(f"[INFO] Seed track: '{track_name}' by {artist_name}")
        
        # Connect to database
        conn = get_db_connection()
        if not conn:
            print("[SKIP] Database connection failed")
            return None
        
        try:
            # Check if seed track already in database (REQUIREMENT 2: skip YouTube if exists)
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT spotify_track_id FROM audio_features WHERE spotify_track_id = %s",
                    (seed_track_id,)
                )
                already_exists = cursor.fetchone() is not None
            
            if already_exists:
                print(f"[INFO] ✓ Seed track already in database, skipping YouTube analysis")
            else:
                # Search YouTube for the track
                print(f"[INFO] Searching YouTube for: {artist_name} - {track_name}")
                video_id, youtube_title = search_youtube(track_name, artist_name, max_results=5)
                
                if not video_id:
                    print(f"[SKIP] Could not find track on YouTube - track cannot be used as seed")
                    return None  # Signal to caller to try another seed track
                
                print(f"[INFO] Found on YouTube: {youtube_title}")
                
                # Download and analyze with librosa
                print(f"[INFO] Downloading and analyzing audio features...")
                features = download_and_analyze_audio(video_id, track_name, artist_name)
                
                if not features:
                    print(f"[SKIP] Could not extract audio features - track cannot be used as seed")
                    return None  # Signal to caller to try another seed track
                
                print(f"[INFO] Extracted features: tempo={features['tempo']:.1f}bpm, energy={features['energy']:.3f}, dance={features['danceability']:.3f}")
                
                # Add to database
                print(f"[INFO] Adding seed track to database...")
                add_track_to_audio_features_db(
                    conn,
                    seed_track_id,
                    artist_name,
                    track_name,
                    seed_track['uri'],
                    seed_track.get('popularity', 0),
                    features,
                    youtube_title
                )
                
                time.sleep(2)  # Brief delay after YouTube download
            
            # Now query database for most similar track
            print(f"[INFO] Searching database for most similar track...")
            
            # Re-fetch features from database to ensure we have the exact same values
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT tempo_bpm, beat_regularity, brightness_hz, treble_hz, fullness_hz, 
                           dynamic_range, percussiveness, loudness, warmth, punch, texture,
                           energy, danceability, mood_positive, acousticness, instrumental
                    FROM audio_features
                    WHERE spotify_track_id = %s
                """, (seed_track_id,))
                row = cursor.fetchone()
                
                if not row:
                    print("[ERROR] Seed track not found in database after insertion")
                    return None
                
                # Build features dict for similarity search
                features_from_db = {
                    'tempo': row[0],
                    'beat_strength': row[1],
                    'spectral_centroid': row[2],
                    'spectral_rolloff': row[3],
                    'spectral_bandwidth': row[4],
                    'spectral_contrast': row[5],
                    'zero_crossing_rate': row[6],
                    'rms_energy': row[7],
                    'harmonic_mean': row[8],
                    'percussive_mean': row[9],
                    'mfcc_mean': row[10],
                    'energy': row[11],
                    'danceability': row[12],
                    'valence': row[13],
                    'acousticness': row[14],
                    'instrumentalness': row[15]
                }
            
            # Find most similar tracks (get top 10 to validate)
            # REQUIREMENT 1: We'll validate each until we find one that passes all requirements
            similar_tracks_list = find_most_similar_track_in_db(conn, features_from_db, liked_track_ids or [], max_results=10)
            
            if not similar_tracks_list:
                print("[INFO] No similar tracks found in database")
                return None
            
            print(f"[INFO] Found {len(similar_tracks_list)} similar tracks in database, validating...")
            
            # Fetch seed track genres from Last.fm (once, outside the loop)
            print(f"[INFO] Fetching genres for seed track from Last.fm...")
            seed_genres = get_lastfm_track_genres(artist_name, track_name)
            if seed_genres:
                print(f"[INFO] Seed track genres: {', '.join(seed_genres[:5])}")
            else:
                print(f"[WARN] No genre data available for seed track - will skip genre validation")
            
            # Try each similar track until we find one that passes validation
            for idx, similar_track_info in enumerate(similar_tracks_list, 1):
                print(f"[INFO] Candidate {idx}: '{similar_track_info['track_name']}' by {similar_track_info['artist_name']} (distance: {similar_track_info['similarity_distance']:.4f})")
                
                # Get full track info from Spotify
                similar_track = safe_spotify_call(sp.track, similar_track_info['id'])
                
                if not similar_track:
                    print(f"[SKIP] Could not get track info from Spotify")
                    continue
                
                # REQUIREMENT 1: Validate the track (follower count, not in liked songs, etc.)
                if not validate_track_lite(similar_track, existing_artist_ids, liked_songs_artist_ids, max_follower_count):
                    print(f"[SKIP] Track did not pass validation requirements")
                    continue
                
                # GENRE VALIDATION: Check if candidate shares at least one genre with seed
                if LASTFM_API_KEY and seed_genres:
                    print(f"[INFO] Checking genre compatibility...")
                    candidate_artist = similar_track_info['artist_name']
                    candidate_track = similar_track_info['track_name']
                    candidate_genres = get_lastfm_track_genres(candidate_artist, candidate_track)
                    
                    if candidate_genres:
                        print(f"[INFO] Candidate genres: {', '.join(candidate_genres[:5])}")
                        has_match, shared_genres = compare_genres(seed_genres, candidate_genres)
                        
                        if has_match:
                            print(f"[SUCCESS] ✓ Genre match found: {', '.join(shared_genres)}")
                        else:
                            print(f"[SKIP] No shared genres between seed and candidate")
                            continue
                    else:
                        print(f"[WARN] No genre data for candidate - accepting anyway (can't validate)")
                    
                    time.sleep(0.2)  # Rate limit courtesy for Last.fm API
                
                # Found a valid track!
                print(f"[SUCCESS] ✓ Found mathematically similar track: {similar_track['name']} by {similar_track['artists'][0]['name']}")
                print(f"[SUCCESS] ✓ Similarity distance: {similar_track_info['similarity_distance']:.4f}")
                return similar_track
            
            # If we get here, none of the similar tracks passed validation
            print("[INFO] No similar tracks passed validation requirements")
            return None
            
        finally:
            conn.close()
        
    except YouTubeRateLimitError as e:
        print(f"[ERROR] YouTube rate limit hit: {e}")
        print("[INFO] Falling back to Spotify API recommendations")
        return get_similar_tracks_by_audio_features_spotify_fallback(
            sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids, max_follower_count
        )
    except Exception as e:
        print(f"[ERROR] Error finding similar tracks by audio features (DB): {e}")
        import traceback
        traceback.print_exc()
        return None

def get_similar_tracks_by_audio_features_spotify_fallback(sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None):
    """
    Fallback method using Spotify's built-in recommendations API
    (Original implementation as backup)
    """
    try:
        print(f"[INFO] [FALLBACK] Using Spotify recommendations API for seed track {seed_track_id[:10]}...")
        
        # Get audio features for the seed track
        features = safe_spotify_call(sp.audio_features, seed_track_id)
        if not features or not features[0]:
            print("[SKIP] Could not get audio features for seed track")
            return None
        
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
            return None
        
        # Validate and find first valid track
        for track in recs["tracks"]:
            if validate_track_lite(track, existing_artist_ids, liked_songs_artist_ids, max_follower_count):
                print(f"[SUCCESS] Found similar track: {track['name']} by {track['artists'][0]['name']}")
                return track
        
        print(f"[INFO] No valid similar tracks found")
        return None
        
    except Exception as e:
        print(f"[ERROR] Error finding similar tracks by audio features (Spotify fallback): {e}")
        return None

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

def search_user_playlists_for_artist(sp, artist_id, artist_name, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None, max_playlists=5):
    """
    Search user-created playlists containing the artist and try to find valid tracks
    
    Args:
        sp: Spotify client
        artist_id: The artist ID to search for
        artist_name: The artist name (for search)
        existing_artist_ids: Set of artist IDs already in the playlist
        liked_songs_artist_ids: Set of artist IDs from user's liked songs (to exclude)
        max_follower_count: Maximum artist follower count (None = no limit)
        max_playlists: Maximum number of playlists to check (default 5)
    
    Returns:
        Track object if found, None otherwise
    """
    print(f"[INFO] Searching user playlists for '{artist_name}'...")
    
    try:
        # Search for playlists containing the artist name
        candidate_playlists = []
        seen_playlist_ids = set()
        
        # Fetch multiple pages to get variety
        for page_offset in [0, 50, 100, 150]:
            search_res = safe_spotify_call(sp.search, artist_name, type="playlist", limit=50, offset=page_offset)
            if not search_res or "playlists" not in search_res or not search_res["playlists"].get("items"):
                break
            
            for pl in search_res["playlists"]["items"]:
                if not pl or not pl.get("id"):
                    continue
                
                pid = pl["id"]
                if pid in seen_playlist_ids:
                    continue
                    
                seen_playlist_ids.add(pid)
                candidate_playlists.append(pl)
            
            time.sleep(0.15)  # Rate limiting courtesy
            
            # Stop if we have enough candidates
            if len(candidate_playlists) >= max_playlists * 3:
                break
        
        if not candidate_playlists:
            print(f"[INFO] No user playlists found for '{artist_name}'")
            return None
        
        # Shuffle to avoid always checking the same popular playlists
        random.shuffle(candidate_playlists)
        
        playlists_checked = 0
        for pl in candidate_playlists:
            if playlists_checked >= max_playlists:
                break
            
            playlist_id = pl["id"]
            playlist_name = pl.get("name", "<unknown>")
            
            # Fetch playlist items to verify artist is present
            playlist_data = safe_spotify_call(
                sp.playlist_items,
                playlist_id,
                fields="items(track(id,name,artists(id,name,followers)))",
                limit=100
            )
            
            if not playlist_data or "items" not in playlist_data:
                print(f"[SKIP] Playlist '{playlist_name}' is empty or inaccessible")
                continue
            
            # Verify the artist is actually in this playlist
            contains_artist = False
            for item in playlist_data["items"]:
                track = item.get("track")
                if not track:
                    continue
                
                for artist in track.get("artists", []):
                    if artist.get("id") == artist_id:
                        contains_artist = True
                        break
                
                if contains_artist:
                    break
            
            if not contains_artist:
                print(f"[SKIP] Playlist '{playlist_name}' doesn't actually contain '{artist_name}'")
                continue
            
            # Count how many tracks by this artist are in the playlist
            artist_track_count = sum(
                1 for item in playlist_data["items"]
                if item.get("track") and any(
                    a.get("id") == artist_id for a in item["track"].get("artists", [])
                )
            )
            
            # Skip playlists dominated by this artist (likely artist-focused playlists)
            if artist_track_count > 10:
                print(f"[SKIP] Playlist '{playlist_name}' has too many tracks by '{artist_name}' ({artist_track_count})")
                continue
            
            playlists_checked += 1
            print(f"[INFO] Checking playlist '{playlist_name}' (contains {artist_track_count} tracks by '{artist_name}')...")
            
            # Try up to 10 times to find a valid track from this playlist
            attempts = 0
            max_attempts_per_playlist = 10
            
            while attempts < max_attempts_per_playlist:
                attempts += 1
                
                if not playlist_data["items"]:
                    break
                
                # Pick a random track from the playlist
                item = random.choice(playlist_data["items"])
                track = item.get("track")
                
                if not track or not track.get("id"):
                    continue
                
                # Validate the track
                if validate_track_lite(track, existing_artist_ids, liked_songs_artist_ids, max_follower_count):
                    print(f"[SUCCESS] Found valid track from playlist '{playlist_name}': {track['name']} by {track['artists'][0]['name']}")
                    return track
            
            print(f"[INFO] No valid tracks found in playlist '{playlist_name}' after {attempts} attempts")
        
        print(f"[INFO] Checked {playlists_checked} playlists, no valid tracks found")
        return None
        
    except Exception as e:
        print(f"[ERROR] Error searching user playlists: {e}")
        return None

def select_track_for_artist_lite(sp, artist_name, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None):
    """
    Real-time track selection following the exact strategy:
    
    Strategy (in order):
    5a. Mathematical audio features check (like Chosic) using rolled artist's liked songs or top tracks as seeds
    5b. Search user playlists containing the rolled artist (try 10 times per playlist, max 5 playlists)
    5c. Last.fm similar artists as fallback
    5d. Try generating with artist as seed up to 10 times
    
    Returns None if all methods fail (will trigger re-roll)
    
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
    
    print(f"[INFO] === Starting track discovery for '{artist_name}' ===")

    # ===== STEP 5a: Mathematical audio features check (like Chosic) =====
    print(f"[5a] Trying mathematical audio features matching for '{artist_name}'...")
    
    # REQUIREMENT 3 & 4: Try multiple seed tracks from artist's liked songs
    # If one can't be found on YouTube, try another. If none work, signal to re-roll artist.
    
    # Get ALL liked tracks by this artist (not just one random one)
    artist_liked_tracks = []
    try:
        offset = 0
        limit = 50
        while True:
            results = safe_spotify_call(sp.current_user_saved_tracks, limit=limit, offset=offset)
            if not results or not results.get("items"):
                break
            
            for item in results["items"]:
                track = item.get("track")
                if not track:
                    continue
                
                # Check if any artist matches
                for artist in track.get("artists", []):
                    if artist.get("id") == artist_id:
                        artist_liked_tracks.append(track["id"])
                        break
            
            if len(results["items"]) < limit:
                break
            
            offset += limit
    except Exception as e:
        print(f"[WARN] Could not fetch liked tracks: {e}")
    
    # If no liked tracks, try artist's top tracks as fallback
    if not artist_liked_tracks:
        print(f"[INFO] No liked tracks found for '{artist_name}', trying top tracks as seed...")
        top_tracks_res = safe_spotify_call(sp.artist_top_tracks, artist_id, country="US")
        if top_tracks_res and "tracks" in top_tracks_res and top_tracks_res["tracks"]:
            for track in top_tracks_res["tracks"][:5]:  # Try up to 5 top tracks
                if track.get("id"):
                    artist_liked_tracks.append(track["id"])
    
    if not artist_liked_tracks:
        # REQUIREMENT 4: No tracks available from artist - signal to re-roll artist
        print(f"[FAIL] No tracks available for '{artist_name}' - WILL RE-ROLL ARTIST")
        return None
    
    # Get all liked track IDs to exclude from similarity search (fetch once, use for all attempts)
    liked_track_ids = []
    try:
        offset = 0
        limit = 50
        while True:
            results = safe_spotify_call(sp.current_user_saved_tracks, limit=limit, offset=offset)
            if not results or not results.get("items"):
                break
            
            for item in results["items"]:
                track = item.get("track")
                if track and track.get("id"):
                    liked_track_ids.append(track["id"])
            
            if len(results["items"]) < limit:
                break
            
            offset += limit
    except Exception as e:
        print(f"[WARN] Could not fetch all liked track IDs: {e}")
    
    print(f"[INFO] Found {len(artist_liked_tracks)} potential seed tracks for '{artist_name}'")
    print(f"[INFO] Excluding {len(liked_track_ids)} liked tracks from similarity search")
    
    # REQUIREMENT 3: Try up to 10 seed tracks until one works (exists in DB or can be found on YouTube)
    random.shuffle(artist_liked_tracks)  # Randomize order
    max_seed_attempts = min(10, len(artist_liked_tracks))  # Try up to 10 seeds
    
    for attempt in range(max_seed_attempts):
        seed_track_id = artist_liked_tracks[attempt]
        print(f"[INFO] Seed attempt {attempt + 1}/{max_seed_attempts}: Trying seed track {seed_track_id[:10]}...")
        
        similar_track = get_similar_tracks_by_audio_features_db(
            sp, 
            seed_track_id, 
            existing_artist_ids, 
            liked_songs_artist_ids,
            liked_track_ids,
            max_follower_count
        )
        
        if similar_track:
            print(f"[SUCCESS] ✓ Found track via audio features: {similar_track['name']} by {similar_track['artists'][0]['name']}")
            return similar_track
        else:
            print(f"[INFO] Seed attempt {attempt + 1} failed, trying next seed track...")
            # Continue to next seed track
    
    # REQUIREMENT 4: If we tried up to 10 seed tracks and none worked, signal to re-roll artist
    print(f"[FAIL] All {max_seed_attempts} seed attempts failed for '{artist_name}' - WILL RE-ROLL ARTIST")
    
    print(f"[5a] Audio features matching failed for '{artist_name}'")

    # ===== STEP 5b: Try generating with artist as seed (last resort) =====
    print(f"[5b] Trying seed generation with artist ID as seed for '{artist_name}'...")
    
    for attempt in range(10):
        try:
            recs = safe_spotify_call(
                sp.recommendations,
                seed_artists=[artist_id],
                limit=50
            )
            
            if not recs or "tracks" not in recs:
                continue
            
            # Validate tracks
            for track in recs["tracks"]:
                if validate_track_lite(track, existing_artist_ids, liked_songs_artist_ids, max_follower_count):
                    print(f"[SUCCESS] Found track via artist seed (attempt {attempt + 1}): {track['name']} by {track['artists'][0]['name']}")
                    return track
            
            print(f"[INFO] Attempt {attempt + 1}/10: No valid tracks from artist seed recommendations")
            
        except Exception as e:
            print(f"[ERROR] Seed generation attempt {attempt + 1} failed: {e}")
    
    print(f"[5b] Seed generation failed after 10 attempts for '{artist_name}'")
    print(f"[FAIL] All methods exhausted for '{artist_name}' - will re-roll")
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
    Filters to only include artists with listening activity in last 6 months
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
        
        # Filter to only artists with listening activity in last 6 months
        # Spotify's medium_term is approximately 6 months, which is the closest metric available
        print("[INFO] Filtering artists by recent listening activity (last 6 months)...")
        artists_with_recent_activity = set()
        
        # If artist_play_map provided (from Spotify listening data), it already contains 6-month data
        if artist_play_map:
            for artist_name_lower in artist_play_map.keys():
                # Find matching artist_id from artist_counts
                for artist_id, info in artist_counts.items():
                    if info["name"].lower() == artist_name_lower:
                        artists_with_recent_activity.add(artist_id)
                        break
        
        # Filter artist_counts to only include recently active artists
        if artists_with_recent_activity:
            original_count = len(artist_counts)
            artist_counts = {
                aid: info for aid, info in artist_counts.items() 
                if aid in artists_with_recent_activity
            }
            filtered_count = original_count - len(artist_counts)
            print(f"[INFO] Filtered out {filtered_count} artists without recent listening activity (6 months)")
            print(f"[INFO] {len(artist_counts)} artists remaining with recent activity")
        else:
            print("[WARN] No listening data available - including all artists from liked songs")
        
        # Calculate weights based on how many songs user has liked
        for artist_id, info in artist_counts.items():
            total_liked = info["total_liked"]
            artist_name_lower = info["name"].lower()
            
            # Weight formula: MORE liked songs = HIGHER weight (prefer artists you already love)
            if total_liked >= 10:
                base_weight = 10
            elif total_liked >= 5:
                base_weight = 7
            elif total_liked >= 3:
                base_weight = 5
            elif total_liked == 2:
                base_weight = 3
            else:  # total_liked == 1
                base_weight = 1
            
            # Boost for recent listening activity (applies additional weight for recently played)
            if artist_play_map and artist_name_lower in artist_play_map:
                play_count = artist_play_map[artist_name_lower]
                # Scale boost based on play count - more plays = higher boost (capped at 3x)
                boost = min(1.0 + (play_count / 10), 3.0)
                base_weight *= boost
            
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
        max_attempts = max_songs * 10  # Allow more attempts since we're re-rolling on failure
        
        # Build weight lists for weighted selection
        artist_ids = list(artists_data.keys())
        artist_weights = [artists_data[aid]["weight"] for aid in artist_ids]
        
        # Track which artists have been rolled (never roll same artist twice)
        rolled_artist_ids = set()
        
        while len(selected_tracks) < max_songs and attempts < max_attempts:
            attempts += 1
            
            # Check if we have any artists left to roll
            available_artists = [aid for aid in artist_ids if aid not in rolled_artist_ids]
            if not available_artists:
                print("[WARN] All artists have been rolled, cannot find more tracks")
                break
            
            try:
                # Weighted random selection from liked songs artists (excluding already rolled)
                available_weights = [
                    artist_weights[artist_ids.index(aid)] 
                    for aid in available_artists
                ]
                
                selected_aid = random.choices(available_artists, weights=available_weights, k=1)[0]
                artist_info = artists_data[selected_aid]
                artist_name = artist_info.get("name", "")
                
                # Mark this artist as rolled (can never be rolled again)
                rolled_artist_ids.add(selected_aid)
                
                print(f"\n[LOTTERY] Attempt {attempts}: Rolled '{artist_name}' (liked {artist_info['total_liked']} songs, {len(available_artists)-1} artists remaining)")
                
                # Find tracks by similar artists (NOT by the selected artist themselves)
                track = select_track_for_artist_lite(sp, artist_name, existing_artist_ids, liked_songs_artist_ids, max_follower_count)
                
                if track:
                    selected_tracks.append(track)
                    # Add artist to existing set to avoid duplicates
                    for artist in track["artists"]:
                        if artist.get("id"):
                            existing_artist_ids.add(artist["id"])
                    print(f"[SUCCESS] ✓ Found track {len(selected_tracks)}/{max_songs}: {track['name']} by {track['artists'][0]['name']}\n")
                else:
                    print(f"[FAIL] ✗ All methods exhausted for '{artist_name}' - re-rolling lottery\n")
                    # Artist stays in rolled_artist_ids, will never be rolled again
                    
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
            "tracks_removed": 0,  # Old track removal logic removed
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