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

# Import audio utilities (Railway-friendly, not gitignored)
try:
    from audio_utils import (
        search_youtube,
        download_and_analyze_audio,
        extract_audio_features,
        YouTubeRateLimitError,
        process_track_for_db,
        check_audio_processing_available
    )
    AUDIO_FEATURES_AVAILABLE = check_audio_processing_available()
except ImportError:
    print("[WARN] Could not import audio utilities - similarity matching disabled")
    AUDIO_FEATURES_AVAILABLE = False
    
    # Stub functions
    def search_youtube(*args, **kwargs):
        raise Exception("Audio utilities not available")
    def download_and_analyze_audio(*args, **kwargs):
        raise Exception("Audio utilities not available")
    def extract_audio_features(*args, **kwargs):
        raise Exception("Audio utilities not available")
    def process_track_for_db(*args, **kwargs):
        return None, None
    def check_audio_processing_available():
        return False
    class YouTubeRateLimitError(Exception):
        pass
# ==== HELPER FUNCTIONS ====

def parse_spotify_url(url):
    """
    Parse a Spotify URL and extract type and ID
    Handles URLs with query parameters like ?si=...
    
    Args:
        url: Spotify URL (e.g., https://open.spotify.com/track/xxx?si=yyy)
    
    Returns:
        tuple: (type, id) where type is 'track', 'artist', 'album', 'playlist', or 'user'
               Returns (None, None) if invalid
    
    Examples:
        https://open.spotify.com/track/7fVvUY3EOoqc8lEwUamIMO?si=xxx -> ('track', '7fVvUY3EOoqc8lEwUamIMO')
        https://open.spotify.com/artist/1cLXpQsVOMiqdZzlSsyy8u?si=xxx -> ('artist', '1cLXpQsVOMiqdZzlSsyy8u')
        https://open.spotify.com/album/5Z9iiGl2FcIfa3BMiv6OIw?si=xxx -> ('album', '5Z9iiGl2FcIfa3BMiv6OIw')
        https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=xxx -> ('playlist', '37i9dQZF1DXcBWIGoYBM5M')
    """
    import re
    
    # Remove query parameters (everything after ?)
    url = url.split('?')[0]
    
    # Pattern: https://open.spotify.com/{type}/{id}
    pattern = r'https://open\.spotify\.com/(track|artist|album|playlist|user)/([a-zA-Z0-9]+)'
    match = re.match(pattern, url)
    
    if match:
        return match.group(1), match.group(2)
    
    return None, None

def fetch_tracks_from_source(sp, generation_mode, source_url):
    """
    Fetch tracks from different sources based on generation mode
    
    Args:
        sp: Spotify client
        generation_mode: 'track', 'artist', 'album', or 'playlist'
        source_url: Spotify URL to fetch from
    
    Returns:
        tuple: (list of track_ids, source_description)
               source_description is a string describing the source for logging
    """
    url_type, url_id = parse_spotify_url(source_url)
    
    if not url_type or not url_id:
        raise ValueError(f"Invalid Spotify URL: {source_url}")
    
    if generation_mode == 'track':
        # Single track mode
        track = safe_spotify_call(sp.track, url_id)
        if not track:
            raise ValueError(f"Could not fetch track: {url_id}")
        source_desc = f"track '{track['name']}' by {track['artists'][0]['name']}"
        return [track['id']], source_desc
    
    elif generation_mode == 'artist':
        # Artist mode - get all tracks from artist's top tracks and albums
        artist = safe_spotify_call(sp.artist, url_id)
        if not artist:
            raise ValueError(f"Could not fetch artist: {url_id}")
        
        source_desc = f"artist '{artist['name']}'"
        track_ids = []
        
        # Get top tracks
        top_tracks = safe_spotify_call(sp.artist_top_tracks, url_id, country='US')
        if top_tracks and 'tracks' in top_tracks:
            track_ids.extend([t['id'] for t in top_tracks['tracks'] if t.get('id')])
        
        # Get albums and their tracks
        albums = safe_spotify_call(sp.artist_albums, url_id, limit=50, album_type='album,single')
        if albums and 'items' in albums:
            for album in albums['items'][:10]:  # Limit to 10 albums
                album_tracks = safe_spotify_call(sp.album_tracks, album['id'])
                if album_tracks and 'items' in album_tracks:
                    track_ids.extend([t['id'] for t in album_tracks['items'] if t.get('id')])
        
        return track_ids, source_desc
    
    elif generation_mode == 'album':
        # Album mode - get all tracks from album
        album = safe_spotify_call(sp.album, url_id)
        if not album:
            raise ValueError(f"Could not fetch album: {url_id}")
        
        source_desc = f"album '{album['name']}' by {album['artists'][0]['name']}"
        track_ids = []
        
        album_tracks = safe_spotify_call(sp.album_tracks, url_id)
        if album_tracks and 'items' in album_tracks:
            track_ids.extend([t['id'] for t in album_tracks['items'] if t.get('id')])
        
        return track_ids, source_desc
    
    elif generation_mode == 'playlist':
        # Playlist mode - get all tracks from playlist
        playlist = safe_spotify_call(sp.playlist, url_id)
        if not playlist:
            raise ValueError(f"Could not fetch playlist: {url_id}")
        
        source_desc = f"playlist '{playlist['name']}' by {playlist['owner']['display_name']}"
        track_ids = []
        
        # Fetch all tracks from playlist (handle pagination)
        offset = 0
        while True:
            results = safe_spotify_call(sp.playlist_items, url_id, offset=offset, limit=100)
            if not results or not results.get('items'):
                break
            
            for item in results['items']:
                track = item.get('track')
                if track and track.get('id'):
                    track_ids.append(track['id'])
            
            if len(results['items']) < 100:
                break
            offset += 100
        
        return track_ids, source_desc
    
    else:
        raise ValueError(f"Unsupported generation mode: {generation_mode}")

