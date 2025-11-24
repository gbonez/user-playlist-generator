#!/usr/bin/env python3
"""
Audio Features Database Builder - YouTube + Librosa Version

This script:
1. Fetches your liked songs from Spotify (for metadata)
2. Searches for each track on YouTube
3. Downloads audio temporarily
4. Analyzes audio with librosa to extract features
5. IMMEDIATELY deletes the audio file
6. Stores features in PostgreSQL database

No audio files are kept on disk!

IMPORTANT: This script uses Chrome browser cookies for YouTube authentication
to access age-restricted videos. Make sure:
- You have Chrome installed
- You are logged into YouTube in Chrome
- Chrome is closed before running this script (or use --cookiesfrombrowser chrome:~/.config/chromium)
"""

import os
import sys
import json
import time
import tempfile
import psycopg2
from psycopg2.extras import execute_values
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Check for required libraries
try:
    import librosa
    import numpy as np
except ImportError:
    print("[ERROR] Required libraries not installed!")
    print("\nPlease install:")
    print("  pip install librosa soundfile")
    print("\nOptional for better performance:")
    print("  pip install numba")
    sys.exit(1)

try:
    import yt_dlp
except ImportError:
    print("[ERROR] yt-dlp not installed!")
    print("\nPlease install:")
    print("  pip install yt-dlp")
    sys.exit(1)


# Custom exception for YouTube rate limiting
class YouTubeRateLimitError(Exception):
    """Raised when YouTube rate limit is detected"""
    pass


# Load secrets
def load_secrets():
    """Load Spotify credentials from secrets.json or environment"""
    secrets_paths = [
        'secrets.json',
        '../secrets.json',
        os.path.join(os.path.dirname(__file__), '..', 'secrets.json')
    ]
    
    for path in secrets_paths:
        if os.path.exists(path):
            print(f"[INFO] Loading secrets from {path}")
            with open(path, 'r') as f:
                return json.load(f)
    
    print("[INFO] Loading secrets from environment variables")
    return {
        'SPOTIFY_CLIENT_ID': os.environ.get('SPOTIFY_CLIENT_ID'),
        'SPOTIFY_CLIENT_SECRET': os.environ.get('SPOTIFY_CLIENT_SECRET'),
        'BASE_URL': os.environ.get('BASE_URL', 'http://localhost:5001'),
        'DATABASE_URL': os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PUBLIC_URL')
    }

secrets = load_secrets()

# Validate required secrets
required_keys = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'DATABASE_URL']
missing_keys = [key for key in required_keys if not secrets.get(key)]
if missing_keys:
    print(f"[ERROR] Missing required secrets: {', '.join(missing_keys)}")
    sys.exit(1)

scope = "user-library-read"


def create_spotify_client():
    """Create authenticated Spotify client"""
    auth_manager = SpotifyOAuth(
        client_id=secrets['SPOTIFY_CLIENT_ID'],
        client_secret=secrets['SPOTIFY_CLIENT_SECRET'],
        redirect_uri=f"{secrets['BASE_URL']}/callback",
        scope=scope,
        cache_path=".spotify_youtube_cache"
    )
    
    return Spotify(auth_manager=auth_manager)


def get_db_connection():
    """Get Postgres database connection"""
    try:
        db_url = secrets.get('DATABASE_PUBLIC_URL') or secrets.get('DATABASE_URL')
        
        if not db_url:
            print("[ERROR] No DATABASE_URL found in secrets")
            sys.exit(1)
        
        print(f"[INFO] Connecting to database: {db_url.split('@')[1] if '@' in db_url else 'configured'}")
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"[ERROR] Failed to connect to database: {e}")
        sys.exit(1)


