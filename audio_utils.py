#!/usr/bin/env python3
"""
Audio Processing Utilities for Playlist Generator

This module contains the audio processing functions needed by lite_script.py
for mathematical similarity matching using YouTube + librosa analysis.

These functions are separated from db_creation/ to remain accessible 
when db_creation/ is gitignored.
"""

import os
import sys
import time
import tempfile
import re

# Audio analysis
try:
    import librosa
    import numpy as np
    LIBROSA_AVAILABLE = True
except ImportError:
    print("[WARN] librosa not available - audio analysis disabled")
    LIBROSA_AVAILABLE = False

# YouTube
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    print("[WARN] yt-dlp not available - YouTube download disabled")
    YTDLP_AVAILABLE = False


# ============================================================================
# EXCEPTIONS
# ============================================================================

class YouTubeRateLimitError(Exception):
    """Raised when YouTube rate limit is detected"""
    pass


# ============================================================================
# AUDIO FEATURE EXTRACTION
# ============================================================================

def extract_audio_features(y, sr):
    """
    Extract comprehensive audio features from audio signal
    
    Args:
        y: Audio time series (numpy array)
        sr: Sample rate
    
    Returns:
        dict: Dictionary containing all extracted features
    """
    if not LIBROSA_AVAILABLE:
        raise Exception("librosa not available for audio analysis")
    
    # Tempo and beat
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    
    # Key estimation
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key_estimate = np.argmax(np.sum(chroma, axis=1))
    
    # Beat strength (using onset strength envelope)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    beat_strength = np.mean(onset_env) / (np.std(onset_env) + 1e-10)
    beat_strength = min(beat_strength / 10, 1.0)
    
    # Spectral features
    spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    spectral_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    spectral_contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    
    # Zero crossing rate (percussiveness)
    zero_crossing_rate = librosa.feature.zero_crossing_rate(y)[0]
    
    # RMS energy (loudness)
    rms_energy = librosa.feature.rms(y=y)[0]
    
    # Harmonic and percussive separation
    y_harmonic, y_percussive = librosa.effects.hpss(y)
    harmonic_mean = np.mean(np.abs(y_harmonic))
    percussive_mean = np.mean(np.abs(y_percussive))
    
    # MFCC (timbre/texture)
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_mean = np.mean(mfccs, axis=1)
    
    # Spotify-like features
    energy = np.mean(rms_energy)
    energy = min(energy * 10, 1.0)
    
    danceability = min((tempo / 120.0) * beat_strength, 1.0)
    
    brightness = np.mean(spectral_centroids)
    valence = 0.5 + (brightness - 2000) / 10000
    valence = np.clip(valence, 0, 1)
    
    acousticness = 1.0 - min(harmonic_mean * 5, 1.0)
    
    vocal_range = mfcc_mean[1:4]
    instrumentalness = 1.0 - (np.mean(np.abs(vocal_range)) / 20.0)
    instrumentalness = np.clip(instrumentalness, 0, 1)
    
    return {
        'tempo': float(tempo),
        'key_estimate': int(key_estimate),
        'beat_strength': float(beat_strength),
        'spectral_centroid': float(np.mean(spectral_centroids)),
        'spectral_rolloff': float(np.mean(spectral_rolloff)),
        'spectral_bandwidth': float(np.mean(spectral_bandwidth)),
        'spectral_contrast': float(np.mean(spectral_contrast)),
        'zero_crossing_rate': float(np.mean(zero_crossing_rate)),
        'rms_energy': float(np.mean(rms_energy)),
        'harmonic_mean': float(harmonic_mean),
        'percussive_mean': float(percussive_mean),
        'mfcc_mean': float(mfcc_mean[0]),
        'energy': float(energy),
        'danceability': float(danceability),
        'valence': float(valence),
        'acousticness': float(acousticness),
        'instrumentalness': float(instrumentalness)
    }