# Fallback stub for Spotify similarity if audio features are unavailable
def get_similar_tracks_by_audio_features_spotify_fallback(sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids=None, max_follower_count=None):
    print("[WARN] Fallback: audio features similarity not available, using Spotify API only.")
    return None

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

def get_lastfm_artist_genres(artist_name):
    """Fetch genres from Last.fm for an artist"""
    if not LASTFM_API_KEY:
        return []
    
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getInfo",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json"
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if "artist" in data and "tags" in data["artist"] and "tag" in data["artist"]["tags"]:
            tags = data["artist"]["tags"]["tag"]
            genres = [tag["name"].lower() for tag in tags[:5] if isinstance(tag, dict)]
            return genres
        
        return []
    except Exception as e:
        print(f"[WARN] Last.fm genres error for {artist_name}: {e}")
        return []

def get_spotify_artist_genres(sp, artist_name):
    """Fetch genres from Spotify for an artist"""
    try:
        results = safe_spotify_call(sp.search, q=f"artist:{artist_name}", type="artist", limit=1)
        
        if results and "artists" in results and results["artists"]["items"]:
            artist = results["artists"]["items"][0]
            genres = [genre.lower() for genre in artist.get("genres", [])]
            return genres
        
        return []
    except Exception as e:
        print(f"[WARN] Spotify genres error for {artist_name}: {e}")
        return []

def normalize_genre(genre):
    """Normalize genre names for better matching"""
    genre = genre.lower().strip()
    
    # Common mappings
    mappings = {
        "hip hop": "hip-hop",
        "r&b": "rnb",
        "rhythm and blues": "rnb",
        "electronic dance music": "edm",
        "drum and bass": "drum-n-bass",
        "pop rock": "pop-rock",
        "indie rock": "indie-rock",
        "alternative rock": "alt-rock",
        "hard rock": "hard-rock",
        "heavy metal": "metal",
        "death metal": "metal",
        "black metal": "metal",
    }
    
    return mappings.get(genre, genre)

def get_or_create_artist_genres(sp, conn, artist_name):
    """
    Get genres for an artist from database, or fetch and store if not exists
    Returns list of up to 3 genres
    """
    try:
        # Check if artist already has genres in database
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT genres FROM artist_genres 
                WHERE artist_name = %s
            """, (artist_name,))
            row = cursor.fetchone()
            
            if row and row[0]:
                print(f"[INFO] Found cached genres for {artist_name}: {row[0]}")
                return row[0]
        
        # Not in database - fetch from multiple sources
        print(f"[INFO] Fetching genres for new artist: {artist_name}")
        
        spotify_genres = get_spotify_artist_genres(sp, artist_name)
        lastfm_genres = get_lastfm_artist_genres(artist_name)
        
        # Merge and rank genres
        genre_scores = {}
        
        # Spotify genres (weight 3)
        for genre in spotify_genres:
            normalized = normalize_genre(genre)
            genre_scores[normalized] = genre_scores.get(normalized, 0) + 3
        
        # Last.fm genres (weight 2)
        for genre in lastfm_genres:
            normalized = normalize_genre(genre)
            genre_scores[normalized] = genre_scores.get(normalized, 0) + 2
        
        # Sort and take top 3
        sorted_genres = sorted(genre_scores.items(), key=lambda x: x[1], reverse=True)
        top_genres = [genre for genre, score in sorted_genres[:3]]
        
        if top_genres:
            # Store in database
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO artist_genres (artist_name, genres)
                    VALUES (%s, %s)
                    ON CONFLICT (artist_name) DO UPDATE
                    SET genres = EXCLUDED.genres
                """, (artist_name, top_genres))
            conn.commit()
            print(f"[INFO] Stored {len(top_genres)} genres for {artist_name}: {top_genres}")
            return top_genres
        else:
            print(f"[WARN] No genres found for {artist_name}")
            return []
            
    except Exception as e:
        print(f"[ERROR] Failed to get genres for {artist_name}: {e}")
        return []

