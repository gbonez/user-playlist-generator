#!/usr/bin/env python3
"""
Audio Features Database Builder - YouTube Playlist Version

This script:
1. Fetches all videos from a YouTube playlist
2. Searches for each track on Spotify to verify it exists
3. Downloads audio temporarily from YouTube
4. Analyzes audio with librosa to extract features
5. IMMEDIATELY deletes the audio file
6. Stores features in PostgreSQL database

No audio files are kept on disk!

IMPORTANT: This script uses browser cookies for YouTube authentication
to access age-restricted videos. It tries these browsers in order:
Chrome → Firefox → Brave → Edge → Safari → Chromium

Make sure:
- You have at least one of these browsers installed
- You are logged into YouTube in that browser
- The browser is closed before running this script
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
import re

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
        cache_path=".spotify_youtube_playlist_cache"
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


def extract_playlist_or_channel_id(url_or_id):
    """
    Extract playlist ID or channel info from various YouTube URL formats
    Returns: (type, id) where type is 'playlist', 'channel', or 'user'
    """
    # If it's already just a playlist ID
    if len(url_or_id) == 34 and url_or_id.startswith('PL'):
        return ('playlist', url_or_id)
    
    # Check for playlist patterns
    playlist_patterns = [
        r'list=([a-zA-Z0-9_-]+)',
        r'playlist\?list=([a-zA-Z0-9_-]+)',
    ]
    
    for pattern in playlist_patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return ('playlist', match.group(1))
    
    # Check for channel patterns
    channel_patterns = [
        (r'youtube\.com/channel/([a-zA-Z0-9_-]+)', 'channel'),
        (r'youtube\.com/c/([a-zA-Z0-9_-]+)', 'channel'),
        (r'youtube\.com/@([a-zA-Z0-9_-]+)', 'channel'),
        (r'youtube\.com/user/([a-zA-Z0-9_-]+)', 'user'),
    ]
    
    for pattern, channel_type in channel_patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return (channel_type, match.group(1))
    
    # If no match, assume it's a playlist ID
    return ('playlist', url_or_id)


def fetch_youtube_videos(source_type, source_id):
    """
    Fetch all videos from a YouTube playlist or channel.
    Returns list of dicts with video info.
    Skips private, deleted, or unavailable videos automatically.
    
    Args:
        source_type: 'playlist', 'channel', or 'user'
        source_id: The playlist ID, channel ID, or username
    """
    if source_type == 'playlist':
        print(f"\n📺 Fetching YouTube playlist: {source_id}")
        url = f"https://www.youtube.com/playlist?list={source_id}"
    elif source_type == 'channel':
        print(f"\n📺 Fetching YouTube channel: {source_id}")
        # Try different channel URL formats
        if source_id.startswith('@'):
            url = f"https://www.youtube.com/{source_id}/videos"
        elif source_id.startswith('UC'):
            url = f"https://www.youtube.com/channel/{source_id}/videos"
        else:
            url = f"https://www.youtube.com/c/{source_id}/videos"
    elif source_type == 'user':
        print(f"\n📺 Fetching YouTube user channel: {source_id}")
        url = f"https://www.youtube.com/user/{source_id}/videos"
    else:
        print(f"[ERROR] Unknown source type: {source_type}")
        return []
    
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,  # Continue on download errors (private/deleted videos)
    }
    
    # Try to add browser cookies for age-restricted content
    # Try multiple browsers in order of preference
    for browser in ['chrome', 'firefox', 'brave', 'edge', 'safari', 'chromium']:
        try:
            ydl_opts['cookiesfrombrowser'] = (browser,)
            break  # If successful, use this browser
        except:
            continue  # Try next browser
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(url, download=False)
            
            if not result or 'entries' not in result:
                print(f"[ERROR] Could not fetch {source_type} or it is empty")
                return []
            
            videos = []
            skipped_count = 0
            
            for entry in result['entries']:
                # Skip None entries (private/deleted/unavailable videos)
                if not entry:
                    skipped_count += 1
                    continue
                
                # Skip entries without required fields
                if not entry.get('id') or not entry.get('title'):
                    skipped_count += 1
                    continue
                
                videos.append({
                    'id': entry.get('id'),
                    'title': entry.get('title', ''),
                    'duration': entry.get('duration', 0),
                    'uploader': entry.get('uploader', '') or entry.get('channel', '')
                })
            
            if skipped_count > 0:
                print(f"⚠️  Skipped {skipped_count} unavailable videos (private/deleted/restricted)")
            print(f"✅ Found {len(videos)} accessible videos")
            return videos
            
    except Exception as e:
        error_msg = str(e).lower()
        # Detect YouTube rate limiting errors (but NOT age-restriction errors)
        if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429', 'quota exceeded', 'bot detection']) and 'sign in to confirm' not in error_msg:
            print(f"🚫 YOUTUBE RATE LIMIT DETECTED!")
            print(f"Error: {e}")
            raise YouTubeRateLimitError(f"YouTube rate limit reached during fetch: {e}")
        print(f"[ERROR] Failed to fetch {source_type}: {e}")
        return []


def clean_title_for_search(title):
    """
    Clean YouTube video title to extract likely artist and track name.
    Handles common patterns like:
    - "Artist - Track"
    - "Artist - Track (Official Video)"
    - "Track - Artist"
    - "Artist: Track"
    """
    # Remove common suffixes
    title = re.sub(r'\s*\((official|audio|video|lyric|lyrics|music video|visualizer|mv).*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[(official|audio|video|lyric|lyrics|music video|visualizer|mv).*?\]', '', title, flags=re.IGNORECASE)
    
    # Try to split on common separators
    for separator in [' - ', ' – ', ' — ', ': ', ' | ']:
        if separator in title:
            parts = title.split(separator, 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
    
    # If no separator found, return the whole title
    return None, title.strip()


def search_spotify_for_track(sp, artist_hint, track_hint, video_title):
    """
    Search Spotify to verify if a track exists.
    Returns: (track_id, artist_name, track_name, popularity, uri) or None
    """
    # Try multiple search strategies
    search_queries = []
    
    # Strategy 1: If we have artist and track hints
    if artist_hint and track_hint:
        search_queries.append(f"artist:{artist_hint} track:{track_hint}")
        search_queries.append(f"{artist_hint} {track_hint}")
    
    # Strategy 2: Use full video title
    search_queries.append(video_title)
    
    # Strategy 3: Use just track hint if available
    if track_hint and not artist_hint:
        search_queries.append(track_hint)
    
    for query in search_queries:
        try:
            # Add delay before Spotify API call to relax API pressure
            time.sleep(0.1)
            
            results = sp.search(q=query, limit=5, type='track')
            
            if results and results.get('tracks') and results['tracks'].get('items'):
                # Get the top result
                track = results['tracks']['items'][0]
                
                return {
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [a['name'] for a in track.get('artists', [])],
                    'uri': track['uri'],
                    'popularity': track.get('popularity', 0)
                }
            
            # Brief delay to respect rate limits
            time.sleep(0.25)
            
        except Exception as e:
            print(f"      [WARN] Spotify search failed for '{query}': {e}")
            continue
    
    return None


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
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }
        
        # Try to add browser cookies for age-restricted content
        # Try multiple browsers in order of preference
        for browser in ['chrome', 'firefox', 'brave', 'edge', 'safari', 'chromium']:
            try:
                ydl_opts['cookiesfrombrowser'] = (browser,)
                break  # If successful, use this browser
            except:
                continue  # Try next browser
        # If no browser cookies work, continue without them
        
        
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
            
            # Detect age-restricted video errors
            if 'sign in to confirm' in error_msg or 'age' in error_msg:
                print(f"   ⚠️  Age-restricted video - skipping")
                print(f"   💡 TIP: Make sure Chrome is CLOSED so yt-dlp can read your cookies")
                print(f"   💡 If you're logged into YouTube in Chrome, the script should be able to access age-restricted content")
                return None
            
            # Detect geo-restricted video errors (not available in your country)
            if 'not made this video available in your country' in error_msg or 'not available in your country' in error_msg or 'video is available in' in error_msg:
                print(f"   🌍 Video not available in your country - skipping")
                return None
            
            # Detect copyright-removed video errors
            if 'copyright claim' in error_msg or 'copyright' in error_msg and 'no longer available' in error_msg:
                print(f"   ©️  Video removed due to copyright claim - skipping")
                return None
            
            # Detect private/unavailable video errors
            if any(keyword in error_msg for keyword in ['private video', 'video unavailable', 'this video is not available', 'video has been removed', 'members-only', 'join this channel']):
                print(f"   ⚠️  Video is private, unavailable, or restricted - skipping")
                return None
            
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
        
    except YouTubeRateLimitError:
        raise
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


def process_single_video(video, video_num, total_videos, sp, conn, db_lock, print_lock, thread_id, overwrite=False):
    """
    Process a single YouTube video (thread-safe).
    Returns: (success: bool, skipped: bool)
    """
    import threading
    thread_name = f"T{thread_id}"
    
    with print_lock:
        print(f"\n[{thread_name}][{video_num}/{total_videos}] {video['title']}")
    
    try:
        # STEP 1: Check if this YouTube video is already in database by video title
        # This is the fastest check that avoids ALL API calls (Spotify + YouTube)
        # BUT: If entry has NULL columns (incomplete), we MUST re-process it
        if not overwrite:
            # Add delay before database check to relax DB pressure
            time.sleep(0.1)
            
            with db_lock:
                cursor = conn.cursor()
                # Check if exists AND has complete data (no NULL in critical columns)
                cursor.execute("""
                    SELECT COUNT(*) FROM audio_features 
                    WHERE youtube_match = %s 
                    AND tempo_bpm IS NOT NULL 
                    AND brightness_hz IS NOT NULL 
                    AND warmth IS NOT NULL
                """, (video['title'],))
                exists_and_complete = cursor.fetchone()[0] > 0
                cursor.close()
            
            if exists_and_complete:
                with print_lock:
                    print(f"   [{thread_name}][{video_num}] ⏭️  Already in database (complete), skipping")
                return False, True
        
        # STEP 2: Extract artist and track hints from title
        artist_hint, track_hint = clean_title_for_search(video['title'])
        
        with print_lock:
            if artist_hint:
                print(f"   [{thread_name}][{video_num}] 🔍 Searching Spotify for: {artist_hint} - {track_hint}")
            else:
                print(f"   [{thread_name}][{video_num}] 🔍 Searching Spotify for: {track_hint}")
        
        # STEP 3: Search Spotify to verify track exists and get Spotify track ID
        spotify_track = search_spotify_for_track(sp, artist_hint, track_hint, video['title'])
        
        if not spotify_track:
            with print_lock:
                time.sleep(.05)
                print(f"   [{thread_name}][{video_num}] ❌ Not found on Spotify, skipping")
            return False, True
        
        with print_lock:
            print(f"   [{thread_name}][{video_num}] ✓ Found on Spotify: {spotify_track['name']} - {', '.join(spotify_track['artists'][:2])}")
        
        # STEP 4: Double-check by Spotify track ID (in case same track was added from different YouTube video)
        # BUT: If entry has NULL columns (incomplete), we MUST re-process it
        if not overwrite:
            # Add delay before database check to relax DB pressure
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
                """, (spotify_track['id'],))
                exists_and_complete = cursor.fetchone()[0] > 0
                cursor.close()
            
            if exists_and_complete:
                with print_lock:
                    print(f"   [{thread_name}][{video_num}] ⏭️  Already in database (complete), skipping")
                return False, True
            elif exists_and_complete is False:
                with print_lock:
                    print(f"   [{thread_name}][{video_num}] 🔄 Found incomplete entry, re-processing...")
                # Will continue to re-process
        
        # STEP 5: Download and analyze from YouTube (with delay before YouTube API call)
        time.sleep(0.1)  # Relax YouTube API pressure
        features = download_and_analyze_audio(video['id'], spotify_track['name'], ', '.join(spotify_track['artists']))
        
        if not features:
            return False, True
        
        # Save to database (thread-safe with lock)
        with db_lock:
            success = insert_track_to_db(conn, spotify_track, features, video['title'])
        
        if success:
            with print_lock:
                print(f"   [{thread_name}][{video_num}] ✅ Saved! Tempo: {features['tempo']:.1f}bpm | Energy: {features['energy']:.6f} | Dance: {features['danceability']:.6f} | Valence: {features['valence']:.6f}")
            return True, False
        else:
            with print_lock:
                print(f"   [{thread_name}][{video_num}] ❌ Database save failed")
            return False, True
            
    except YouTubeRateLimitError:
        # Re-raise rate limit errors to stop the entire program
        raise
    except Exception as e:
        with print_lock:
            print(f"   [{thread_name}][{video_num}] ❌ Error: {e}")
        return False, True
    finally:
        # Longer delay to avoid YouTube bot detection
        time.sleep(3)


