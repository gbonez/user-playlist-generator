#!/usr/bin/env python3
"""
Audio Features Database Builder - Spotify User's Public Playlists & Artists

This script:
1. Fetches all public playlists from a Spotify user profile OR top tracks from artist profile
2. For each playlist/artist, gets all tracks
3. Searches YouTube for each track
4. Downloads audio temporarily and analyzes with librosa
5. Stores features in PostgreSQL database

Usage:
    # User's public playlists
    python3 build_audio_features_from_spotify_user.py <spotify_user_id> --threads 10
    python3 build_audio_features_from_spotify_user.py https://open.spotify.com/user/USER_ID --threads 10
    
    # Artist's top tracks and albums
    python3 build_audio_features_from_spotify_user.py --artist <artist_id> --threads 10
    python3 build_audio_features_from_spotify_user.py --artist https://open.spotify.com/artist/ARTIST_ID --threads 10
    
    # Single track
    python3 build_audio_features_from_spotify_user.py --track https://open.spotify.com/track/TRACK_ID
"""

import os
import sys
import json
import time
import tempfile
import psycopg2
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
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
    sys.exit(1)

try:
    import yt_dlp
except ImportError:
    print("[ERROR] yt-dlp not installed!")
    print("\nPlease install:")
    print("  pip install yt-dlp")
    sys.exit(1)


# Import functions from the YouTube playlist script
# We'll reuse the audio analysis functions
sys.path.insert(0, os.path.dirname(__file__))
try:
    from build_audio_features_from_youtube_playlist import (
        load_secrets,
        get_db_connection,
        create_table,
        extract_audio_features,
        insert_track_to_db,
        YouTubeRateLimitError
    )
except ImportError:
    print("[ERROR] Could not import functions from build_audio_features_from_youtube_playlist.py")
    print("Make sure the file exists in the same directory.")
    sys.exit(1)

secrets = load_secrets()


def parse_spotify_url(url_or_id):
    """
    Parse Spotify URL or ID and return (type, id).
    Supports: user, artist, track, album URLs or plain IDs
    
    Returns: ('user', id) or ('artist', id) or ('track', id) or ('album', id)
    """
    # Check if it's a URL
    if 'open.spotify.com' in url_or_id or 'spotify.com' in url_or_id:
        # Extract type and ID from URL
        # Format: https://open.spotify.com/user/USER_ID or /artist/ARTIST_ID etc.
        match = re.search(r'spotify\.com/(user|artist|track|album)/([a-zA-Z0-9]+)', url_or_id)
        if match:
            return (match.group(1), match.group(2))
    
    # If it's just an ID, assume it's a user ID (for backward compatibility)
    return ('user', url_or_id)


def create_spotify_client():
    """Create authenticated Spotify client with user permissions"""
    scope = "user-library-read playlist-read-private playlist-read-collaborative"
    
    auth_manager = SpotifyOAuth(
        client_id=secrets['SPOTIFY_CLIENT_ID'],
        client_secret=secrets['SPOTIFY_CLIENT_SECRET'],
        redirect_uri=f"{secrets['BASE_URL']}/callback",
        scope=scope,
        cache_path=".spotify_user_playlists_cache"
    )
    
    return Spotify(auth_manager=auth_manager)


def get_user_playlists(sp, user_id):
    """
    Fetch all public playlists for a given Spotify user.
    Returns list of playlist dicts with id, name, and track count.
    """
    print(f"📋 Fetching playlists for user: {user_id}")
    
    playlists = []
    offset = 0
    limit = 50
    
    try:
        while True:
            results = sp.user_playlists(user_id, limit=limit, offset=offset)
            
            if not results or not results.get('items'):
                break
            
            for playlist in results['items']:
                if playlist and playlist.get('public', True):  # Only public playlists
                    playlists.append({
                        'id': playlist['id'],
                        'name': playlist['name'],
                        'total_tracks': playlist['tracks']['total'],
                        'owner': playlist['owner']['display_name']
                    })
            
            # Check if there are more playlists
            if not results.get('next'):
                break
            
            offset += limit
            time.sleep(0.1)  # Rate limit protection
        
        print(f"✅ Found {len(playlists)} public playlists")
        return playlists
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch user playlists: {e}")
        return []