# ============================================================================
# YOUTUBE SEARCH & DOWNLOAD
# ============================================================================

def normalize_string(s):
    """Normalize string for fuzzy matching"""
    s = s.lower()
    s = re.sub(r'\bfeat\.?\b|\bft\.?\b|\bfeature\b', 'feat', s)
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def video_matches_track(video_title, track_name, artist_name, uploader_name=None):
    """
    Check if YouTube video matches track
    
    Returns True if:
    1. Both track name AND artist name are in video title, OR
    2. Track name in title AND artist name in uploader/channel
    """
    video_normalized = normalize_string(video_title)
    track_normalized = normalize_string(track_name)
    artist_normalized = normalize_string(artist_name)
    
    artist_parts = [normalize_string(a.strip()) for a in artist_name.split(',')]
    
    track_match = track_normalized in video_normalized
    
    # Check if artist name is in title
    artist_match = any(artist_part in video_normalized for artist_part in artist_parts)
    
    if track_match and artist_match:
        return True
    
    # Fallback: check uploader/channel name
    if track_match and uploader_name:
        uploader_normalized = normalize_string(uploader_name)
        for artist_part in artist_parts:
            if artist_part in uploader_normalized or uploader_normalized in artist_part:
                return True
    
    return False


def search_youtube(track_name, artist_name, max_results=10):
    """
    Search YouTube for track
    
    Args:
        track_name: Name of the track
        artist_name: Name of the artist
        max_results: Maximum number of results to check
    
    Returns:
        (video_id, video_title) if found, (None, None) otherwise
    
    Raises:
        YouTubeRateLimitError: If YouTube rate limit is hit
    """
    if not YTDLP_AVAILABLE:
        raise Exception("yt-dlp not available for YouTube search")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': f'ytsearch{max_results}',
    }
    
    search_queries = [
        f'{artist_name} {track_name} audio',
        f'{artist_name} {track_name}',
        f"{track_name} {artist_name} official audio"
    ]
    
    for query in search_queries:
        time.sleep(0.15)  # Rate limiting
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                
                if result and 'entries' in result:
                    for video in result['entries']:
                        video_title = video.get('title', '')
                        video_id = video.get('id', '')
                        uploader = video.get('uploader', '') or video.get('channel', '')
                        
                        if video_matches_track(video_title, track_name, artist_name, uploader):
                            return video_id, video_title
        
        except Exception as e:
            error_msg = str(e).lower()
            if 'rate limit' in error_msg or '429' in error_msg:
                raise YouTubeRateLimitError(f"YouTube rate limit: {e}")
    
    return None, None


