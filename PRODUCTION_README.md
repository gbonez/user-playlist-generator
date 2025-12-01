# Production Deployment Guide

## Overview

This playlist generator system combines Spotify OAuth, a PostgreSQL database of audio features, and mathematical similarity matching to generate personalized music playlists.

## Architecture

### Frontend (GitHub Pages)
- `docs/` folder: Static HTML/CSS/JS files
- Hosted on GitHub Pages
- Communicates with backend via REST API

### Backend (Railway)
- `app.py`: Flask server handling OAuth, jobs, API endpoints
- `lite_script.py`: Core recommendation engine
- `audio_utils.py`: Audio processing utilities (YouTube + librosa)
- Hosted on Railway with PostgreSQL database

## Key Components

### 1. Backend Entry Point: `app.py`
- **Port**: Uses `$PORT` environment variable (Railway requirement)
- **Endpoints**:
  - `/auth/login`: Initiate Spotify OAuth
  - `/auth/callback`: Handle OAuth callback
  - `/api/recommendations/start`: Start playlist generation
  - `/api/recommendations/status/<job_id>`: Check job status

### 2. Recommendation Engine: `lite_script.py`
- **Main Function**: `run_enhanced_recommendation_script()`
- **Process**:
  1. Load user's liked songs from Spotify
  2. Run lottery to select seed tracks
  3. For each seed: Find mathematically similar tracks from database
  4. Verify candidates aren't already in user's library
  5. Add top candidates to new playlist
  6. **Auto-Processing**: If seed track not in DB, process it automatically

### 3. Audio Processing: `audio_utils.py`
- **Purpose**: Extract audio features from tracks for similarity matching
- **Functions**:
  - `process_track_for_db()`: Search YouTube, download audio, extract features
  - `extract_audio_features()`: Use librosa to analyze tempo, key, energy, etc.
  - `search_youtube()`: Find matching video for track
- **Railway-Friendly**: Uses temp files, no permanent storage

### 4. Database Schema: `audio_features` table
```sql
CREATE TABLE audio_features (
    id SERIAL PRIMARY KEY,
    spotify_track_id VARCHAR(255) UNIQUE NOT NULL,
    artist_name TEXT NOT NULL,
    track_name TEXT NOT NULL,
    spotify_uri TEXT NOT NULL,
    popularity INTEGER,
    tempo FLOAT,
    key_estimate INTEGER,
    beat_strength FLOAT,
    spectral_centroid FLOAT,
    spectral_rolloff FLOAT,
    spectral_bandwidth FLOAT,
    spectral_contrast FLOAT,
    zero_crossing_rate FLOAT,
    rms_energy FLOAT,
    harmonic_mean FLOAT,
    percussive_mean FLOAT,
    mfcc_mean FLOAT,
    energy FLOAT,
    danceability FLOAT,
    valence FLOAT,
    acousticness FLOAT,
    instrumentalness FLOAT,
    youtube_title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Environment Variables (Railway)

### Required
```bash
# Spotify API
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
REDIRECT_URI=https://your-app.railway.app/auth/callback

# Database
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Railway
PORT=8080  # Set automatically by Railway
```

### Optional
```bash
# Development mode
DEVELOPMENT_MODE=false