def build_audio_features_from_playlist(playlist_url, num_threads=4, overwrite=False, max_videos=None):
    """
    Main function to build the audio features database from a YouTube playlist or channel.
    
    Args:
        playlist_url: YouTube playlist URL, channel URL, or ID
        num_threads: Number of parallel threads for processing (default: 4)
        overwrite: If True, overwrite existing tracks; if False, skip existing (default: False)
        max_videos: Maximum number of videos to process (None = all)
    """
    import datetime
    start_time = time.time()
    
    print("=" * 60)
    print("🎵 AUDIO FEATURES DATABASE BUILDER (YouTube)")
    print("=" * 60)
    print()
    print("ℹ️  Note: Audio files are downloaded temporarily and deleted")
    print("   immediately after analysis. Nothing is kept on disk!")
    print()
    
    # Extract playlist or channel info
    source_type, source_id = extract_playlist_or_channel_id(playlist_url)
    if source_type == 'playlist':
        print(f"📋 Source: Playlist")
        print(f"📋 Playlist ID: {source_id}")
    elif source_type == 'channel':
        print(f"📋 Source: Channel")
        print(f"📋 Channel: {source_id}")
    elif source_type == 'user':
        print(f"📋 Source: User Channel")
        print(f"📋 Username: {source_id}")
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
    
    # 4. Fetch YouTube videos
    videos = fetch_youtube_videos(source_type, source_id)
    
    if not videos:
        print(f"[ERROR] No videos fetched from {source_type}!")
        return
    
    # Limit number of videos if specified
    if max_videos:
        videos = videos[:max_videos]
        print(f"⚠️  Limiting to first {max_videos} videos")
    
    print()
    
    # 5. Process videos with multithreading
    print(f"🎼 Processing {len(videos)} videos with {num_threads} parallel threads...")
    print()
    
    processed_count = 0
    skipped_count = 0
    
    # Thread-safe locks
    db_lock = Lock()
    print_lock = Lock()
    
    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        # Submit all tasks with thread IDs
        future_to_video = {}
        for i, video in enumerate(videos, 1):
            # Assign thread ID based on order (cycles through 1 to num_threads)
            thread_id = ((i - 1) % num_threads) + 1
            future = executor.submit(
                process_single_video, 
                video, 
                i, 
                len(videos), 
                sp,
                conn, 
                db_lock, 
                print_lock,
                thread_id,
                overwrite
            )
            future_to_video[future] = (i, video)
        
        # Collect results as they complete
        rate_limit_hit = False
        for future in as_completed(future_to_video):
            video_num, video = future_to_video[future]
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
                for f in future_to_video:
                    f.cancel()
                break
            except Exception as e:
                with print_lock:
                    print(f"   [ERROR] Thread for video {video_num} failed: {e}")
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
    print("📊 FINAL SUMMARY")
    print("=" * 60)
    print(f"⏱️  Total Runtime: {runtime_str}")
    print(f"🧵 Threads Used: {num_threads}")
    print(f"🔄 Overwrite Mode: {'ON (replacing existing)' if overwrite else 'OFF (skipping existing)'}")
    print(f"✅ Successfully Processed: {processed_count} tracks")
    print(f"⚠️  Not Processed/Skipped: {skipped_count} tracks")
    print(f"📊 Total Videos Attempted: {len(videos)} videos")
    print("=" * 60)
    print()
    print("✨ Database ready for similarity matching!")
    print()
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Build audio features database from YouTube playlist or channel (with Spotify verification)')
    parser.add_argument('playlist', type=str, nargs='?', help='YouTube playlist URL, channel URL (@username, /channel/, /c/, /user/), or playlist ID')
    parser.add_argument('--videos', type=int, default=None, help='Max number of videos to process (default: all)')
    parser.add_argument('--threads', type=int, default=4, help='Number of parallel threads (default: 4)')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing tracks in database (default: skip existing)')
    parser.add_argument('--batch', action='store_true', help='Read multiple playlists from playlists.txt file (one URL per line)')
    args = parser.parse_args()
    
    try:
        # Batch mode: read playlists from file
        if args.batch:
            playlists_file = 'playlists.txt'
            
            # Check if file exists
            if not os.path.exists(playlists_file):
                print(f"[ERROR] {playlists_file} not found!")
                print(f"Please create a file named 'playlists.txt' with one playlist URL per line.")
                sys.exit(1)
            
            # Read playlists from file
            with open(playlists_file, 'r') as f:
                playlists = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            
            if not playlists:
                print(f"[ERROR] No playlists found in {playlists_file}")
                print(f"Please add playlist URLs (one per line)")
                sys.exit(1)
            
            print(f"📚 Found {len(playlists)} playlists to process")
            print()
            
            # Process each playlist
            for idx, playlist_url in enumerate(playlists, 1):
                print(f"\n{'='*60}")
                print(f"📋 PROCESSING PLAYLIST {idx}/{len(playlists)}")
                print(f"{'='*60}\n")
                
                try:
                    build_audio_features_from_playlist(
                        playlist_url=playlist_url,
                        num_threads=args.threads,
                        overwrite=args.overwrite,
                        max_videos=args.videos
                    )
                    
                    # Successfully completed - remove this playlist from the file
                    try:
                        # Read all lines from playlists.txt
                        with open(playlists_file, 'r') as f:
                            all_lines = f.readlines()
                        
                        # Write back all lines except the completed playlist
                        with open(playlists_file, 'w') as f:
                            for line in all_lines:
                                # Keep the line if it's a comment, empty, or doesn't match the completed playlist
                                if line.strip() != playlist_url:
                                    f.write(line)
                        
                        print(f"\n✅ Removed completed playlist from {playlists_file}")
                    except Exception as e:
                        print(f"\n⚠️  Could not remove playlist from file: {e}")
                    
                except KeyboardInterrupt:
                    print("\n\n⚠️  Interrupted by user")
                    sys.exit(1)
                except Exception as e:
                    print(f"\n[ERROR] Failed to process playlist {idx}: {e}")
                    print(f"Continuing to next playlist...")
                    import traceback
                    traceback.print_exc()
                    continue
            
            print(f"\n{'='*60}")
            print(f"✅ COMPLETED ALL {len(playlists)} PLAYLISTS")
            print(f"{'='*60}\n")
            sys.exit(0)
        
        # Single playlist mode
        else:
            if not args.playlist:
                print("[ERROR] Please provide a playlist URL or use --batch flag")
                parser.print_help()
                sys.exit(1)
            
            build_audio_features_from_playlist(
                playlist_url=args.playlist,
                num_threads=args.threads,
                overwrite=args.overwrite,
                max_videos=args.videos
            )
            sys.exit(0)
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