def download_and_analyze_audio(video_id, track_name, artist_name):
    """
    Download audio from YouTube, analyze with librosa, and delete immediately
    
    Args:
        video_id: YouTube video ID
        track_name: Track name (for logging)
        artist_name: Artist name (for logging)
    
    Returns:
        dict: Extracted audio features, or None if failed
    
    Raises:
        YouTubeRateLimitError: If YouTube rate limit is hit
    """
    if not YTDLP_AVAILABLE:
        raise Exception("yt-dlp not available for YouTube download")
    
    if not LIBROSA_AVAILABLE:
        raise Exception("librosa not available for audio analysis")
    
    if not video_id:
        return None
    
    temp_files = []
    downloaded_file = None
    
    try:
        import random
        time.sleep(random.uniform(0.01, 0.25))
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        temp_files.append(temp_dir)
        temp_file = os.path.join(temp_dir, 'audio')
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_file + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }
        
        # Download
        try:
            print(f"[DEBUG] Downloading from YouTube video ID: {video_id}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                downloaded_file = ydl.prepare_filename(info)
            print(f"[DEBUG] Downloaded to: {downloaded_file}")
            print(f"[DEBUG] File exists: {os.path.exists(downloaded_file)}, Size: {os.path.getsize(downloaded_file) if os.path.exists(downloaded_file) else 0} bytes")
        except Exception as e:
            error_msg = str(e).lower()
            if 'rate limit' in error_msg or '429' in error_msg:
                raise YouTubeRateLimitError(f"YouTube rate limit: {e}")
            print(f"[ERROR] Download failed: {e}")
            raise
        
        if not os.path.exists(downloaded_file):
            raise Exception(f"Downloaded file not found: {downloaded_file}")
        
        temp_files.append(downloaded_file)
        
        # Analyze audio
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            
            try:
                print(f"[DEBUG] Loading audio file with librosa...")
                # Get duration and analyze middle portion
                duration_full = librosa.get_duration(path=downloaded_file)
                print(f"[DEBUG] Audio duration: {duration_full:.2f} seconds")
                
                if duration_full <= 120:
                    duration = 30.0
                else:
                    duration = 60.0
                
                offset = max(0, (duration_full - duration) / 2)
                print(f"[DEBUG] Analyzing {duration}s segment starting at {offset}s")
                y, sr = librosa.load(downloaded_file, offset=offset, duration=duration)
                print(f"[DEBUG] Loaded audio: {len(y)} samples at {sr} Hz")
            except Exception as e:
                print(f"[ERROR] Librosa failed to load audio: {type(e).__name__}: {e}")
                print(f"[ERROR] This usually means FFmpeg is not available or the audio file is corrupted")
                raise
        
        # Extract features
        print(f"[DEBUG] Extracting audio features...")
        features = extract_audio_features(y, sr)
        print(f"[DEBUG] Features extracted successfully")
        
        return features
    
    except Exception as e:
        print(f"[ERROR] download_and_analyze_audio failed for '{track_name}' by {artist_name}: {type(e).__name__}: {e}")
        raise
    
    finally:
        # Clean up temp files
        for temp_file in temp_files:
            try:
                if os.path.isfile(temp_file):
                    os.remove(temp_file)
                elif os.path.isdir(temp_file):
                    import shutil
                    shutil.rmtree(temp_file, ignore_errors=True)
            except:
                pass


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def process_track_for_db(sp, track_id):
    """
    Process a single track: search YouTube, download, analyze, return features
    
    This is a simplified version for use in lite_script when a track needs
    to be added to the database.
    
    Args:
        sp: Spotify client
        track_id: Spotify track ID
    
    Returns:
        tuple: (track_info_dict, features_dict) or (None, None) if failed
    """
    try:
        # Get track info from Spotify
        track = sp.track(track_id)
        track_name = track['name']
        artist_name = ', '.join([a['name'] for a in track['artists']])
        spotify_uri = track['uri']
        popularity = track.get('popularity', 0)
        
        print(f"[INFO] Processing: {track_name} by {artist_name}")
        
        # Search YouTube
        print(f"[INFO] Searching YouTube...")
        video_id, video_title = search_youtube(track_name, artist_name)
        
        if not video_id:
            print(f"[WARN] Not found on YouTube")
            return None, None
        
        print(f"[INFO] Found: {video_title}")
        
        # Download and analyze
        print(f"[INFO] Analyzing audio...")
        features = download_and_analyze_audio(video_id, track_name, artist_name)
        
        if not features:
            print(f"[WARN] Analysis failed")
            return None, None
        
        # Prepare track info
        track_info = {
            'track_id': track_id,
            'artist_name': artist_name,
            'track_name': track_name,
            'spotify_uri': spotify_uri,
            'popularity': popularity,
            'youtube_title': video_title
        }
        
        return track_info, features
    
    except YouTubeRateLimitError:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to process track: {e}")
        return None, None


# ============================================================================
# AVAILABILITY CHECK
# ============================================================================

def check_audio_processing_available():
    """
    Check if audio processing is available (librosa + yt-dlp)
    
    Returns:
        bool: True if both librosa and yt-dlp are available
    """
    return LIBROSA_AVAILABLE and YTDLP_AVAILABLE