# Frontend URL (for CORS)
FRONTEND_URL=https://yourusername.github.io
```

## Deployment Steps

### 1. Railway Setup

1. **Create New Project** on Railway
2. **Add PostgreSQL** plugin to project
3. **Deploy from GitHub**:
   - Connect your repository
   - Set root directory: `user-playlist-generator/`
   - Railway auto-detects Python and uses `requirements.txt`

4. **Set Environment Variables**:
   ```
   SPOTIFY_CLIENT_ID
   SPOTIFY_CLIENT_SECRET
   REDIRECT_URI
   FRONTEND_URL
   ```

5. **Database Setup**:
   - Railway provides `DATABASE_URL` automatically
   - Create table using schema above (see step 2)

### 2. Database Initialization

Connect to Railway PostgreSQL and run:

```sql
CREATE TABLE audio_features (
    id SERIAL PRIMARY KEY,
    spotify_track_id VARCHAR(255) UNIQUE NOT NULL,
    artist_name TEXT NOT NULL,
    track_name TEXT NOT NULL,
    spotify_uri TEXT NOT NULL,
    popularity INTEGER,
    tempo FLOAT,
    key_estimate INTEGER,
    beat_strength FLOAT,
    spectral_centroid FLOAT,
    spectral_rolloff FLOAT,
    spectral_bandwidth FLOAT,
    spectral_contrast FLOAT,
    zero_crossing_rate FLOAT,
    rms_energy FLOAT,
    harmonic_mean FLOAT,
    percussive_mean FLOAT,
    mfcc_mean FLOAT,
    energy FLOAT,
    danceability FLOAT,
    valence FLOAT,
    acousticness FLOAT,
    instrumentalness FLOAT,
    youtube_title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_spotify_track_id ON audio_features(spotify_track_id);
```

### 3. Populate Database (Optional)

If you want to pre-populate the database with tracks:

```bash
# Using local development
cd db_creation/
python build_audio_features_from_spotify.py
```

**Note**: The system auto-processes tracks that aren't in the database, so pre-population is optional but recommended for faster recommendations.

### 4. GitHub Pages Setup

1. **Push `docs/` folder** to GitHub
2. **Enable GitHub Pages**:
   - Settings → Pages
   - Source: Deploy from branch
   - Branch: main
   - Folder: /docs

3. **Update API URLs** in `docs/app.js`:
   ```javascript
   const API_BASE_URL = 'https://your-app.railway.app';
   ```

### 5. Spotify Developer Settings

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create/edit your app
3. **Add Redirect URIs**:
   - `https://your-app.railway.app/auth/callback`
   - `http://localhost:8080/auth/callback` (for local testing)
4. **Set App Scopes** (requested in app.py):
   - `user-library-read`
   - `playlist-modify-public`
   - `playlist-modify-private`

## File Structure (Production)

```
user-playlist-generator/
├── app.py                  # Flask backend (Railway)
├── lite_script.py          # Recommendation engine
├── audio_utils.py          # Audio processing utilities
├── requirements.txt        # Python dependencies
├── runtime.txt             # Python version for Railway
├── Procfile                # Railway start command
├── .gitignore              # Excludes secrets, db_creation/*
│
├── docs/                   # Frontend (GitHub Pages)
│   ├── index.html
│   ├── dashboard.html
│   ├── style.css
│   └── app.js
│
├── db_creation/            # Development tools (GITIGNORED)
│   ├── audio_features_processor.py
│   ├── build_audio_features_*.py
│   └── README.md
│
└── templates/              # Flask templates (for errors)
    ├── base.html
    ├── dashboard.html
    └── error.html
```

## Dependencies

### Production (requirements.txt)
```
Flask==3.0.0
spotipy==2.23.0
psycopg2-binary==2.9.9
python-dotenv==1.0.0
requests==2.31.0
yt-dlp==2023.12.30
librosa==0.10.1
numpy==1.26.2
```

### System Requirements
- **Python**: 3.11+ (specified in `runtime.txt`)
- **FFmpeg**: Required by librosa (Railway has this pre-installed)

## How It Works

### User Flow

1. **Login**: User clicks "Login with Spotify" → OAuth flow → Dashboard
2. **Configure**: User sets lottery odds, playlist length
3. **Generate**: Click "Generate Playlist"
4. **Backend Process**:
   - Load user's liked songs
   - Run lottery (30% odds by default)
   - For each winner: Find similar tracks
   - **Auto-processing**: If winner not in DB, process it
   - Verify candidates aren't already liked
   - Add top candidates to playlist
5. **Results**: Display added songs with titles, artists, Spotify links

### Mathematical Similarity

**Algorithm**: Euclidean distance across 16 audio features

```python
distance = sqrt(
    (tempo_1 - tempo_2)² + 
    (energy_1 - energy_2)² + 
    (danceability_1 - danceability_2)² + 
    ... # 13 more features
)
```

**Features Used**:
- Tempo, key, beat strength
- Energy, danceability, valence
- Acousticness, instrumentalness
- Spectral features (centroid, rolloff, bandwidth, contrast)
- Harmonic/percussive separation
- Zero crossing rate, RMS energy, MFCC

### Auto-Processing Flow

When a lottery winner isn't in the database:

1. **Check DB**: `ensure_track_in_db()` queries database
2. **Process**: If missing, call `process_track_for_db()`:
   - Search YouTube for matching video
   - Download audio (temp file)
   - Extract features with librosa
   - Clean up temp files
3. **Store**: Insert features into database
4. **Continue**: Use newly added track for similarity matching

## Monitoring

### Railway Logs

View logs in Railway dashboard:
- Request/response logs
- Audio processing status
- Database queries
- Errors and warnings

### Key Log Messages

- `[INFO] Processing: <track> by <artist>` - Track being analyzed
- `[INFO] ✅ Successfully added track <id> to database` - Track processed
- `[WARN] Track <id> not in database, processing now...` - Auto-processing triggered
- `[ERROR] YouTube rate limit` - Need to slow down processing

## Troubleshooting

### "Audio utilities not available"
- **Cause**: Missing `librosa` or `yt-dlp`
- **Fix**: Ensure `requirements.txt` includes both libraries

### "YouTube rate limit"
- **Cause**: Too many requests to YouTube
- **Fix**: Built-in rate limiting (0.15s between requests)
- **Note**: Wait 15-30 minutes and try again

### "Track not found on YouTube"
- **Cause**: No matching video on YouTube
- **Fix**: Normal - track skipped, system continues

### Database connection errors
- **Cause**: Invalid `DATABASE_URL`
- **Fix**: Check Railway environment variables

### OAuth redirect errors
- **Cause**: Mismatched redirect URI
- **Fix**: Update Spotify Developer Dashboard with correct URI

## Development vs Production

### Development Mode
- Set `DEVELOPMENT_MODE=true` in secrets.json
- Uses local secrets.json
- Verbose logging

### Production Mode (Railway)
- Set `DEVELOPMENT_MODE=false` (or omit)
- Uses environment variables
- Error logging only

## Performance

### Typical Processing Times
- **Lottery + Matching**: 2-5 seconds (if all tracks in DB)
- **Auto-Processing**: +30-60 seconds per missing track
- **Database Query**: <1 second per similarity search

### Recommendations
- **Pre-populate database** with common tracks for faster results
- **Limit playlist length** to 20-30 tracks initially
- **Monitor Railway logs** for performance issues

## Security

### Secrets Management
- **Never commit** `secrets.json` to git
- **Use Railway environment variables** for production
- **Rotate credentials** if exposed

### CORS
- Frontend URL whitelisted in `app.py`
- Only allow requests from GitHub Pages domain

### Rate Limiting
- YouTube API: Built-in delays
- Spotify API: spotipy handles automatically

## Support

### Documentation Files
- `ENHANCED_RECOMMENDATION_SUMMARY.md` - Recommendation algorithm details
- `CONSOLIDATED_PROCESSOR_README.md` - Audio processor usage
- `db_creation/README.md` - Database population tools

### Common Issues
- See "Troubleshooting" section above
- Check Railway logs for error details
- Verify environment variables are set correctly

## Updates

### Deploying Changes
1. **Push to GitHub** (main branch)
2. **Railway auto-deploys** within 1-2 minutes
3. **Verify logs** in Railway dashboard
4. **Test frontend** on GitHub Pages

### Database Schema Changes
1. **Connect to Railway PostgreSQL**
2. **Run ALTER TABLE** commands
3. **Update `audio_utils.py`** if needed
4. **Deploy updated code**

---

**Last Updated**: December 2024  
**Version**: 2.0 (Enhanced with auto-processing)
