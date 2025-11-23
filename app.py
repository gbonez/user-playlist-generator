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

CORS(app, 
     supports_credentials=True, 
     origins=[FRONTEND_URL, "http://localhost:*", "https://gbonez.github.io"],
     allow_headers=["Content-Type"],
     methods=["GET", "POST", "OPTIONS"],
     expose_headers=["Set-Cookie"])

# Spotify OAuth configuration
SPOTIFY_CLIENT_ID = config.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = config.get('SPOTIFY_CLIENT_SECRET')
BASE_URL = config.get('BASE_URL', 'https://release-radar-scripts-production.up.railway.app')
# OAuth callback redirects back to frontend
SPOTIFY_REDIRECT_URI = f"{FRONTEND_URL}/callback.html"

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
    return Spotify(access_token=token_info['access_token'])

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

@app.route('/api/login')
def login():
    """Generate Spotify OAuth URL for frontend to use"""
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return jsonify({'auth_url': auth_url})

@app.route('/callback')
def callback():
    """Handle Spotify OAuth callback and redirect back to frontend"""
    sp_oauth = create_spotify_oauth()
    
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        # Redirect to frontend with error
        return redirect(f"{FRONTEND_URL}/login.html?error={error}")
    
    if not code:
        return redirect(f"{FRONTEND_URL}/login.html?error=no_code")
    
    try:
        token_info = sp_oauth.get_access_token(code)
        session['token_info'] = token_info
        
        # Redirect back to frontend dashboard
        return redirect(f"{FRONTEND_URL}/dashboard.html")
    except Exception as e:
        print(f"OAuth error: {e}")
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
    if 'token_info' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    playlist_id = data.get('playlist_id')
    max_songs = int(data.get('max_songs', 10))
    lastfm_username = data.get('lastfm_username', '').strip()
    
    if not playlist_id:
        return jsonify({'error': 'Playlist ID is required'}), 400
    
    if max_songs < 1 or max_songs > 50:
        return jsonify({'error': 'Max songs must be between 1 and 50'}), 400
    
    try:
        # Create Spotify client
        sp = get_spotify_client(session['token_info'])
        
        # Verify user can modify this playlist
        try:
            playlist_info = sp.playlist(playlist_id)
            current_user = sp.current_user()
            
            if playlist_info['owner']['id'] != current_user['id']:
                return jsonify({'error': 'You can only run the script on playlists you own'}), 403
                
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
            'playlist_name': playlist_info['name'],
            'max_songs': max_songs,
            'lastfm_username': lastfm_username if lastfm_username else None,
            'started_at': time.time(),
            'result': None,
            'error': None
        }
        
        # Start script in background thread
        def run_script_background():
            try:
                running_jobs[job_id]['status'] = 'running'
                
                # Run the lite script
                result = run_lite_script(
                    sp=sp,
                    output_playlist_id=playlist_id,
                    max_songs=max_songs,
                    lastfm_username=lastfm_username if lastfm_username else None
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
        return jsonify({'error': str(e)}), 500

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
    
    print(f"Starting Music Discovery Web App on port {port}")
    print(f"Spotify Redirect URI: {SPOTIFY_REDIRECT_URI}")
    
    app.run(host='0.0.0.0', port=port, debug=debug)