def get_artist_albums(sp, artist_id):
    """
    Fetch all albums from an artist.
    Returns list of album dicts.
    """
    print(f"🎤 Fetching albums for artist: {artist_id}")
    
    albums = []
    offset = 0
    limit = 50
    
    try:
        # Get artist info
        artist = sp.artist(artist_id)
        print(f"   Artist: {artist['name']}")
        
        # Get all albums (including singles and compilations)
        while True:
            results = sp.artist_albums(artist_id, album_type='album,single,compilation', limit=limit, offset=offset)
            
            if not results or not results.get('items'):
                break
            
            for album in results['items']:
                if album:
                    albums.append({
                        'id': album['id'],
                        'name': album['name'],
                        'type': album['album_type'],
                        'total_tracks': album['total_tracks']
                    })
            
            if not results.get('next'):
                break
            
            offset += limit
            time.sleep(0.1)
        
        print(f"✅ Found {len(albums)} albums/singles")
        return albums
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch artist albums: {e}")
        return []


def get_album_tracks(sp, album_id):
    """
    Fetch all tracks from an album.
    Returns list of track dicts.
    """
    tracks = []
    offset = 0
    limit = 50
    
    try:
        while True:
            results = sp.album_tracks(album_id, limit=limit, offset=offset)
            
            if not results or not results.get('items'):
                break
            
            for track in results['items']:
                if not track or not track.get('id'):
                    continue
                
                # Get full track info (album_tracks doesn't include popularity)
                try:
                    full_track = sp.track(track['id'])
                    tracks.append({
                        'id': full_track['id'],
                        'name': full_track['name'],
                        'artists': [a['name'] for a in full_track.get('artists', [])],
                        'uri': full_track['uri'],
                        'popularity': full_track.get('popularity', 0)
                    })
                    time.sleep(0.05)
                except:
                    # Fallback if full track fetch fails
                    tracks.append({
                        'id': track['id'],
                        'name': track['name'],
                        'artists': [a['name'] for a in track.get('artists', [])],
                        'uri': track['uri'],
                        'popularity': 0
                    })
            
            if not results.get('next'):
                break
            
            offset += limit
            time.sleep(0.1)
        
        return tracks
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch album tracks: {e}")
        return []


def get_single_track(sp, track_id):
    """
    Fetch a single track by ID.
    Returns track dict or None.
    """
    print(f"🎵 Fetching track: {track_id}")
    
    try:
        track = sp.track(track_id)
        
        if not track or not track.get('id'):
            return None
        
        print(f"   Track: {', '.join([a['name'] for a in track['artists']])} - {track['name']}")
        
        return {
            'id': track['id'],
            'name': track['name'],
            'artists': [a['name'] for a in track.get('artists', [])],
            'uri': track['uri'],
            'popularity': track.get('popularity', 0)
        }
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch track: {e}")
        return None


def get_playlist_tracks(sp, playlist_id):
    """
    Fetch all tracks from a Spotify playlist.
    Returns list of track dicts.
    """
    tracks = []
    offset = 0
    limit = 100
    
    try:
        while True:
            results = sp.playlist_tracks(playlist_id, limit=limit, offset=offset)
            
            if not results or not results.get('items'):
                break
            
            for item in results['items']:
                if not item or not item.get('track'):
                    continue
                
                track = item['track']
                if not track or not track.get('id'):
                    continue
                
                tracks.append({
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [a['name'] for a in track.get('artists', [])],
                    'uri': track['uri'],
                    'popularity': track.get('popularity', 0)
                })
            
            # Check if there are more tracks
            if not results.get('next'):
                break
            
            offset += limit
            time.sleep(0.1)  # Rate limit protection
        
        return tracks
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch playlist tracks: {e}")
        return []


def search_youtube_for_track(artist_name, track_name):
    """
    Search YouTube for a specific track.
    Returns video ID or None.
    """
    query = f"{artist_name} {track_name}"
    
    firefox_profile = '7hkeppud.default-release-1'  # Change if your profile name changes
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': 'ytsearch1',  # Search and return first result
        'cookiesfrombrowser': ('firefox', firefox_profile),
    }
    try:
        time.sleep(0.1)  # Rate limit protection
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if result and 'entries' in result and result['entries']:
                video = result['entries'][0]
                if video and video.get('id'):
                    return video['id']
        return None
    except Exception as e:
        return None