def check_genre_match(seed_genres, candidate_genres):
    """
    Check if at least 1 out of 3 genres match between seed and candidate
    Returns (bool: has_match, list: matched_genres)
    """
    if not seed_genres or not candidate_genres:
        # If either has no genre data, skip genre validation
        print("[INFO] Genre data missing - skipping genre validation")
        return (True, [])
    
    # Normalize genres
    seed_set = set(normalize_genre(g) for g in seed_genres)
    candidate_set = set(normalize_genre(g) for g in candidate_genres)
    
    # Find matches
    matches = seed_set & candidate_set
    
    if matches:
        print(f"[GENRE MATCH] Found {len(matches)} matching genres: {list(matches)}")
        return (True, list(matches))
    else:
        print(f"[GENRE MISMATCH] Seed genres {list(seed_set)} vs Candidate genres {list(candidate_set)}")
        return (False, [])

def add_track_to_audio_features_db(conn, track_id, artist_name, track_name, spotify_uri, popularity, features, youtube_title):
    """
    Add a track's audio features to the database
    Also checks and populates artist_genres if needed
    """
    # First, check if artist has genres in the database
    # Extract first artist if multiple (comma-separated)
    first_artist = artist_name.split(',')[0].strip() if artist_name else None
    
    if first_artist:
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT array_length(genres, 1) as genre_count
                    FROM artist_genres 
                    WHERE artist_name = %s
                """, (first_artist,))
                result = cursor.fetchone()
                
                # If artist not in DB or has <3 genres, try to populate
                if not result or (result[0] is not None and result[0] < 3):
                    print(f"[INFO] Artist '{first_artist}' needs genre data, attempting to populate...")
                    # Note: We need sp (Spotify client) to fetch genres
                    # This will be handled by get_or_create_artist_genres which is already called elsewhere
                    # For now, just log it - the actual population happens in get_or_create_artist_genres
        except Exception as e:
            print(f"[WARN] Could not check artist genres for {first_artist}: {e}")
    
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

def ensure_track_in_db(sp, conn, track_id):
    """
    Ensure a track is in the database. If not, process and add it.
    Railway-friendly: Works in serverless environment with limited storage.
    
    Args:
        sp: Spotify client
        conn: Database connection
        track_id: Spotify track ID
    
    Returns:
        True if track is in database (or was successfully added), False otherwise
    """
    if not AUDIO_FEATURES_AVAILABLE:
        print("[WARN] Audio processing not available - skipping DB check")
        return False
    
    # Check if track already exists
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM audio_features WHERE spotify_track_id = %s",
                (track_id,)
            )
            if cursor.fetchone():
                return True  # Already in database
    except Exception as e:
        print(f"[WARN] Error checking if track exists in DB: {e}")
        return False
    
    # Track not in database - process it
    print(f"[INFO] Track {track_id} not in database, processing now...")
    
    try:
        # Use audio_utils to process track
        track_info, features = process_track_for_db(sp, track_id)
        
        if not track_info or not features:
            print(f"[WARN] Could not process track {track_id}")
            return False
        
        # Add to database using add_track_to_audio_features_db
        success = add_track_to_audio_features_db(
            conn,
            track_info['track_id'],
            track_info['artist_name'],
            track_info['track_name'],
            track_info['spotify_uri'],
            track_info['popularity'],
            features,
            track_info['youtube_title']
        )
        
        if success:
            print(f"[INFO] ✅ Successfully added track {track_id} to database")
        
        return success
    except YouTubeRateLimitError:
        print(f"[ERROR] YouTube rate limit hit while processing {track_id}")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to process track {track_id}: {e}")
        return False

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
        return get_similar_tracks_by_audio_features_spotify_fallback(sp, seed_track_id, existing_artist_ids, liked_songs_artist_ids, max_follower_count)
    
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
                            try:
                                video_id, youtube_title = search_youtube(track_name, artist_name, max_results=5)
                                if not video_id:
                                    print(f"[ERROR] No YouTube match found for '{track_name}' by '{artist_name}'. Skipping seed.")
                                    return None
                                print(f"[INFO] Downloading and analyzing YouTube audio for video: {youtube_title} ({video_id})")
                                features = download_and_analyze_audio(video_id, track_name, artist_name)
                                if not features:
                                    print(f"[ERROR] Audio analysis failed for YouTube video '{youtube_title}'. Skipping seed.")
                                    return None
                                print(f"[INFO] Audio features extracted: {features}")
                                # Add to database
                                add_track_to_audio_features_db(conn, seed_track_id, artist_name, track_name, seed_track['uri'], seed_track.get('popularity', 0), features, youtube_title)
                            except Exception as e:
                                print(f"[ERROR] Exception during YouTube download/analyze: {e}. Skipping seed.")
                                return None
                        # Now query database for most similar track
                        print(f"[INFO] Searching database for most similar track...")
                        # Re-fetch features from database to ensure we have the exact same values
                        with conn.cursor() as cursor:
                            cursor.execute(
                                "SELECT tempo_bpm, key_musical, beat_regularity, brightness_hz, treble_hz, fullness_hz, dynamic_range, percussiveness, loudness, warmth, punch, texture, energy, danceability, mood_positive, acousticness, instrumental FROM audio_features WHERE spotify_track_id = %s",
                                (seed_track_id,)
                            )
                            row = cursor.fetchone()
                            if not row:
                                print(f"[ERROR] Could not fetch features for seed track from DB. Skipping seed.")
                                return None
                            features_from_db = {
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
                        print(f"[DEBUG] Seed track features for comparison: {features_from_db}")
                        # Find most similar tracks (get top 10 to validate)
                        similar_tracks_list = find_most_similar_track_in_db(conn, features_from_db, liked_track_ids or [], max_results=10)
                        if not similar_tracks_list:
                            print(f"[WARN] No similar tracks found in database for seed track {seed_track_id}")
                            return None
                        print(f"[INFO] Found {len(similar_tracks_list)} similar tracks in database, validating...")
                        # Fetch seed track genres from Last.fm (once, outside the loop)
                        print(f"[INFO] Fetching genres for seed track from Last.fm...")
                        seed_genres = get_lastfm_track_genres(artist_name, track_name)
                        if seed_genres:
                            print(f"[DEBUG] Seed track genres: {seed_genres}")
                        else:
                            print(f"[DEBUG] No genres found for seed track.")
                        # Try each similar track until we find one that passes validation
                        for idx, similar_track_info in enumerate(similar_tracks_list, 1):
                            print(f"[DEBUG] Comparing candidate #{idx}: {similar_track_info['track_name']} by {similar_track_info['artist_name']} (ID: {similar_track_info['id']})")
                            print(f"[DEBUG] Candidate features: {similar_track_info}")
                            print(f"[DEBUG] Similarity distance: {similar_track_info['similarity_distance']}")
                            # Fetch full track info from Spotify
                            candidate_track = safe_spotify_call(sp.track, similar_track_info['id'])
                            if not candidate_track:
                                print(f"[WARN] Could not fetch candidate track info from Spotify. Skipping.")
                                continue
                            # Validate track
                            valid = validate_track_lite(candidate_track, existing_artist_ids, liked_songs_artist_ids, max_follower_count)
                            print(f"[DEBUG] Validation result: {valid}")
                            if not valid:
                                print(f"[INFO] Candidate track failed validation. Trying next.")
                                continue
                            # Compare genres if available
                            candidate_genres = get_lastfm_track_genres(similar_track_info['artist_name'], similar_track_info['track_name'])
                            has_genre_match, shared_genres = compare_genres(seed_genres, candidate_genres)
                            print(f"[DEBUG] Genre comparison: match={has_genre_match}, shared={shared_genres}")
                            if has_genre_match is False:
                                print(f"[INFO] Candidate track does not share genres with seed. Trying next.")
                                continue
                            print(f"[SUCCESS] Found valid similar track: {candidate_track['name']} by {candidate_track['artists'][0]['name']}")
                            return candidate_track
                        print("[INFO] No similar tracks passed validation requirements")
                        return None
                    finally:
                        conn.close()
                except YouTubeRateLimitError as e:
                    print(f"[ERROR] YouTube rate limit hit: {e}")
                    print("[INFO] Skipping this seed track due to YouTube rate limit.")
                    return None
                except Exception as e:
                    print(f"[ERROR] Error finding similar tracks by audio features (DB): {e}")
                    import traceback
                    traceback.print_exc()
                    return None
            # Fetch seed track genres from Last.fm (once, outside the loop)
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
        return None
    except Exception as e:
        print(f"[ERROR] Error finding similar tracks by audio features (DB): {e}")
        import traceback
        traceback.print_exc()
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

def build_artist_list_from_liked_songs(sp, artist_play_map=None, min_liked_songs=3):
    """
    Build fresh artist list from user's current liked songs
    Filters to only include artists with listening activity in last 6 months
    And filters to only artists with at least min_liked_songs liked tracks
    
    Args:
        sp: Spotify client
        artist_play_map: Optional map of artist listening data
        min_liked_songs: Minimum number of liked songs required per artist (default 3)
    
    Returns:
        dict of {artist_id: {name, total_liked, weight}}
    """
    print(f"[INFO] Building artist list from liked songs (minimum {min_liked_songs} liked songs per artist)...")
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
        
        # Filter by minimum liked songs
        if min_liked_songs > 1:
            original_count = len(artist_counts)
            artist_counts = {
                aid: info for aid, info in artist_counts.items()
                if info["total_liked"] >= min_liked_songs
            }
            filtered_count = original_count - len(artist_counts)
            print(f"[INFO] Filtered out {filtered_count} artists with < {min_liked_songs} liked songs")
            print(f"[INFO] {len(artist_counts)} artists remaining")
        
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

def run_enhanced_recommendation_script(sp, output_playlist_id, max_songs=10, lastfm_username=None, max_follower_count=None, min_liked_songs=3, generation_mode='liked_songs', source_url=None, job_id=None, running_jobs=None, enable_genre_matching=False):
    """
    Enhanced recommendation script using:
    1. Existing lottery system to pick artists (or custom source)
    2. Mathematical similarity from database for each winner
    3. Validation and deduplication
    4. Genre matching validation (optional)
    5. Returns list of added songs for display
    
    Args:
        sp: Spotify client
        output_playlist_id: Playlist to add tracks to
        max_songs: Number of songs to add
        lastfm_username: Optional Last.fm username for listening data
        max_follower_count: Maximum artist follower count (None = no limit)
        min_liked_songs: Minimum liked songs per artist (default 3)
        generation_mode: 'liked_songs', 'track', 'artist', or 'playlist'
        source_url: Spotify URL when mode is not 'liked_songs'
    
    Returns:
        {
            'success': bool,
            'tracks_added': int,
            'added_songs': [{title, artist, spotify_url, based_on_artist}],
            'error': str (if failed)
        }
    """
    def update_progress(progress, status_message):
        """Helper to update job progress and status message"""
        if job_id and running_jobs is not None:
            running_jobs[job_id]['progress'] = progress
            running_jobs[job_id]['status_message'] = status_message
            print(f"[PROGRESS] {progress:.1f}% - {status_message}")
    
    try:
        follower_desc = f"max {max_follower_count:,} followers" if max_follower_count else "no follower limit"
        print(f"[INFO] Starting enhanced recommendation run for playlist {output_playlist_id} ({follower_desc})")
        
        # Connect to database
        conn = get_db_connection()
        if not conn:
            return {
                "success": False,
                "error": "Could not connect to audio features database",
                "tracks_added": 0,
                "added_songs": []
            }
        
        # Handle different generation modes
        seed_track_ids = []
        source_description = ""
        
        if generation_mode == 'liked_songs':
            # Original mode: lottery from liked songs
            print(f"[MODE] Running in LIKED SONGS mode")
            update_progress(10, "Analyzing your liked songs...")
            
            # Get listening data for lottery weights
            artist_play_map = {}
            if lastfm_username and LASTFM_API_KEY:
                print("[INFO] Fetching Last.fm recent tracks...")
                recent_tracks = fetch_all_recent_tracks(lastfm_username, LASTFM_API_KEY)
                artist_play_map = build_artist_play_map(recent_tracks)
                print(f"[INFO] Found {len(recent_tracks)} recent tracks, {len(artist_play_map)} unique artists from Last.fm")
            else:
                print("[INFO] No Last.fm username provided. Using Spotify listening data...")
                artist_play_map = fetch_spotify_listening_data(sp)
            
            # Build fresh artist list from current liked songs (with minimum filter)
            print("[INFO] Scanning liked songs to build artist list...")
            update_progress(20, "Building artist list from your library...")
            artists_data = build_artist_list_from_liked_songs(sp, artist_play_map, min_liked_songs)
            
            if not artists_data:
                print("[ERROR] No artists found in liked songs!")
                conn.close()
                return {
                    "success": False,
                    "error": f"No artists found with at least {min_liked_songs} liked songs. Try lowering the minimum liked songs filter.",
                    "tracks_added": 0,
                    "added_songs": []
                }
            
            source_description = "liked songs"
            
        else:
            # Alternative modes: track, artist, album, playlist
            if not source_url:
                conn.close()
                return {
                    "success": False,
                    "error": f"Source URL is required for {generation_mode} mode",
                    "tracks_added": 0,
                    "added_songs": []
                }
            
            try:
                update_progress(10, f"Fetching {generation_mode} data from Spotify...")
                seed_track_ids, source_description = fetch_tracks_from_source(sp, generation_mode, source_url)
                print(f"[MODE] Running in {generation_mode.upper()} mode")
                print(f"[INFO] Discovering recommendations based on {source_description}")
                print(f"[INFO] Found {len(seed_track_ids)} seed tracks from source")
                update_progress(25, f"Found {len(seed_track_ids)} tracks from {source_description}")
                
                if not seed_track_ids:
                    conn.close()
                    return {
                        "success": False,
                        "error": f"No tracks found from {source_description}",
                        "tracks_added": 0,
                        "added_songs": []
                    }
                
                # For alternative modes, we don't need liked songs data
                artists_data = None
                
            except Exception as e:
                conn.close()
                return {
                    "success": False,
                    "error": f"Failed to fetch tracks from source: {str(e)}",
                    "tracks_added": 0,
                    "added_songs": []
                }
        
        # Fetch liked songs for exclusion (always do this to avoid recommending already-liked tracks)
        liked_songs_artist_ids = set()
        liked_track_ids = set()
        print(f"[INFO] Fetching liked track IDs for exclusion...")
        offset = 0
        while True:
            results = safe_spotify_call(sp.current_user_saved_tracks, limit=50, offset=offset)
            if not results or not results.get("items"):
                break
            for item in results["items"]:
                track = item.get("track")
                if track and track.get("id"):
                    liked_track_ids.add(track["id"])
                    for artist in track.get("artists", []):
                        if artist.get("id"):
                            liked_songs_artist_ids.add(artist["id"])
            if len(results["items"]) < 50:
                break
            offset += 50
        print(f"[INFO] Will exclude {len(liked_songs_artist_ids)} artists and {len(liked_track_ids)} tracks from liked songs")
        
        # Get current playlist tracks to avoid duplicates
        playlist_items = []
        playlist_track_ids = set()
        offset = 0
        while True:
            items = safe_spotify_call(sp.playlist_items, output_playlist_id, offset=offset, limit=100)
            if not items or not items.get("items"):
                break
            playlist_items.extend(items["items"])
            for item in items["items"]:
                track = item.get("track")
                if track and track.get("id"):
                    playlist_track_ids.add(track["id"])
            if len(items["items"]) < 100:
                break
            offset += 100
        
        existing_artist_ids = build_existing_artist_ids(playlist_items)
        print(f"[INFO] Found {len(existing_artist_ids)} existing artists in target playlist")
        
        # Prepare seed selection based on mode
        if generation_mode == 'liked_songs':
            # Build weight lists for weighted lottery selection
            artist_ids = list(artists_data.keys())
            artist_weights = [artists_data[aid]["weight"] for aid in artist_ids]
            
            # Pick lottery winners (artists to use as seeds)
            num_winners = max_songs
            rolled_artist_ids = set()
            lottery_winners = []
            
            print(f"\n[LOTTERY] Drawing {num_winners} lottery winners from {len(artist_ids)} artists...")
            for i in range(num_winners * 3):  # Try up to 3x to get enough unique winners
                if len(lottery_winners) >= num_winners:
                    break
                
                available_artists = [aid for aid in artist_ids if aid not in rolled_artist_ids]
                if not available_artists:
                    break
                
                available_weights = [artist_weights[artist_ids.index(aid)] for aid in available_artists]
                selected_aid = random.choices(available_artists, weights=available_weights, k=1)[0]
                rolled_artist_ids.add(selected_aid)
                
                artist_info = artists_data[selected_aid]
                artist_name = artist_info.get("name", "")
                print(f"[LOTTERY] Winner {len(lottery_winners)+1}/{num_winners}: '{artist_name}' (liked {artist_info['total_liked']} songs)")
                lottery_winners.append((selected_aid, artist_name, artist_info))
            
            print(f"\n[INFO] Selected {len(lottery_winners)} lottery winners")
        else:
            # For alternative modes: randomly select from seed_track_ids
            # If more songs requested than available, allow reusing tracks
            lottery_winners = []
            print(f"\n[SEED SELECTION] Randomly selecting {max_songs} seed tracks from {len(seed_track_ids)} available tracks")
            
            for i in range(max_songs):
                # Randomly pick a track (allow repeats if needed)
                selected_track_id = random.choice(seed_track_ids)
                lottery_winners.append(selected_track_id)
                print(f"[SEED] Selection {i+1}/{max_songs}: Track ID {selected_track_id}")
        
        # For each seed, find similar songs using mathematical similarity
        selected_tracks = []
        added_songs = []  # Track details for frontend display
        seen_artist_ids = set(existing_artist_ids)
        all_excluded_track_ids = liked_track_ids | playlist_track_ids
        
        for idx, winner in enumerate(lottery_winners):
            if len(selected_tracks) >= max_songs:
                break
            
            # Update progress (30% to 90% during track discovery)
            current_progress = 30 + (60 * idx / len(lottery_winners))
            
            # Handle different winner formats
            if generation_mode == 'liked_songs':
                winner_aid, winner_name, winner_info = winner
                print(f"\n[SIMILARITY {idx+1}/{len(lottery_winners)}] Finding similar songs for lottery winner: '{winner_name}'")
                update_progress(current_progress, f"Discovering songs similar to {winner_name}...")
                
                # Get a seed track from this artist (from user's liked songs)
                seed_track_id = None
                try:
                    offset = 0
                    while True:
                        results = safe_spotify_call(sp.current_user_saved_tracks, limit=50, offset=offset)
                        if not results or not results.get("items"):
                            break
                        for item in results["items"]:
                            track = item.get("track")
                            if track and track.get("id"):
                                track_artist_ids = {a["id"] for a in track["artists"]}
                                if winner_aid in track_artist_ids:
                                    seed_track_id = track["id"]
                                    print(f"[INFO] Using seed track: {track['name']} by {winner_name}")
                                    break
                        if seed_track_id:
                            break
                        if len(results["items"]) < 50:
                            break
                        offset += 50
                except Exception as e:
                    print(f"[WARN] Error finding seed track: {e}")
                
                if not seed_track_id:
                    print(f"[WARN] Could not find seed track for {winner_name}, skipping")
                    continue
            else:
                # Alternative modes: winner IS the seed track ID
                seed_track_id = winner
                print(f"\n[SIMILARITY {idx+1}/{len(lottery_winners)}] Finding similar songs for seed track: {seed_track_id}")
                update_progress(current_progress, f"Discovering songs from {source_description} ({idx+1}/{len(lottery_winners)})...")
            
            # Ensure seed track is in database (Railway-friendly auto-processing)
            # Retry up to 5 times with different tracks if processing fails
            print(f"[INFO] Checking if seed track is in database...")
            seed_processed = False
            retry_count = 0
            max_retries = 5
            
            while not seed_processed and retry_count < max_retries:
                if ensure_track_in_db(sp, conn, seed_track_id):
                    seed_processed = True
                    break
                else:
                    retry_count += 1
                    print(f"[WARN] Failed to process seed track {seed_track_id} (attempt {retry_count}/{max_retries})")
                    
                    if retry_count < max_retries:
                        if generation_mode == 'liked_songs':
                            # Try to find another track from the same artist
                            print(f"[INFO] Looking for another seed track from {winner_name}...")
                            old_seed = seed_track_id
                            seed_track_id = None
                            
                            try:
                                offset = 0
                                while True:
                                    results = safe_spotify_call(sp.current_user_saved_tracks, limit=50, offset=offset)
                                    if not results or not results.get("items"):
                                        break
                                    for item in results["items"]:
                                        track = item.get("track")
                                        if track and track.get("id") and track["id"] != old_seed:
                                            track_artist_ids = {a["id"] for a in track["artists"]}
                                            if winner_aid in track_artist_ids:
                                                seed_track_id = track["id"]
                                                print(f"[INFO] Trying alternative seed: {track['name']} by {winner_name}")
                                                break
                                    if seed_track_id:
                                        break
                                    if len(results["items"]) < 50:
                                        break
                                    offset += 50
                            except Exception as e:
                                print(f"[WARN] Error finding alternative seed track: {e}")
                            
                            if not seed_track_id:
                                print(f"[WARN] No more alternative tracks available for {winner_name}")
                                break
                        else:
                            # Alternative modes: just pick another random track from seed pool
                            print(f"[INFO] Selecting a different random seed track from source...")
                            old_seed = seed_track_id
                            available_seeds = [tid for tid in seed_track_ids if tid != old_seed]
                            if available_seeds:
                                seed_track_id = random.choice(available_seeds)
                                print(f"[INFO] Trying alternative seed: {seed_track_id}")
                            else:
                                print(f"[WARN] No more alternative tracks available")
                                break
            
            if not seed_processed:
                if generation_mode == 'liked_songs':
                    print(f"[WARN] Failed to process any seed track for {winner_name} after {max_retries} attempts, re-rolling lottery")
                else:
                    print(f"[WARN] Failed to process seed track after {max_retries} attempts, skipping")
                continue
            
            # Get seed track name for display (for alternative modes)
            if generation_mode != 'liked_songs':
                try:
                    seed_track_info = safe_spotify_call(sp.track, seed_track_id)
                    if seed_track_info:
                        winner_name = seed_track_info['artists'][0]['name']
                    else:
                        winner_name = "Unknown Artist"
                except Exception as e:
                    print(f"[WARN] Could not fetch seed track info: {e}")
                    winner_name = "Unknown Artist"
            
            # Get seed artist genres for validation
            seed_genres = get_or_create_artist_genres(sp, conn, winner_name)
            print(f"[INFO] Seed artist '{winner_name}' genres: {seed_genres}")
            
            # Get audio features for seed track from database
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """SELECT tempo_bpm, key_musical, beat_regularity, brightness_hz, treble_hz, 
                           fullness_hz, dynamic_range, percussiveness, loudness, warmth, punch, texture, 
                           energy, danceability, mood_positive, acousticness, instrumental 
                           FROM audio_features WHERE spotify_track_id = %s""",
                        (seed_track_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        # This should not happen since we just ensured it's in the DB
                        print(f"[ERROR] Seed track {seed_track_id} still not in database after processing!")
                        continue
                    
                    features = {
                        'tempo': row[0], 'key_estimate': row[1], 'beat_strength': row[2],
                        'spectral_centroid': row[3], 'spectral_rolloff': row[4], 'spectral_bandwidth': row[5],
                        'spectral_contrast': row[6], 'zero_crossing_rate': row[7], 'rms_energy': row[8],
                        'harmonic_mean': row[9], 'percussive_mean': row[10], 'mfcc_mean': row[11],
                        'energy': row[12], 'danceability': row[13], 'valence': row[14],
                        'acousticness': row[15], 'instrumentalness': row[16]
                    }
            except Exception as e:
                print(f"[ERROR] Database error: {e}")
                continue
            
            # Find similar tracks from database
            similar_tracks = find_most_similar_track_in_db(
                conn, 
                features, 
                liked_track_ids=list(all_excluded_track_ids), 
                max_results=20  # Get multiple candidates
            )
            
            if not similar_tracks:
                print(f"[WARN] No similar tracks found in database for {winner_name}")
                continue
            
            # Validate and select the best candidate
            for candidate in similar_tracks:
                candidate_id = candidate['id']
                
                # Skip if already checked
                if candidate_id in all_excluded_track_ids:
                    continue
                
                # Fetch full track info
                candidate_track = safe_spotify_call(sp.track, candidate_id)
                if not candidate_track:
                    continue
                
                candidate_artist_ids = {a['id'] for a in candidate_track['artists']}
                
                # Validation checks
                # 1. Not from seed artist (only for liked_songs mode)
                if generation_mode == 'liked_songs' and winner_aid in candidate_artist_ids:
                    continue
                
                # 2. Not from liked songs artists (only for liked_songs mode)
                if generation_mode == 'liked_songs' and candidate_artist_ids & liked_songs_artist_ids:
                    continue
                
                # 3. Not already in playlist (artist)
                if candidate_artist_ids & seen_artist_ids:
                    continue
                
                # 4. Check follower count
                if max_follower_count is not None:
                    main_artist_id = candidate_track['artists'][0]['id']
                    main_artist_profile = safe_spotify_call(sp.artist, main_artist_id)
                    if main_artist_profile and 'followers' in main_artist_profile:
                        follower_count = main_artist_profile['followers'].get('total', 0)
                        if follower_count > max_follower_count:
                            continue
                
                # 5. Check genre match (at least 1/3 genres must match) - if enabled
                if enable_genre_matching:
                    candidate_artist_name = candidate_track['artists'][0]['name']
                    candidate_genres = get_or_create_artist_genres(sp, conn, candidate_artist_name)
                    genre_match, matched_genres = check_genre_match(seed_genres, candidate_genres)
                    
                    if not genre_match:
                        print(f"[SKIP] No genre match between '{winner_name}' (genres: {seed_genres}) and '{candidate_artist_name}' (genres: {candidate_genres})")
                        continue
                    else:
                        print(f"[MATCH] Genre match found: {matched_genres}")
                
                # Valid candidate found!
                selected_tracks.append(candidate_track)
                all_excluded_track_ids.add(candidate_id)
                seen_artist_ids.update(candidate_artist_ids)
                
                # Add to display list with seed artist info
                added_songs.append({
                    'title': candidate_track['name'],
                    'artist': ', '.join([a['name'] for a in candidate_track['artists']]),
                    'spotify_url': candidate_track['external_urls']['spotify'],
                    'based_on_artist': winner_name
                })
                
                print(f"[SUCCESS] ✓ Selected: {candidate_track['name']} by {candidate_track['artists'][0]['name']} (based on {winner_name}, distance: {candidate['similarity_distance']:.4f})")
                break
        
        conn.close()
        
        # Add all selected tracks to playlist
        if selected_tracks:
            print(f"\n[INFO] Adding {len(selected_tracks)} tracks to playlist...")
            update_progress(90, f"Adding {len(selected_tracks)} tracks to your playlist...")
            track_uris = [track["uri"] for track in selected_tracks]
            try:
                result = safe_spotify_call(sp.playlist_add_items, output_playlist_id, track_uris)
                if result:
                    print(f"[SUCCESS] Added {len(selected_tracks)} new tracks to playlist")
                    update_progress(100, f"Complete! Added {len(selected_tracks)} new tracks")
                else:
                    print("[ERROR] Failed to add tracks to playlist")
            except Exception as e:
                print(f"[ERROR] Error adding tracks to playlist: {e}")
        else:
            print("[WARNING] No tracks were selected")
            update_progress(100, "Complete! No new tracks added")
        
        return {
            "success": True,
            "tracks_added": len(selected_tracks),
            "added_songs": added_songs,
            "playlist_id": output_playlist_id
        }
        
    except Exception as e:
        print(f"[FATAL ERROR] Enhanced recommendation script failed: {e}")
        import traceback
        traceback.print_exc()
        if 'conn' in locals() and conn:
            conn.close()
        return {
            "success": False,
            "error": str(e),
            "tracks_added": 0,
            "added_songs": []
        }

# For testing purposes
if __name__ == "__main__":
    # This would be used for standalone testing
    print("Lite script loaded successfully")