#!/usr/bin/env python3
"""
Test playlist generation locally with real-time logging
"""
import os
import json
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from lite_script import run_enhanced_recommendation_script

# Load secrets
with open('secrets.json', 'r') as f:
    secrets = json.load(f)

# Set environment variables
for key, value in secrets.items():
    os.environ[key] = str(value)

# Initialize Spotify client
scope = "playlist-modify-public playlist-modify-private user-library-read user-read-recently-played user-top-read"
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=secrets['SPOTIFY_CLIENT_ID'],
    client_secret=secrets['SPOTIFY_CLIENT_SECRET'],
    redirect_uri=f"{secrets['BASE_URL']}/callback",
    scope=scope
))

print("=" * 80)
print("TESTING PLAYLIST GENERATION")
print("=" * 80)
print(f"Source Playlist: https://open.spotify.com/playlist/7fEFlqVJJlwgTugyYqXFDG")
print(f"Output: NEW PLAYLIST (will be created)")
print(f"Max Songs: 5")
print(f"Genre Matching: ENABLED (strict mode)")
print(f"Exclude Liked Songs: FALSE (liked songs allowed)")
print("=" * 80)
print()

# Run the generation
try:
    result = run_enhanced_recommendation_script(
        sp=sp,
        output_playlist_id=None,  # Will create new playlist
        max_songs=5,
        lastfm_username=secrets.get('LASTFM_USERNAME'),
        max_follower_count=100000,
        min_liked_songs=3,
        generation_mode='playlist',
        source_url='https://open.spotify.com/playlist/7fEFlqVJJlwgTugyYqXFDG',
        enable_genre_matching=True,
        exclude_liked_songs=False,  # Allow liked songs
        genre_matching_mode='strict',
        create_new_playlist=True  # Create new playlist
    )
    
    if result.get('success'):
        print(f"\n✓ Created playlist: {result.get('playlist_id')}")
        print(f"✓ Added {result.get('tracks_added')} songs")
    else:
        print(f"\n✗ Failed: {result.get('error')}")
    print("\n" + "=" * 80)
    print("✓ GENERATION COMPLETE")
    print("=" * 80)
except Exception as e:
    print("\n" + "=" * 80)
    print(f"✗ ERROR: {e}")
    print("=" * 80)
    import traceback
    traceback.print_exc()
