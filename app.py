import os
import json
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_cors import CORS
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
import threading
import time
from lite_script import run_lite_script

# Load configuration from secrets.json if it exists, otherwise use environment variables
def load_config():
    config = {}
    
    # Try to load from secrets.json first
    if os.path.exists('secrets.json'):
        try:
            with open('secrets.json', 'r') as f:
                config = json.load(f)
            print("✅ Loaded configuration from secrets.json")
        except Exception as e:
            print(f"⚠️  Could not load secrets.json: {e}")
    
    # Fall back to environment variables
    return {
        'SPOTIFY_CLIENT_ID': config.get('SPOTIFY_CLIENT_ID') or os.environ.get('SPOTIFY_CLIENT_ID'),
        'SPOTIFY_CLIENT_SECRET': config.get('SPOTIFY_CLIENT_SECRET') or os.environ.get('SPOTIFY_CLIENT_SECRET'),
        'BASE_URL': config.get('BASE_URL') or os.environ.get('BASE_URL', 'http://localhost:5000'),
        'FLASK_SECRET_KEY': config.get('FLASK_SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY'),
        'LASTFM_API_KEY': config.get('LASTFM_API_KEY') or os.environ.get('LASTFM_API_KEY'),
        'CHROME_BIN': config.get('CHROME_BIN') or os.environ.get('CHROME_BIN'),
        'CHROMEDRIVER_PATH': config.get('CHROMEDRIVER_PATH') or os.environ.get('CHROMEDRIVER_PATH'),
    }

# Load configuration
config = load_config()

# API-only backend - no static file serving
app = Flask(__name__)
app.secret_key = config.get('FLASK_SECRET_KEY') or secrets.token_hex(16)

# Enable CORS for frontend on GitHub Pages
# Check if running locally
import socket
def is_local_environment():
    return os.environ.get('FLASK_ENV') == 'development' or os.environ.get('PORT') is None

FRONTEND_URL = config.get('FRONTEND_URL')
if not FRONTEND_URL:
    # Auto-detect: use localhost for local dev, GitHub Pages for production
    FRONTEND_URL = 'http://localhost:8000' if is_local_environment() else 'https://gbonez.github.io/user-playlist-generator'

# Session configuration for cross-origin cookies
app.config['SESSION_COOKIE_SAMESITE'] = 'None'  # Allow cross-site cookies
app.config['SESSION_COOKIE_SECURE'] = True      # Require HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True    # Prevent JavaScript access
app.config['SESSION_COOKIE_DOMAIN'] = '.gbonez.org' if not is_local_environment() else None  # Share cookies across subdomains

CORS(app, 
     supports_credentials=True, 
     origins=[FRONTEND_URL, "http://localhost:*", "https://gbonez.github.io"],
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "OPTIONS"],
     expose_headers=["Set-Cookie"])

# Spotify OAuth configuration
SPOTIFY_CLIENT_ID = config.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = config.get('SPOTIFY_CLIENT_SECRET')
BASE_URL = config.get('BASE_URL', 'https://release-radar-scripts-production.up.railway.app')
# Spotify redirects to BACKEND /callback endpoint to exchange code for token
# Then backend redirects to frontend dashboard
SPOTIFY_REDIRECT_URI = f"{BASE_URL}/callback"

# Set environment variables for the lite script to use
if config.get('LASTFM_API_KEY'):
    os.environ['LASTFM_API_KEY'] = config.get('LASTFM_API_KEY')
if config.get('CHROME_BIN'):
    os.environ['CHROME_BIN'] = config.get('CHROME_BIN')
if config.get('CHROMEDRIVER_PATH'):
    os.environ['CHROMEDRIVER_PATH'] = config.get('CHROMEDRIVER_PATH')

# Spotify scopes needed for the lite script
SPOTIFY_SCOPES = "playlist-modify-public playlist-modify-private user-library-read"

# Store for running jobs (in production, use Redis or database)
running_jobs = {}

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPES,
        cache_path=None,
        show_dialog=True
    )

def get_spotify_client(token_info):
    """Create Spotify client from token info"""
    return Spotify(auth=token_info['access_token'])