def create_table(conn):
    """Create audio_features table with ALL individual data points and descriptive names"""
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS audio_features (
        id SERIAL PRIMARY KEY,
        spotify_track_id VARCHAR(50) UNIQUE NOT NULL,
        artist_name VARCHAR(255) NOT NULL,
        track_name VARCHAR(255) NOT NULL,
        
        -- Rhythm Features (timing and tempo)
        tempo_bpm REAL,                      -- Speed: beats per minute
        key_musical SMALLINT,                -- Musical key (0=C, 1=C#, etc.)
        beat_regularity REAL,                -- Rhythm consistency (0-1)
        
        -- Spectral Features (frequency/brightness/timbre)
        brightness_hz REAL,                  -- Spectral centroid: higher = brighter sound
        treble_hz REAL,                      -- Spectral rolloff: high frequency content
        fullness_hz REAL,                    -- Spectral bandwidth: width of sound
        dynamic_range REAL,                  -- Spectral contrast: peaks vs valleys
        
        -- Temporal Features (time-domain characteristics)
        percussiveness REAL,                 -- Zero crossing rate: sharp transients
        loudness REAL,                       -- RMS energy: overall volume
        
        -- Harmonic/Percussive (tonal vs rhythmic)
        warmth REAL,                         -- Harmonic content: melodic/tonal
        punch REAL,                          -- Percussive content: drums/rhythm
        
        -- Timbral Features (texture/color)
        texture REAL,                        -- MFCC mean: timbre signature
        
        -- Computed Spotify-like Features (0-1 normalized)
        energy REAL,                         -- Intensity: how energetic/intense
        danceability REAL,                   -- Groove: how danceable/rhythmic
        mood_positive REAL,                  -- Valence: happy/bright vs sad/dark
        acousticness REAL,                   -- Acoustic: unplugged vs electric
        instrumental REAL,                   -- Instrumental: less vocals = higher
        
        -- Metadata
        popularity SMALLINT,
        spotify_uri VARCHAR(100),
        youtube_match VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        CONSTRAINT unique_track UNIQUE(spotify_track_id)
    );
    
    -- Create indexes for faster queries
    CREATE INDEX IF NOT EXISTS idx_artist_name ON audio_features(artist_name);
    CREATE INDEX IF NOT EXISTS idx_track_name ON audio_features(track_name);
    CREATE INDEX IF NOT EXISTS idx_tempo_bpm ON audio_features(tempo_bpm);
    CREATE INDEX IF NOT EXISTS idx_energy ON audio_features(energy);
    CREATE INDEX IF NOT EXISTS idx_danceability ON audio_features(danceability);
    CREATE INDEX IF NOT EXISTS idx_mood_positive ON audio_features(mood_positive);
    """
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
            conn.commit()
            print("✅ Table 'audio_features' created/verified successfully")
            
            cursor.execute("SELECT COUNT(*) FROM audio_features")
            count = cursor.fetchone()[0]
            print(f"📊 Current database contains {count} tracks")
            
    except Exception as e:
        print(f"[ERROR] Failed to create table: {e}")
        conn.rollback()
        sys.exit(1)


def fetch_liked_songs(sp, limit=None):
    """
    Fetch liked songs from Spotify in batches (single-threaded with rate limiting).
    If limit is None, fetches ALL liked songs.
    """
    if limit is None:
        print(f"\n📀 Fetching ALL liked songs from Spotify (in batches of 50)...")
    else:
        print(f"\n📀 Fetching {limit} liked songs from Spotify...")
    
    tracks_data = []
    offset = 0
    batch_size = 50
    
    while True:
        # Determine how many to fetch in this batch
        if limit is None:
            fetch_size = batch_size
        else:
            remaining = limit - len(tracks_data)
            if remaining <= 0:
                break
            fetch_size = min(batch_size, remaining)
        
        try:
            results = sp.current_user_saved_tracks(limit=fetch_size, offset=offset)
            
            # Sleep briefly to respect rate limits (Spotify allows ~180 requests/minute)
            import random
            time.sleep(random.uniform(0.15, 0.5))
            
        except Exception as e:
            print(f"[ERROR] Failed to fetch tracks at offset {offset}: {e}")
            print(f"   Retrying in 2 seconds...")
            time.sleep(2)
            continue
        
        if not results or not results.get('items'):
            break
        
        for item in results['items']:
            track = item.get('track')
            if not track or not track.get('id'):
                continue
            
            tracks_data.append({
                'id': track['id'],
                'name': track['name'],
                'artists': [a['name'] for a in track.get('artists', [])],
                'uri': track['uri'],
                'popularity': track.get('popularity', 0)
            })
        
        offset += fetch_size
        
        if limit is None:
            print(f"   Fetched {len(tracks_data)} tracks so far...")
        else:
            print(f"   Fetched {len(tracks_data)}/{limit} tracks...")
        
        # If we got fewer results than requested, we've reached the end
        if len(results['items']) < fetch_size:
            break
    
    print(f"✅ Fetched {len(tracks_data)} tracks total")
    return tracks_data


def normalize_string(s):
    """Normalize string for fuzzy matching (lowercase, remove special chars)"""
    import re
    s = s.lower()
    # Remove common variations
    s = re.sub(r'\bfeat\.?\b|\bft\.?\b|\bfeature\b', 'feat', s)
    # Remove special characters but keep spaces
    s = re.sub(r'[^\w\s]', '', s)
    # Collapse multiple spaces
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def video_matches_track(video_title, track_name, artist_name, uploader_name=None, is_fallback=False):
    """
    Two-tier validation:
    1. PRIMARY (strict): Title contains BOTH track name AND artist name
    2. FALLBACK (flexible): Title contains track name + channel name matches artist
    
    Returns: (matches: bool, match_type: str)
    """
    video_normalized = normalize_string(video_title)
    track_normalized = normalize_string(track_name)
    artist_normalized = normalize_string(artist_name)
    
    # Split artist name to handle multi-artist tracks (e.g., "Artist1, Artist2")
    artist_parts = [normalize_string(a.strip()) for a in artist_name.split(',')]
    
    # Check if track name is in video title
    track_match = track_normalized in video_normalized
    
    # PRIMARY CHECK: Track name + artist name both in title (STRICT)
    if not is_fallback:
        artist_match = any(artist_part in video_normalized for artist_part in artist_parts)
        if track_match and artist_match:
            return True, "primary"
    
    # FALLBACK CHECK: Track name in title + artist name in channel (FLEXIBLE)
    if track_match and uploader_name:
        uploader_normalized = normalize_string(uploader_name)
        # Check if any artist part is in the channel name (more flexible matching)
        for artist_part in artist_parts:
            # Check for artist in channel name
            if artist_part in uploader_normalized:
                return True, "fallback-channel"
            # Also check if channel name is in artist (for partial matches)
            if len(artist_part) > 3 and uploader_normalized and len(uploader_normalized) > 3:
                # Check overlap between artist and channel
                artist_words = set(artist_part.split())
                channel_words = set(uploader_normalized.split())
                # If they share at least 1 significant word (>3 chars), it's likely a match
                common_words = artist_words & channel_words
                significant_common = [w for w in common_words if len(w) > 3]
                if significant_common:
                    return True, "fallback-partial"
    
    return False, None


def search_youtube(track_name, artist_name, max_results=10, thread_name=None, track_num=None):
    """
    Search YouTube for track with two-tier validation:
    1. First search: '{artist} {track} audio' - targets audio uploads
    2. Second search (if first fails): '{artist} {track}' - broader search
    For each search, tries strict then flexible matching
    """
    # Create prefix for logging
    prefix = f"[{thread_name}][{track_num}]" if thread_name and track_num else ""
    
    firefox_profile = '7hkeppud.default-release-1'  # Change if your profile name changes
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': f'ytsearch{max_results}',
        'cookiesfrombrowser': ('firefox', firefox_profile),  # Use Firefox cookies for authentication
    }
    
    # Try two different search queries
    search_queries = [
        f'{artist_name} {track_name} audio',  # First: with "audio"
        f'{artist_name} {track_name}'         # Second: without "audio"
    ]
    
    for query_idx, query in enumerate(search_queries, 1):
        # Add delay before EVERY YouTube search to relax API pressure
        time.sleep(0.15)
        
        # Add additional delay between searches to avoid bot detection
        if query_idx > 1:
            time.sleep(2)  # Wait 2 seconds before second search
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                
                if result and 'entries' in result and len(result['entries']) > 0:
                    videos = result['entries']
                    
                    if query_idx == 1:
                        print(f"   {prefix} 🔍 Found {len(videos)} videos, checking each...")
                    else:
                        print(f"   {prefix} � Trying broader search (query #{query_idx})...")
                        print(f"   {prefix} �🔍 Found {len(videos)} videos, checking each...")
                    
                    # FIRST PASS: Try strict matching (track + artist both in title)
                    for i, video in enumerate(videos, 1):
                        video_title = video.get('title', '')
                        video_id = video.get('id')
                        uploader = video.get('uploader', '') or video.get('channel', '')
                        
                        if not video_id or not video_title:
                            continue
                        
                        print(f"   {prefix} [{i}/{max_results}] '{video_title[:50]}{'...' if len(video_title) > 50 else ''}' by {uploader[:25]}")
                        
                        # Try strict match first
                        matches, match_type = video_matches_track(video_title, track_name, artist_name, uploader_name=uploader, is_fallback=False)
                        if matches:
                            print(f"   {prefix} ✓ MATCH (strict - query #{query_idx})! Using this video")
                            return video_id, video_title
                    
                    # SECOND PASS: Try flexible matching (track in title + artist in channel)
                    print(f"   {prefix} 🔄 No strict match, trying flexible matching...")
                    for i, video in enumerate(videos, 1):
                        video_title = video.get('title', '')
                        video_id = video.get('id')
                        uploader = video.get('uploader', '') or video.get('channel', '')
                        
                        if not video_id or not video_title:
                            continue
                        
                        # Try flexible match
                        matches, match_type = video_matches_track(video_title, track_name, artist_name, uploader_name=uploader, is_fallback=True)
                        if matches:
                            print(f"   {prefix} ✓ MATCH (flexible - {match_type}, query #{query_idx})! Using video #{i}")
                            return video_id, video_title
                    
                    print(f"   {prefix} ❌ No matches in query #{query_idx}")
                    
        except Exception as e:
            error_msg = str(e).lower()
            # Detect YouTube rate limiting errors (but NOT age-restriction errors)
            if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429', 'quota exceeded', 'bot detection']) and 'sign in to confirm' not in error_msg:
                print(f"   {prefix} 🚫 YOUTUBE RATE LIMIT DETECTED!")
                print(f"   {prefix} Error: {e}")
                raise YouTubeRateLimitError(f"YouTube rate limit reached during search: {e}")
            print(f"   {prefix} [ERROR] YouTube search #{query_idx} failed: {e}")
    
    # If both searches fail
    print(f"   {prefix} ❌ No matches found (tried 2 different search queries)")
    return None, None


def download_and_analyze_audio(video_id, track_name, artist_name):
    """
    Download audio temporarily, analyze with librosa, delete immediately.
    Returns extracted features dict.
    """
    if not video_id:
        return None
    
    # Create temporary file
    temp_file = None
    
    # Track all possible temp files for cleanup
    temp_files = []
    
    try:
        # Random delay before download to appear more human-like and avoid bot detection
        import random
        delay = random.uniform(0.01, 0.25)
        time.sleep(delay)
        
        # Create a temp directory for this download
        temp_dir = tempfile.mkdtemp()
        temp_files.append(temp_dir)
        temp_file = os.path.join(temp_dir, 'audio')
        
        firefox_profile = '7hkeppud.default-release-1'  # Change if your profile name changes
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': ('firefox', firefox_profile),  # Use Firefox cookies for authentication
        }
        
        # Download
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                downloaded_file = ydl.prepare_filename(info)
        except Exception as download_error:
            error_msg = str(download_error).lower()
            # Detect YouTube rate limiting errors (but NOT age-restriction errors)
            if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429', 'quota exceeded', 'bot detection']) and 'sign in to confirm' not in error_msg:
                print(f"   🚫 YOUTUBE RATE LIMIT DETECTED!")
                print(f"   Error: {download_error}")
                raise YouTubeRateLimitError(f"YouTube rate limit reached during download: {download_error}")
            raise  # Re-raise other exceptions
        
        # Find the downloaded file
        temp_file_converted = downloaded_file
        temp_files.append(temp_file_converted)
        
        # Load and analyze audio with librosa (suppress warnings)
        # Analyze the middle portion of the song for best representation
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Get total duration
            duration_full = librosa.get_duration(path=temp_file_converted)
            
            # For songs <= 2 minutes: analyze middle 30 seconds
            # For songs > 2 minutes: analyze middle 60 seconds
            if duration_full <= 120:
                duration = 30.0
                offset = max(0, (duration_full - duration) / 2)  # Center the 30s window
            else:
                duration = 60.0
                offset = max(0, (duration_full - duration) / 2)  # Center the 60s window
            
            y, sr = librosa.load(temp_file_converted, offset=offset, duration=duration)
        
        # Extract features
        print(f"   🎵 Analyzing...", end='', flush=True)
        features = extract_audio_features(y, sr)
        print(f" Done!")
        
        return features
        
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return None
        
    finally:
        # ALWAYS delete all temporary files
        import shutil
        for temp_item in temp_files:
            if temp_item and os.path.exists(temp_item):
                try:
                    if os.path.isdir(temp_item):
                        shutil.rmtree(temp_item)
                    else:
                        os.remove(temp_item)
                except Exception:
                    pass  # Silently fail on cleanup


def extract_audio_features(y, sr):
    """Extract comprehensive audio features using librosa"""
    features = {}
    
    try:
        # === TEMPO & RHYTHM ===
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        features['tempo'] = float(tempo)
        
        # === SPECTRAL FEATURES (Timbre/Brightness) ===
        spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        features['spectral_centroid'] = float(np.mean(spectral_centroids))
        
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        features['spectral_rolloff'] = float(np.mean(spectral_rolloff))
        
        spectral_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
        features['spectral_bandwidth'] = float(np.mean(spectral_bandwidth))
        
        spectral_contrast = librosa.feature.spectral_contrast(y=y, sr=sr)[0]
        features['spectral_contrast'] = float(np.mean(spectral_contrast))
        
        # === TEMPORAL FEATURES ===
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        features['zero_crossing_rate'] = float(np.mean(zcr))
        
        rms = librosa.feature.rms(y=y)[0]
        features['rms_energy'] = float(np.mean(rms))
        
        # === HARMONIC/PERCUSSIVE SEPARATION ===
        y_harmonic, y_percussive = librosa.effects.hpss(y)
        features['harmonic_mean'] = float(np.mean(np.abs(y_harmonic)))
        features['percussive_mean'] = float(np.mean(np.abs(y_percussive)))
        
        # === TIMBRAL FEATURES (MFCCs) ===
        mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        features['mfcc_mean'] = float(np.mean(mfccs))
        
        # === KEY ESTIMATION ===
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_estimate = int(np.argmax(np.sum(chroma, axis=1)))
        features['key_estimate'] = key_estimate
        
        # === BEAT ANALYSIS ===
        # Convert beat frames to times for better analysis
        beat_times = librosa.frames_to_time(beats, sr=sr)
        if len(beat_times) > 1:
            beat_intervals = np.diff(beat_times)
            features['beat_strength'] = float(1.0 - min(0.99, np.std(beat_intervals) / np.mean(beat_intervals)))  # regularity
        else:
            features['beat_strength'] = 0.5
        
        # === SPOTIFY-LIKE COMPUTED FEATURES (0-1 scale) ===
        
        # Energy: combination of RMS and spectral features
        energy_raw = features['rms_energy'] * 2.5
        features['energy'] = float(min(1.0, max(0.0, energy_raw)))
        
        # Danceability: tempo regularity + beat strength + percussiveness
        tempo_score = min(1.0, features['tempo'] / 140.0)
        percussive_ratio = features['percussive_mean'] / (features['harmonic_mean'] + features['percussive_mean'] + 0.0001)
        features['danceability'] = float(tempo_score * 0.5 + features['beat_strength'] * 0.3 + percussive_ratio * 0.2)
        features['danceability'] = min(1.0, max(0.0, features['danceability']))
        
        # Valence: mood approximation from spectral brightness and energy
        valence_estimate = (features['spectral_centroid'] / 4000.0) * 0.6 + features['energy'] * 0.4
        features['valence'] = float(min(1.0, max(0.0, valence_estimate)))
        
        # Acousticness: inverse of energy + harmonic content
        harmonic_ratio = features['harmonic_mean'] / (features['harmonic_mean'] + features['percussive_mean'] + 0.0001)
        acousticness_estimate = harmonic_ratio * 0.7 + (1 - features['energy']) * 0.3
        features['acousticness'] = float(min(1.0, max(0.0, acousticness_estimate)))
        
        # Instrumentalness: inverse of spectral complexity (vocals are complex)
        complexity = features['spectral_contrast'] / 40.0  # Normalize
        features['instrumentalness'] = float(min(1.0, max(0.0, 1.0 - complexity)))
        
    except Exception as e:
        print(f"      [ERROR] Feature extraction failed: {e}")
        return None
    
    return features


def insert_track_to_db(conn, track, features, youtube_title):
    """Insert single track with ALL individual features (descriptive column names, 6 decimal precision)"""
    
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
    ON CONFLICT (spotify_track_id) DO UPDATE SET
        tempo_bpm = EXCLUDED.tempo_bpm,
        key_musical = EXCLUDED.key_musical,
        beat_regularity = EXCLUDED.beat_regularity,
        brightness_hz = EXCLUDED.brightness_hz,
        treble_hz = EXCLUDED.treble_hz,
        fullness_hz = EXCLUDED.fullness_hz,
        dynamic_range = EXCLUDED.dynamic_range,
        percussiveness = EXCLUDED.percussiveness,
        loudness = EXCLUDED.loudness,
        warmth = EXCLUDED.warmth,
        punch = EXCLUDED.punch,
        texture = EXCLUDED.texture,
        energy = EXCLUDED.energy,
        danceability = EXCLUDED.danceability,
        mood_positive = EXCLUDED.mood_positive,
        acousticness = EXCLUDED.acousticness,
        instrumental = EXCLUDED.instrumental,
        popularity = EXCLUDED.popularity,
        youtube_match = EXCLUDED.youtube_match
    """
    
    try:
        with conn.cursor() as cursor:
            # Map features to descriptive column names with 6 decimal precision
            cursor.execute(insert_sql, (
                track['id'],
                ', '.join(track['artists'][:2]),
                track['name'],
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
                track['popularity'],
                track['uri'],
                youtube_title
            ))
            conn.commit()
            return True
    except Exception as e:
        print(f"      [ERROR] Failed to insert track: {e}")
        conn.rollback()
        return False


def process_single_track(track, track_num, total_tracks, conn, db_lock, print_lock, thread_id, overwrite=False):
    """
    Process a single track (thread-safe).
    Returns: (success: bool, skipped: bool)
    """
    import threading
    thread_name = f"T{thread_id}"
    artist_str = ', '.join(track['artists'][:2])
    
    with print_lock:
        print(f"\n[{thread_name}][{track_num}/{total_tracks}] {track['name']} - {artist_str}")
    
    try:
        # Check if already in database FIRST (before any API calls)
        # BUT: If entry has NULL columns (incomplete), we MUST re-process it
        if not overwrite:
            # Small delay before database check to avoid hammering the DB
            time.sleep(0.1)
            
            with db_lock:
                cursor = conn.cursor()
                # Check if exists AND has complete data (no NULL in critical columns)
                cursor.execute("""
                    SELECT COUNT(*) FROM audio_features 
                    WHERE spotify_track_id = %s 
                    AND tempo_bpm IS NOT NULL 
                    AND brightness_hz IS NOT NULL 
                    AND warmth IS NOT NULL
                """, (track['id'],))
                exists_and_complete = cursor.fetchone()[0] > 0
                cursor.close()
            
            if exists_and_complete:
                with print_lock:
                    print(f"   [{thread_name}][{track_num}] ⏭️  Already in database (complete), skipping")
                    time.sleep(.1)
                return False, True
            elif not exists_and_complete:
                # Check if it exists at all (might be incomplete)
                with db_lock:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM audio_features WHERE spotify_track_id = %s",
                        (track['id'],)
                    )
                    exists_incomplete = cursor.fetchone()[0] > 0
                    cursor.close()
                
                if exists_incomplete:
                    with print_lock:
                        print(f"   [{thread_name}][{track_num}] 🔄 Found incomplete entry, re-processing...")
                # Will continue to re-process
        
        # Search YouTube (validates artist + track in title, tries up to 5 videos)
        import random
        time.sleep(random.uniform(0.2, 0.5))
        video_id, youtube_title = search_youtube(track['name'], ', '.join(track['artists']), thread_name=thread_name, track_num=track_num)
        
        if not video_id:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ❌ No verified YouTube match found (checked 5 videos)")
            return False, True
        
        with print_lock:
            print(f"   [{thread_name}][{track_num}] ✓ Verified: {youtube_title}")
        
        # Download and analyze
        features = download_and_analyze_audio(video_id, track['name'], ', '.join(track['artists']))
        
        if not features:
            return False, True
        
        # Save to database (thread-safe with lock)
        with db_lock:
            success = insert_track_to_db(conn, track, features, youtube_title)
        
        if success:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ✅ Saved! Tempo: {features['tempo']:.1f}bpm | Energy: {features['energy']:.6f} | Dance: {features['danceability']:.6f} | Valence: {features['valence']:.6f}")
            return True, False
        else:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ❌ Database save failed")
            return False, True
            
    except YouTubeRateLimitError:
        # Re-raise rate limit errors to stop the entire program
        raise
    except Exception as e:
        with print_lock:
            print(f"   [{thread_name}][{track_num}] ❌ Error: {e}")
        return False, True
    finally:
        # Longer delay to avoid YouTube bot detection (especially important with multiple searches)
        time.sleep(3)