def download_and_analyze_audio(video_id, track_name, artist_name):
    """
    Download audio temporarily, analyze with librosa, delete immediately.
    Returns extracted features dict.
    """
    if not video_id:
        return None
    
    temp_files = []
    
    try:
        import random
        delay = random.uniform(0.05, 0.3)
        time.sleep(delay)
        
        temp_dir = tempfile.mkdtemp()
        temp_files.append(temp_dir)
        temp_file = os.path.join(temp_dir, 'audio')
        firefox_profile = '7hkeppud.default-release-1'  # Change if your profile name changes
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': ('firefox', firefox_profile),
        }
        # Download
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                downloaded_file = ydl.prepare_filename(info)
        except Exception as download_error:
            error_msg = str(download_error).lower()
            # Detect YouTube rate limiting
            if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429', 'quota exceeded', 'bot detection']) and 'sign in to confirm' not in error_msg:
                raise YouTubeRateLimitError(f"YouTube rate limit reached: {download_error}")
            if any(keyword in error_msg for keyword in ['sign in to confirm', 'age', 'not available in your country', 'copyright', 'private video', 'unavailable', 'removed']):
                return None
            raise
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            duration_full = librosa.get_duration(path=downloaded_file)
            if duration_full <= 120:
                duration = 30.0
                offset = max(0, (duration_full - duration) / 2)
            else:
                duration = 60.0
                offset = max(0, (duration_full - duration) / 2)
            y, sr = librosa.load(downloaded_file, offset=offset, duration=duration)
        features = extract_audio_features(y, sr)
        return features
        
    except YouTubeRateLimitError:
        raise
    except Exception as e:
        return None
        
    finally:
        import shutil
        for temp_item in temp_files:
            if temp_item and os.path.exists(temp_item):
                try:
                    if os.path.isdir(temp_item):
                        shutil.rmtree(temp_item)
                    else:
                        os.remove(temp_item)
                except Exception:
                    pass


def process_single_track(track, track_num, total_tracks, conn, db_lock, print_lock, thread_id, playlist_name, overwrite=False, extensive=False, sp=None, processed_artists=None):
    """
    Process a single Spotify track (thread-safe).
    Returns: (success: bool, skipped: bool)
    
    Args:
        extensive: If True, also process the artist's entire discography
        sp: Spotify client (required if extensive=True)
        processed_artists: Set to track which artists have been processed (required if extensive=True)
    """
    thread_name = f"T{thread_id}"
    artist_str = ', '.join(track['artists'][:2])
    
    with print_lock:
        print(f"\n[{thread_name}][{track_num}/{total_tracks}] {artist_str} - {track['name']}")
    
    try:
        # STEP 1: Check if already in database by Spotify track ID
        # BUT: If entry has NULL columns (incomplete), we MUST re-process it
        if not overwrite:
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
                return False, True
            else:
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
        
        # STEP 2: Search YouTube
        with print_lock:
            print(f"   [{thread_name}][{track_num}] 🔍 Searching YouTube...")
        
        video_id = search_youtube_for_track(artist_str, track['name'])
        
        if not video_id:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ❌ Not found on YouTube, skipping")
            return False, True
        
        with print_lock:
            print(f"   [{thread_name}][{track_num}] ✓ Found on YouTube: {video_id}")
        
        # STEP 3: Download and analyze
        with print_lock:
            print(f"   [{thread_name}][{track_num}] 🎵 Analyzing...", end='', flush=True)
        
        features = download_and_analyze_audio(video_id, track['name'], artist_str)
        
        if not features:
            with print_lock:
                print(f" Failed")
            return False, True
        
        with print_lock:
            print(f" Done!")
        
        # STEP 4: Save to database
        with db_lock:
            success = insert_track_to_db(conn, track, features, f"{artist_str} - {track['name']}")
        
        if success:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ✅ Saved! Tempo: {features['tempo']:.1f}bpm | Energy: {features['energy']:.6f}")
            
            # EXTENSIVE MODE: Process artist's discography
            if extensive and sp and processed_artists is not None:
                # Get the primary artist (first in the list)
                if track.get('artist_id'):
                    artist_id = track['artist_id']
                else:
                    # Need to look up artist ID from the track
                    try:
                        full_track = sp.track(track['id'])
                        if full_track and full_track.get('artists') and len(full_track['artists']) > 0:
                            artist_id = full_track['artists'][0]['id']
                            artist_name = full_track['artists'][0]['name']
                        else:
                            return True, False
                    except:
                        return True, False
                
                # Check if we've already processed this artist
                with db_lock:
                    if artist_id in processed_artists:
                        with print_lock:
                            print(f"   [{thread_name}][{track_num}] 🎤 Artist already processed extensively, skipping")
                        return True, False
                    
                    # Mark artist as processed
                    processed_artists.add(artist_id)
                
                with print_lock:
                    print(f"   [{thread_name}][{track_num}] 🎤 EXTENSIVE: Processing artist {artist_name}'s discography...")
                
                # Get artist's albums
                try:
                    albums = get_artist_albums(sp, artist_id)
                    
                    if albums:
                        with print_lock:
                            print(f"   [{thread_name}][{track_num}] 🎤 Found {len(albums)} albums from {artist_name}")
                        
                        # Process each album's tracks
                        for album in albums:
                            album_tracks = get_album_tracks(sp, album['id'])
                            
                            for album_track in album_tracks:
                                # Process recursively but WITHOUT extensive flag to avoid infinite loops
                                try:
                                    process_single_track(
                                        album_track, 
                                        track_num, 
                                        total_tracks, 
                                        conn, 
                                        db_lock, 
                                        print_lock, 
                                        thread_id, 
                                        f"{artist_name} - {album['name']}", 
                                        overwrite,
                                        extensive=False,  # Don't go deeper
                                        sp=sp,
                                        processed_artists=processed_artists
                                    )
                                except Exception as e:
                                    with print_lock:
                                        print(f"   [{thread_name}] ⚠️  Failed to process artist track: {e}")
                                    continue
                        
                        with print_lock:
                            print(f"   [{thread_name}][{track_num}] 🎤 Completed {artist_name}'s discography")
                except Exception as e:
                    with print_lock:
                        print(f"   [{thread_name}][{track_num}] ⚠️  Failed to process artist extensively: {e}")
            
            return True, False
        else:
            with print_lock:
                print(f"   [{thread_name}][{track_num}] ❌ Database save failed")
            return False, True
            
    except YouTubeRateLimitError:
        raise
    except Exception as e:
        with print_lock:
            print(f"   [{thread_name}][{track_num}] ❌ Error: {e}")
        return False, True
    finally:
        import random
        delay = random.uniform(0.05, 0.3)
        time.sleep(delay)