@app.route('/')
def index():
    """API root endpoint"""
    return jsonify({
        'service': 'Music Discovery API',
        'status': 'running',
        'frontend': FRONTEND_URL
    })

@app.route('/api/auth/status')
def auth_status():
    """Check if user is authenticated"""
    if 'token_info' in session:
        try:
            sp = get_spotify_client(session['token_info'])
            user_info = sp.current_user()
            return jsonify({
                'authenticated': True,
                'user': {
                    'id': user_info.get('id'),
                    'display_name': user_info.get('display_name'),
                    'email': user_info.get('email')
                }
            })
        except:
            session.clear()
            return jsonify({'authenticated': False}), 401
    
    return jsonify({'authenticated': False}), 401

@app.route('/login')
def login():
    """Redirect directly to Spotify authorization"""
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    
    # Print the auth URL for DB script use
    print("\n" + "="*80, flush=True)
    print("🔗 AUTHENTICATION URL FOR DB SCRIPT:", flush=True)
    print(auth_url, flush=True)
    print("📋 Copy this URL to use in your db creation script", flush=True)
    print("="*80 + "\n", flush=True)
    
    return redirect(auth_url)

@app.route('/api/login')
def api_login():
    """Generate Spotify OAuth URL for frontend to use (API version)"""
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    
    # Print the auth URL for DB script use
    print("\n" + "="*80, flush=True)
    print("🔗 AUTHENTICATION URL FOR DB SCRIPT:", flush=True)
    print(auth_url, flush=True)
    print("📋 Copy this URL to use in your db creation script", flush=True)
    print("="*80 + "\n", flush=True)
    
    return jsonify({'auth_url': auth_url})