def build_audio_features_database(max_tracks=50, num_threads=4, overwrite=False):
    """
    Main function to build the audio features database with multithreading.
    
    Args:
        max_tracks: Number of tracks to process (None = all)
        num_threads: Number of parallel threads for processing (default: 4)
        overwrite: If True, overwrite existing tracks; if False, skip existing (default: False)
    """
    import datetime
    start_time = time.time()
    
    print("=" * 60)
    print("🎵 AUDIO FEATURES DATABASE BUILDER (YouTube + Librosa)")
    print("=" * 60)
    print()
    print("ℹ️  Note: Audio files are downloaded temporarily and deleted")
    print("   immediately after analysis. Nothing is kept on disk!")
    print()
    
    # 1. Connect to Spotify
    print("🔐 Authenticating with Spotify...")
    sp = create_spotify_client()
    user = sp.current_user()
    print(f"   ✅ Logged in as: {user['display_name']}")
    print()
    
    # 2. Connect to Database
    print("🗄️  Connecting to Postgres database...")
    conn = get_db_connection()
    print("   ✅ Database connected")
    print()
    
    # 3. Create table (only if not exists)
    print("📋 Verifying audio_features table exists...")
    create_table(conn)
    print()
    
    # 4. Fetch ALL liked songs (single-threaded with rate limiting)
    tracks = fetch_liked_songs(sp, limit=max_tracks)
    
    if not tracks:
        print("[ERROR] No tracks fetched!")
        return
    
    print()
    
    # 5. Process tracks with multithreading
    print(f"🎼 Processing {len(tracks)} tracks with {num_threads} parallel threads...")
    print()
    
    processed_count = 0
    skipped_count = 0
    
    # Thread-safe locks
    db_lock = Lock()
    print_lock = Lock()
    
    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        # Submit all tasks with thread IDs
        future_to_track = {}
        for i, track in enumerate(tracks, 1):
            # Assign thread ID based on order (cycles through 1 to num_threads)
            thread_id = ((i - 1) % num_threads) + 1
            future = executor.submit(
                process_single_track, 
                track, 
                i, 
                len(tracks), 
                conn, 
                db_lock, 
                print_lock,
                thread_id,
                overwrite
            )
            future_to_track[future] = (i, track)
        
        # Collect results as they complete
        rate_limit_hit = False
        for future in as_completed(future_to_track):
            track_num, track = future_to_track[future]
            try:
                success, skipped = future.result()
                if success:
                    processed_count += 1
                if skipped:
                    skipped_count += 1
            except YouTubeRateLimitError as e:
                with print_lock:
                    print(f"\n{'='*60}")
                    print(f"🚫 YOUTUBE RATE LIMIT DETECTED - STOPPING ALL THREADS")
                    print(f"{'='*60}")
                    print(f"Error: {e}")
                    print(f"Processed so far: {processed_count} tracks")
                    print(f"{'='*60}\n")
                rate_limit_hit = True
                # Cancel remaining futures
                for f in future_to_track:
                    f.cancel()
                break
            except Exception as e:
                with print_lock:
                    print(f"   [ERROR] Thread for track {track_num} failed: {e}")
                skipped_count += 1
        
        # If rate limit was hit, exit after cleaning up
        if rate_limit_hit:
            conn.close()
            print("\n⚠️  Exiting due to YouTube rate limit.")
            print("💡 Please wait a while before running the script again.")
            sys.exit(1)
    
    # Calculate runtime
    end_time = time.time()
    runtime_seconds = end_time - start_time
    runtime_minutes = runtime_seconds / 60
    runtime_hours = runtime_minutes / 60
    
    # Format runtime
    if runtime_hours >= 1:
        runtime_str = f"{runtime_hours:.2f} hours"
    elif runtime_minutes >= 1:
        runtime_str = f"{runtime_minutes:.2f} minutes"
    else:
        runtime_str = f"{runtime_seconds:.2f} seconds"
    
    # Summary
    print()
    print("=" * 60)
    print("� FINAL SUMMARY")
    print("=" * 60)
    print(f"⏱️  Total Runtime: {runtime_str}")
    print(f"🧵 Threads Used: {num_threads}")
    print(f"🔄 Overwrite Mode: {'ON (replacing existing)' if overwrite else 'OFF (skipping existing)'}")
    print(f"✅ Successfully Processed: {processed_count} tracks")
    print(f"⚠️  Not Processed/Skipped: {skipped_count} tracks")
    print(f"� Total Tracks Attempted: {len(tracks)} tracks")
    print("=" * 60)
    print()
    print("✨ Database ready for similarity matching!")
    print()
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Build audio features database using YouTube + Librosa (with multithreading)')
    parser.add_argument('--tracks', type=int, default=100, help='Number of tracks to process (default: 100, use 0 for ALL tracks)')
    parser.add_argument('--threads', type=int, default=9, help='Number of parallel threads (default: 9)')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing tracks in database (default: skip existing)')
    args = parser.parse_args()
    
    # Convert 0 to None for "all tracks"
    max_tracks = None if args.tracks == 0 else args.tracks
    
    try:
        build_audio_features_database(max_tracks=max_tracks, num_threads=args.threads, overwrite=args.overwrite)
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