def build_audio_features_from_user(user_id, num_threads=4, overwrite=False, max_playlists=None, extensive=False):
    """
    Main function to build audio features database from a Spotify user's public playlists.
    
    Args:
        extensive: If True, also process each artist's entire discography
    """
    start_time = time.time()
    
    print("=" * 60)
    print("🎵 AUDIO FEATURES DATABASE BUILDER (Spotify User)")
    if extensive:
        print("🔥 EXTENSIVE MODE: Will process each artist's full discography")
    print("=" * 60)
    print()
    
    # 1. Connect to Spotify
    print("🔐 Authenticating with Spotify...")
    sp = create_spotify_client()
    current_user = sp.current_user()
    print(f"   ✅ Logged in as: {current_user['display_name']}")
    print()
    
    # 2. Connect to Database
    print("🗄️  Connecting to Postgres database...")
    conn = get_db_connection()
    print("   ✅ Database connected")
    print()
    
    # 3. Create table
    print("📋 Verifying audio_features table exists...")
    create_table(conn)
    print()
    
    # 4. Fetch user's playlists
    playlists = get_user_playlists(sp, user_id)
    
    if not playlists:
        print(f"[ERROR] No public playlists found for user: {user_id}")
        return
    
    # Limit playlists if specified
    if max_playlists:
        playlists = playlists[:max_playlists]
        print(f"⚠️  Limiting to first {max_playlists} playlists")
    
    print()
    
    # 5. Process each playlist
    total_processed = 0
    total_skipped = 0
    
    # Track processed artists to avoid duplicates in extensive mode
    processed_artists = set() if extensive else None
    
    for playlist_idx, playlist in enumerate(playlists, 1):
        print(f"\n{'='*60}")
        print(f"📋 PLAYLIST {playlist_idx}/{len(playlists)}: {playlist['name']}")
        print(f"   Owner: {playlist['owner']}")
        print(f"   Tracks: {playlist['total_tracks']}")
        print(f"{'='*60}\n")
        
        # Get tracks from playlist
        tracks = get_playlist_tracks(sp, playlist['id'])
        
        if not tracks:
            print(f"⚠️  No tracks found in playlist, skipping")
            continue
        
        print(f"🎼 Processing {len(tracks)} tracks with {num_threads} threads...\n")
        
        processed_count = 0
        skipped_count = 0
        
        # Thread-safe locks
        db_lock = Lock()
        print_lock = Lock()
        
        # Process tracks with multithreading
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            future_to_track = {}
            
            for i, track in enumerate(tracks, 1):
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
                    playlist['name'],
                    overwrite,
                    extensive,
                    sp,
                    processed_artists
                )
                future_to_track[future] = (i, track)
            
            # Collect results
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
                        print(f"🚫 YOUTUBE RATE LIMIT DETECTED - STOPPING")
                        print(f"{'='*60}\n")
                    rate_limit_hit = True
                    for f in future_to_track:
                        f.cancel()
                    break
                except Exception as e:
                    with print_lock:
                        print(f"   [ERROR] Thread failed: {e}")
                    skipped_count += 1
            
            if rate_limit_hit:
                conn.close()
                print("\n⚠️  Exiting due to YouTube rate limit.")
                sys.exit(1)
        
        total_processed += processed_count
        total_skipped += skipped_count
        
        print(f"\n✅ Playlist '{playlist['name']}' complete: {processed_count} processed, {skipped_count} skipped")
    
    # Calculate runtime
    end_time = time.time()
    runtime_seconds = end_time - start_time
    runtime_minutes = runtime_seconds / 60
    runtime_hours = runtime_minutes / 60
    
    if runtime_hours >= 1:
        runtime_str = f"{runtime_hours:.2f} hours"
    elif runtime_minutes >= 1:
        runtime_str = f"{runtime_minutes:.2f} minutes"
    else:
        runtime_str = f"{runtime_seconds:.2f} seconds"
    
    # Final summary
    print()
    print("=" * 60)
    print("📊 FINAL SUMMARY")
    print("=" * 60)
    print(f"⏱️  Total Runtime: {runtime_str}")
    print(f"📋 Playlists Processed: {len(playlists)}")
    print(f"🧵 Threads Used: {num_threads}")
    print(f"✅ Successfully Processed: {total_processed} tracks")
    print(f"⚠️  Skipped: {total_skipped} tracks")
    print("=" * 60)
    print()
    print("✨ Database ready for similarity matching!")
    print()
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Build audio features database from Spotify user playlists, artist albums, or single tracks')
    parser.add_argument('user_id', type=str, nargs='?', help='Spotify user ID or URL (e.g., "username" or "https://open.spotify.com/user/ID")')
    parser.add_argument('--artist', type=str, help='Spotify artist ID or URL to fetch all albums/tracks')
    parser.add_argument('--track', type=str, help='Spotify track ID or URL to fetch single track')
    parser.add_argument('--threads', type=int, default=4, help='Number of parallel threads (default: 4)')
    parser.add_argument('--playlists', type=int, default=None, help='Max number of playlists/albums to process (default: all)')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing tracks in database (default: skip existing)')
    parser.add_argument('--extensive', action='store_true', help='For each track, also process the artist\'s entire discography (avoids duplicate artists)')
    args = parser.parse_args()
    
    try:
        # MODE 1: Single track
        if args.track:
            source_type, track_id = parse_spotify_url(args.track)
            if source_type != 'track':
                track_id = args.track  # Assume it's just an ID
            
            print("=" * 60)
            print("🎵 AUDIO FEATURES DATABASE BUILDER (Single Track)")
            print("=" * 60)
            print()
            
            # Connect to Spotify
            print("🔐 Authenticating with Spotify...")
            sp = create_spotify_client()
            print("   ✅ Connected")
            print()
            
            # Connect to Database
            print("🗄️  Connecting to Postgres database...")
            conn = get_db_connection()
            print("   ✅ Database connected")
            print()
            
            # Create table
            create_table(conn)
            print()
            
            # Get track
            track = get_single_track(sp, track_id)
            if not track:
                print("[ERROR] Could not fetch track")
                sys.exit(1)
            
            # Process track
            db_lock = Lock()
            print_lock = Lock()
            
            success, skipped = process_single_track(
                track, 1, 1, conn, db_lock, print_lock, 1, "Single Track", args.overwrite
            )
            
            if success:
                print("\n✅ Track processed successfully!")
            elif skipped:
                print("\n⏭️  Track was skipped")
            else:
                print("\n❌ Track processing failed")
            
            conn.close()
            sys.exit(0)
        
        # MODE 2: Artist albums
        elif args.artist:
            source_type, artist_id = parse_spotify_url(args.artist)
            if source_type != 'artist':
                artist_id = args.artist  # Assume it's just an ID
            
            start_time = time.time()
            
            print("=" * 60)
            print("🎵 AUDIO FEATURES DATABASE BUILDER (Artist)")
            print("=" * 60)
            print()
            
            # Connect to Spotify
            print("🔐 Authenticating with Spotify...")
            sp = create_spotify_client()
            print("   ✅ Connected")
            print()
            
            # Connect to Database
            print("🗄️  Connecting to Postgres database...")
            conn = get_db_connection()
            print("   ✅ Database connected")
            print()
            
            # Create table
            create_table(conn)
            print()
            
            # Get artist albums
            albums = get_artist_albums(sp, artist_id)
            if not albums:
                print("[ERROR] No albums found for artist")
                sys.exit(1)
            
            # Limit albums if specified
            if args.playlists:  # Reuse --playlists flag for albums
                albums = albums[:args.playlists]
                print(f"⚠️  Limiting to first {args.playlists} albums")
            
            print()
            
            # Process each album
            total_processed = 0
            total_skipped = 0
            
            for album_idx, album in enumerate(albums, 1):
                print(f"\n{'='*60}")
                print(f"💿 ALBUM {album_idx}/{len(albums)}: {album['name']}")
                print(f"   Type: {album['type']}")
                print(f"   Tracks: {album['total_tracks']}")
                print(f"{'='*60}\n")
                
                # Get tracks from album
                tracks = get_album_tracks(sp, album['id'])
                
                if not tracks:
                    print(f"⚠️  No tracks found in album, skipping")
                    continue
                
                print(f"🎼 Processing {len(tracks)} tracks with {args.threads} threads...\n")
                
                processed_count = 0
                skipped_count = 0
                
                # Thread-safe locks
                db_lock = Lock()
                print_lock = Lock()
                
                # Process tracks
                with ThreadPoolExecutor(max_workers=args.threads) as executor:
                    future_to_track = {}
                    
                    for i, track in enumerate(tracks, 1):
                        thread_id = ((i - 1) % args.threads) + 1
                        future = executor.submit(
                            process_single_track,
                            track, i, len(tracks), conn, db_lock, print_lock,
                            thread_id, album['name'], args.overwrite
                        )
                        future_to_track[future] = (i, track)
                    
                    # Collect results
                    for future in as_completed(future_to_track):
                        try:
                            success, skipped = future.result()
                            if success:
                                processed_count += 1
                            if skipped:
                                skipped_count += 1
                        except YouTubeRateLimitError:
                            print("\n🚫 YouTube rate limit - stopping")
                            conn.close()
                            sys.exit(1)
                        except Exception as e:
                            print(f"[ERROR] {e}")
                            skipped_count += 1
                
                total_processed += processed_count
                total_skipped += skipped_count
                
                print(f"\n✅ Album complete: {processed_count} processed, {skipped_count} skipped")
            
            # Summary
            end_time = time.time()
            runtime = end_time - start_time
            print(f"\n{'='*60}")
            print(f"✅ All albums processed!")
            print(f"   Runtime: {runtime/60:.2f} minutes")
            print(f"   Processed: {total_processed} | Skipped: {total_skipped}")
            print(f"{'='*60}\n")
            
            conn.close()
            sys.exit(0)
        
        # MODE 3: User playlists (default)
        elif args.user_id:
            source_type, user_id = parse_spotify_url(args.user_id)
            if source_type != 'user':
                user_id = args.user_id  # Assume it's just an ID
            
            build_audio_features_from_user(
                user_id=user_id,
                num_threads=args.threads,
                overwrite=args.overwrite,
                max_playlists=args.playlists,
                extensive=args.extensive
            )
            sys.exit(0)
        
        else:
            print("[ERROR] Please provide a user_id, --artist, or --track")
            parser.print_help()
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