@app.route('/callback', methods=['GET', 'POST', 'OPTIONS'])
def callback():
    """Handle Spotify OAuth callback and redirect back to frontend"""
    print("\n" + "="*60, flush=True)
    print("🔔 SPOTIFY CALLBACK RECEIVED", flush=True)
    print("="*60, flush=True)
    print(f"📍 Request URL: {request.url}", flush=True)
    print(f"🌐 FRONTEND_URL: {FRONTEND_URL}", flush=True)
    print(f"🔄 SPOTIFY_REDIRECT_URI: {SPOTIFY_REDIRECT_URI}", flush=True)
    
    sp_oauth = create_spotify_oauth()
    
    code = request.args.get('code')
    error = request.args.get('error')
    
    print(f"📝 Auth Code: {code[:20] + '...' if code else 'None'}", flush=True)
    print(f"❌ Error: {error if error else 'None'}", flush=True)
    
    if error:
        print(f"⚠️  Error from Spotify: {error}")
        print(f"➡️  Redirecting to: {FRONTEND_URL}/login.html?error={error}")
        return redirect(f"{FRONTEND_URL}/login.html?error={error}")
    
    if not code:
        print("⚠️  No auth code received!")
        print(f"➡️  Redirecting to: {FRONTEND_URL}/login.html?error=no_code")
        return redirect(f"{FRONTEND_URL}/login.html?error=no_code")
    
    try:
        print("🔐 Exchanging code for token...")
        print(f"🔧 Using redirect_uri for token exchange: {SPOTIFY_REDIRECT_URI}")
        token_info = sp_oauth.get_access_token(code)
        
        # Store in session (for API calls from frontend)
        session['token_info'] = token_info
        
        # Also pass access token to frontend for client-side storage
        # Frontend will store it in localStorage and send it with API requests
        access_token = token_info['access_token']
        refresh_token = token_info.get('refresh_token', '')
        expires_at = token_info.get('expires_at', 0)
        
        print("✅ Token received and session created!")
        
        # Print the callback URL for DB script
        print("\n" + "="*80)
        print("🔗 BACKEND CALLBACK URL FOR DB SCRIPT:")
        print(f"{SPOTIFY_REDIRECT_URI}?code={code}")
        print("📋 Copy this URL to use in your db creation script")
        print("="*80 + "\n")
        
        # Add a 3-second delay so you can copy the URL from browser if needed
        print("⏱️  Waiting 3 seconds before redirect (so you can copy URL from browser)...")
        time.sleep(3)
        
        print(f"➡️  Redirecting to: {FRONTEND_URL}/callback.html with token")
        print("="*60 + "\n")
        
        # Redirect to frontend callback page with token info
        # Frontend will extract token from URL and store in localStorage
        import urllib.parse
        redirect_url = f"{FRONTEND_URL}/callback.html?access_token={access_token}&refresh_token={urllib.parse.quote(refresh_token)}&expires_at={expires_at}"
        print(f"🌐 Full redirect URL: {redirect_url[:100]}...")
        return redirect(redirect_url)
    except Exception as e:
        print(f"❌ OAuth error: {e}")
        print(f"❌ Error type: {type(e).__name__}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        print(f"➡️  Redirecting to: {FRONTEND_URL}/login.html?error=auth_failed")
        print("="*60 + "\n")
        return redirect(f"{FRONTEND_URL}/login.html?error=auth_failed")

@app.route('/api/logout')
def logout():
    """Clear session and logout"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/playlists')
def get_playlists():
    """Get user's playlists"""
    if 'token_info' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        sp = get_spotify_client(session['token_info'])
        playlists = sp.current_user_playlists(limit=50)
        
        # Filter for playlists the user owns or can modify
        user_playlists = []
        current_user = sp.current_user()
        user_id = current_user['id']
        
        for playlist in playlists['items']:
            # Include playlists owned by user or collaborative playlists
            if (playlist['owner']['id'] == user_id or 
                playlist['collaborative'] or 
                playlist['public']):
                user_playlists.append({
                    'id': playlist['id'],
                    'name': playlist['name'],
                    'tracks_total': playlist['tracks']['total'],
                    'owner': playlist['owner']['display_name'],
                    'is_owner': playlist['owner']['id'] == user_id
                })
        
        return jsonify({'playlists': user_playlists})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/run_script', methods=['POST'])
def run_script():
    """Start the lite script for the user"""
    print("[DEBUG] run_script endpoint called")
    
    # Get token from Authorization header (for token-based auth)
    auth_header = request.headers.get('Authorization')
    token_info = None
    
    print(f"[DEBUG] Authorization header present: {bool(auth_header)}")
    
    if auth_header and auth_header.startswith('Bearer '):
        access_token = auth_header.split(' ')[1]
        token_info = {'access_token': access_token}
        print(f"[DEBUG] Using Bearer token: {access_token[:20]}...")
    elif 'token_info' in session:
        token_info = session['token_info']
        print("[DEBUG] Using session token")
    
    if not token_info:
        print("[DEBUG] No token found, returning 401")
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    playlist_id = data.get('playlist_id')
    max_songs = int(data.get('max_songs', 10))
    lastfm_username = (data.get('lastfm_username') or '').strip()
    max_follower_count = data.get('max_follower_count')  # Can be None for no limit
    create_new = data.get('create_new', False)  # Whether to create new playlist
    
    if max_songs < 1 or max_songs > 50:
        return jsonify({'error': 'Max songs must be between 1 and 50'}), 400
    
    try:
        # Create Spotify client
        sp = get_spotify_client(token_info)
        current_user = sp.current_user()
        
        # If creating new playlist, we'll do it after discovery
        # Otherwise verify user can modify the existing playlist
        playlist_info = None
        if create_new:
            playlist_name = 'Enhanced Recs ⚙️'
        else:
            if not playlist_id:
                return jsonify({'error': 'Playlist ID is required when not creating new playlist'}), 400
                
            try:
                playlist_info = sp.playlist(playlist_id)
                
                if playlist_info['owner']['id'] != current_user['id']:
                    return jsonify({'error': 'You can only run the script on playlists you own'}), 403
                
                playlist_name = playlist_info['name']
                    
            except SpotifyException as e:
                if e.http_status == 404:
                    return jsonify({'error': 'Playlist not found'}), 404
                raise
        
        # Generate job ID
        job_id = secrets.token_hex(8)
        
        # Store job info
        running_jobs[job_id] = {
            'status': 'starting',
            'playlist_id': playlist_id,
            'playlist_name': playlist_name,
            'max_songs': max_songs,
            'lastfm_username': lastfm_username if lastfm_username else None,
            'max_follower_count': max_follower_count,
            'create_new': create_new,
            'user_id': current_user['id'],
            'started_at': time.time(),
            'result': None,
            'error': None
        }
        
        # Start script in background thread
        def run_script_background():
            try:
                running_jobs[job_id]['status'] = 'running'
                
                # Create fresh Spotify client in this thread
                thread_sp = get_spotify_client(token_info)
                thread_user = thread_sp.current_user()
                
                # Create new playlist if needed (BEFORE discovery to get the ID)
                actual_playlist_id = playlist_id
                if create_new:
                    try:
                        new_playlist = thread_sp.user_playlist_create(
                            user=thread_user['id'],
                            name='Enhanced Recs ⚙️',
                            public=True,
                            description='Playlist of personalized music recs generated from https://gbonez.github.io/user-playlist-generator/'
                        )
                        actual_playlist_id = new_playlist['id']
                        running_jobs[job_id]['playlist_id'] = actual_playlist_id
                        print(f"✅ Created new playlist: {new_playlist['name']} | ID: {actual_playlist_id}")
                    except Exception as e:
                        running_jobs[job_id]['status'] = 'failed'
                        running_jobs[job_id]['error'] = f'Failed to create playlist: {str(e)}'
                        return
                
                # Import new enhanced recommendation function
                from lite_script import run_enhanced_recommendation_script
                
                # Run the enhanced script with mathematical similarity
                result = run_enhanced_recommendation_script(
                    sp=thread_sp,
                    output_playlist_id=actual_playlist_id,
                    max_songs=max_songs,
                    lastfm_username=lastfm_username if lastfm_username else None,
                    max_follower_count=max_follower_count
                )
                
                running_jobs[job_id]['result'] = result
                
                if result.get('success'):
                    running_jobs[job_id]['status'] = 'completed'
                else:
                    running_jobs[job_id]['status'] = 'failed'
                    running_jobs[job_id]['error'] = result.get('error', 'Unknown error')
                    
            except Exception as e:
                running_jobs[job_id]['status'] = 'failed'
                running_jobs[job_id]['error'] = str(e)
        
        thread = threading.Thread(target=run_script_background)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'status': 'started',
            'message': 'Script started successfully'
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Exception in run_script: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/api/job_status/<job_id>')
def get_job_status(job_id):
    """Get status of a running job"""
    if job_id not in running_jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = running_jobs[job_id]
    
    response_data = {
        'job_id': job_id,
        'status': job['status'],
        'playlist_name': job['playlist_name'],
        'max_songs': job['max_songs'],
        'started_at': job['started_at'],
        'elapsed_time': time.time() - job['started_at']
    }
    
    if job['status'] == 'completed' and job['result']:
        response_data['result'] = job['result']
    elif job['status'] == 'failed' and job['error']:
        response_data['error'] = job['error']
    
    return jsonify(response_data)

@app.route('/cleanup_jobs', methods=['POST'])
def cleanup_jobs():
    """Remove old completed/failed jobs"""
    current_time = time.time()
    jobs_to_remove = []
    
    for job_id, job in running_jobs.items():
        # Remove jobs older than 1 hour
        if current_time - job['started_at'] > 3600:
            jobs_to_remove.append(job_id)
        # Remove completed/failed jobs older than 10 minutes
        elif (job['status'] in ['completed', 'failed'] and 
              current_time - job['started_at'] > 600):
            jobs_to_remove.append(job_id)
    
    for job_id in jobs_to_remove:
        del running_jobs[job_id]
    
    return jsonify({'removed_jobs': len(jobs_to_remove)})

@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', error="Page not found", code=404), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error="Internal server error", code=500), 500

if __name__ == '__main__':
    # Check required environment variables
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        print("ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables are required")
        exit(1)
    
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    print("\n" + "="*60)
    print("🚀 STARTING MUSIC DISCOVERY WEB APP")
    print("="*60)
    print(f"🌐 Port: {port}")
    print(f"🔧 Debug Mode: {debug}")
    print(f"🎯 Frontend URL: {FRONTEND_URL}")
    print(f"🔄 Spotify Redirect URI: {SPOTIFY_REDIRECT_URI}")
    print(f"📍 Base URL: {BASE_URL}")
    print(f"🔑 Client ID: {SPOTIFY_CLIENT_ID[:20]}...")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=port, debug=debug)