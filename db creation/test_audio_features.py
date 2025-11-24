#!/usr/bin/env python3
"""
Test audio features API access
"""
import json
import sys
import os
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# Load secrets
def load_secrets():
    secrets_paths = ['secrets.json', '../secrets.json']
    for path in secrets_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    return None

secrets = load_secrets()
if not secrets:
    print("❌ Could not load secrets.json")
    sys.exit(1)

print("Testing Audio Features API Access")
print("=" * 60)

# Create Spotify client
auth_manager = SpotifyOAuth(
    client_id=secrets['SPOTIFY_CLIENT_ID'],
    client_secret=secrets['SPOTIFY_CLIENT_SECRET'],
    redirect_uri=f"{secrets['BASE_URL']}/callback",
    scope="user-library-read",
    cache_path=".test_cache"
)

sp = Spotify(auth_manager=auth_manager)

# Get current user
user = sp.current_user()
print(f"✅ Authenticated as: {user['display_name']}")
print()

# Test with a known track ID (a popular song)
test_track_id = "3n3Ppam7vgaVa1iaRUc9Lp"  # "Mr. Brightside" by The Killers
print(f"Testing with track ID: {test_track_id}")
print()

try:
    # Try to get audio features
    print("Attempting to fetch audio features...")
    features = sp.audio_features([test_track_id])
    
    if features and features[0]:
        print("✅ SUCCESS! Audio features retrieved:")
        print(json.dumps(features[0], indent=2))
    else:
        print("❌ No features returned (but no error)")
        
except Exception as e:
    print(f"❌ ERROR: {e}")
    print()
    print("Possible causes:")
    print("1. Your Spotify app is in Development Mode (limited to 25 users)")
    print("2. Your Spotify app doesn't have Extended Quota Mode enabled")
    print("3. API rate limiting")
    print()
    print("Solutions:")
    print("1. Go to your Spotify Developer Dashboard")
    print("2. Select your app")
    print("3. Click 'Settings'")
    print("4. Request Extended Quota Mode")

# Clean up test cache
if os.path.exists(".test_cache"):
    os.remove(".test_cache")
