# Audio Features Database Builder - Command Reference

This directory contains scripts to build and populate an audio features database for music similarity matching. All scripts analyze audio with librosa and store features in PostgreSQL.

---

## 📚 Table of Contents

1. [build_audio_features_youtube.py](#build_audio_features_youtubepy) - Process your Spotify liked songs via YouTube
2. [build_audio_features_from_youtube_playlist.py](#build_audio_features_from_youtube_playlistpy) - Process YouTube playlists/channels
3. [build_audio_features_from_spotify_user.py](#build_audio_features_from_spotify_userpy) - Process Spotify users/artists/tracks
4. [build_audio_features_from_spotify.py](#build_audio_features_from_spotifypy) - Original Spotify processing script

---

## 🎵 Scripts

### `build_audio_features_youtube.py`

**Purpose:** Fetch your Spotify liked songs and analyze them via YouTube downloads.

**Usage:**
```bash
python3 build_audio_features_youtube.py [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tracks` | int | None | Maximum number of liked songs to process (default: all) |
| `--threads` | int | 4 | Number of parallel threads for processing |
| `--overwrite` | flag | False | Overwrite existing tracks in database (default: skip existing) |

**Examples:**
```bash
# Process all liked songs with 10 threads
python3 build_audio_features_youtube.py --threads 10

# Process first 100 liked songs with 5 threads
python3 build_audio_features_youtube.py --tracks 100 --threads 5

# Overwrite existing tracks
python3 build_audio_features_youtube.py --threads 10 --overwrite
```

**Requirements:**
- Spotify account with liked songs
- Chrome browser with YouTube login (for age-restricted content)
- Browser must be closed when running

---

### `build_audio_features_from_youtube_playlist.py`

**Purpose:** Process all videos from YouTube playlists or channels, verify on Spotify, and analyze audio.

**Usage:**
```bash
python3 build_audio_features_from_youtube_playlist.py <PLAYLIST_URL> [OPTIONS]
python3 build_audio_features_from_youtube_playlist.py --batch [OPTIONS]
```

**Arguments:**
| Argument | Required | Description |
|----------|----------|-------------|
| `playlist` | Yes* | YouTube playlist URL, channel URL, or playlist ID (*Not required with --batch) |

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--videos` | int | None | Maximum number of videos to process (default: all) |
| `--threads` | int | 4 | Number of parallel threads for processing |
| `--overwrite` | flag | False | Overwrite existing tracks in database |
| `--batch` | flag | False | Read multiple playlists from `playlists.txt` file |

**Examples:**
```bash
# Process single playlist with 10 threads
python3 build_audio_features_from_youtube_playlist.py "https://www.youtube.com/playlist?list=PLsX5EFjMynbTOWTj308MG9CmM8E-JiWpu" --threads 10

# Process first 50 videos from playlist
python3 build_audio_features_from_youtube_playlist.py "PLAYLIST_URL" --videos 50 --threads 10

# Process multiple playlists from playlists.txt
python3 build_audio_features_from_youtube_playlist.py --batch --threads 10

# Process channel's videos
python3 build_audio_features_from_youtube_playlist.py "https://www.youtube.com/@channelname" --threads 10
```

**Batch Mode:**
Create a `playlists.txt` file with one playlist URL per line:
```
# My Playlists
https://www.youtube.com/playlist?list=PLAYLIST_ID_1
https://www.youtube.com/playlist?list=PLAYLIST_ID_2
https://www.youtube.com/@channelname
# Lines starting with # are comments
```

**Supported URL Formats:**
- `https://www.youtube.com/playlist?list=PLAYLIST_ID`
- `https://www.youtube.com/watch?v=VIDEO&list=PLAYLIST_ID`
- `https://www.youtube.com/@username`
- `https://www.youtube.com/channel/CHANNEL_ID`
- `https://www.youtube.com/c/ChannelName`
- `https://www.youtube.com/user/username`
- Plain playlist ID: `PLsX5EFjMynbTOWTj308MG9CmM8E-JiWpu`

**Requirements:**
- Multi-browser cookie support (Chrome → Firefox → Brave → Edge → Safari → Chromium)
- Browser must be closed for cookie access
- Automatically handles age-restricted, geo-blocked, and copyright-claimed videos

---

### `build_audio_features_from_spotify_user.py`

**Purpose:** Process Spotify users' playlists, artists' discographies, or individual tracks.

**Usage:**
```bash
# User's public playlists
python3 build_audio_features_from_spotify_user.py <USER_ID> [OPTIONS]

# Artist's discography
python3 build_audio_features_from_spotify_user.py --artist <ARTIST_ID> [OPTIONS]

# Single track
python3 build_audio_features_from_spotify_user.py --track <TRACK_ID> [OPTIONS]
```

**Arguments:**
| Argument | Required | Description |
|----------|----------|-------------|
| `user_id` | Yes* | Spotify user ID or URL (*Not required with --artist or --track) |

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--artist` | string | None | Spotify artist ID or URL to fetch all albums/tracks |
| `--track` | string | None | Spotify track ID or URL to fetch single track |
| `--threads` | int | 4 | Number of parallel threads for processing |
| `--playlists` | int | None | Max number of playlists/albums to process (default: all) |
| `--overwrite` | flag | False | Overwrite existing tracks in database |
| `--extensive` | flag | False | **For each track, also process the artist's entire discography** |

**Examples:**

**User Mode:**
```bash
# Process all public playlists from a user
python3 build_audio_features_from_spotify_user.py "spotify_user_id" --threads 10

# Process with Spotify URL
python3 build_audio_features_from_spotify_user.py "https://open.spotify.com/user/USER_ID" --threads 10

# Limit to first 5 playlists
python3 build_audio_features_from_spotify_user.py "user_id" --playlists 5 --threads 10

# EXTENSIVE MODE: Process playlists + each artist's full discography
python3 build_audio_features_from_spotify_user.py "user_id" --threads 10 --extensive
```

**Artist Mode:**
```bash
# Process artist's entire discography (albums, singles, compilations)
python3 build_audio_features_from_spotify_user.py --artist "artist_id" --threads 10

# Process with Spotify URL
python3 build_audio_features_from_spotify_user.py --artist "https://open.spotify.com/artist/ARTIST_ID" --threads 10

# Limit to first 5 albums
python3 build_audio_features_from_spotify_user.py --artist "artist_id" --playlists 5 --threads 10
```

**Track Mode:**
```bash
# Process single track
python3 build_audio_features_from_spotify_user.py --track "track_id"

# Process with Spotify URL
python3 build_audio_features_from_spotify_user.py --track "https://open.spotify.com/track/TRACK_ID"
```

**Extensive Mode Explained:**
When `--extensive` is enabled:
1. Processes each track from the user's playlists
2. Identifies the primary artist of each track
3. Fetches and processes the artist's ENTIRE discography (all albums/singles)
4. Tracks which artists have been processed to avoid duplicates
5. Builds a comprehensive database from curated playlists

**Example:** If your playlist contains "Tame Impala - The Less I Know The Better", extensive mode will:
- Process that specific track
- Detect it's by Tame Impala
- Fetch ALL Tame Impala albums (Currents, Lonerism, Innerspeaker, etc.)
- Process every track from every album
- Mark Tame Impala as processed (skip if encountered again)

**Supported Spotify URL Formats:**
- User: `https://open.spotify.com/user/USER_ID`
- Artist: `https://open.spotify.com/artist/ARTIST_ID`
- Track: `https://open.spotify.com/track/TRACK_ID`
- Album: `https://open.spotify.com/album/ALBUM_ID`
- Plain IDs also supported

**How to Find Spotify IDs:**
1. Go to Spotify (web or desktop app)
2. Right-click on user/artist/track/album
3. Click "Share" → "Copy link"
4. Paste the full URL into the script

---


### `build_audio_features_from_spotify.py`

**Purpose:** Process Spotify liked songs, playlists, or individual tracks directly (legacy, now supports playlist/track links).

**Usage:**
```bash
# Process liked songs (default)
python3 build_audio_features_from_spotify.py [OPTIONS]

# Process a specific Spotify playlist
python3 build_audio_features_from_spotify.py --playlist <PLAYLIST_ID_OR_URL> [OPTIONS]

# Process a specific Spotify track
python3 build_audio_features_from_spotify.py --track <TRACK_ID_OR_URL> [OPTIONS]
```

**Arguments:**
| Argument    | Required | Description |
|-------------|----------|-------------|
| `--playlist`| No       | Spotify playlist ID or URL to process |
| `--track`   | No       | Spotify track ID or URL to process |

**Flags:**
| Flag        | Type     | Default | Description |
|-------------|----------|---------|-------------|
| `--tracks`  | int      | 100     | Number of liked songs or playlist tracks to process (0 = all) |
| `--threads` | int      | 9       | Number of parallel threads |
| `--overwrite`| flag    | False   | Overwrite existing tracks in database |

**Examples:**
```bash
# Process all liked songs (default)
python3 build_audio_features_from_spotify.py --threads 10

# Process a specific playlist
python3 build_audio_features_from_spotify.py --playlist "https://open.spotify.com/playlist/3F4grcxHB3p1t1yvTPDXUD" --threads 2

# Process a specific track
python3 build_audio_features_from_spotify.py --track "https://open.spotify.com/track/TRACK_ID" --threads 2

# Limit to first 10 tracks in playlist
python3 build_audio_features_from_spotify.py --playlist "PLAYLIST_ID" --tracks 10 --threads 4
```

**Supported Spotify URL Formats:**
- Playlist: `https://open.spotify.com/playlist/PLAYLIST_ID`
- Track: `https://open.spotify.com/track/TRACK_ID`
- Plain IDs also supported

**Note:** This script now supports direct playlist and track processing. For user/artist workflows, use the newer scripts above for more features.

---

## 🔧 Global Features

### Multi-Browser Cookie Support
All YouTube-based scripts support multiple browsers for authentication:
- Chrome → Firefox → Brave → Edge → Safari → Chromium
- Automatically tries each browser in order
- Required for age-restricted content
- **Important:** Close all browsers before running scripts

### Error Handling
All scripts automatically handle and skip:
- ✅ Age-restricted videos
- ✅ Geo-blocked videos (not available in your country)
- ✅ Copyright-claimed videos
- ✅ Private/unavailable videos
- ✅ Rate limit detection (stops script to prevent bans)

### Database Optimization
- **Smart Skipping:** Checks database BEFORE making API calls (saves time/quota)
- **Deduplication:** Skips tracks already in database by Spotify ID or YouTube title
- **Multi-threading:** Process multiple tracks in parallel
- **Rate Limiting:** Built-in delays (0.05-0.3s) to avoid API bans

### Audio Analysis Features
All scripts extract 16 audio features using librosa:
- **Rhythm:** Tempo (BPM), Key, Beat Regularity
- **Spectral:** Brightness, Treble, Fullness, Dynamic Range
- **Temporal:** Percussiveness, Loudness
- **Harmonic/Percussive:** Warmth, Punch
- **Timbral:** Texture (MFCC)
- **Computed:** Energy, Danceability, Mood (Valence), Acousticness, Instrumentalness

---

## 📊 Database Schema

```sql
CREATE TABLE audio_features (
    id SERIAL PRIMARY KEY,
    spotify_track_id VARCHAR(50) UNIQUE NOT NULL,
    artist_name VARCHAR(255) NOT NULL,
    track_name VARCHAR(255) NOT NULL,
    
    -- Rhythm Features
    tempo_bpm REAL,
    key_musical SMALLINT,
    beat_regularity REAL,
    
    -- Spectral Features
    brightness_hz REAL,
    treble_hz REAL,
    fullness_hz REAL,
    dynamic_range REAL,
    
    -- Temporal Features
    percussiveness REAL,
    loudness REAL,
    
    -- Harmonic/Percussive
    warmth REAL,
    punch REAL,
    
    -- Timbral Features
    texture REAL,
    
    -- Computed Spotify-like Features (0-1)
    energy REAL,
    danceability REAL,
    mood_positive REAL,
    acousticness REAL,
    instrumental REAL,
    
    -- Metadata
    popularity SMALLINT,
    spotify_uri VARCHAR(100),
    youtube_match VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 🚀 Quick Start Guide

### 1. Setup
```bash
# Install dependencies
pip install librosa soundfile yt-dlp spotipy psycopg2-binary

# Configure secrets.json (in parent directory)
{
  "SPOTIFY_CLIENT_ID": "your_client_id",
  "SPOTIFY_CLIENT_SECRET": "your_client_secret",
  "DATABASE_URL": "postgresql://user:pass@host:port/db",
  "BASE_URL": "http://localhost:5001"
}
```

### 2. Process Your Music
```bash
# Start with your liked songs
python3 build_audio_features_youtube.py --threads 10 --tracks 100

# Add curated playlists
python3 build_audio_features_from_youtube_playlist.py --batch --threads 10

# Deep dive into artists
python3 build_audio_features_from_spotify_user.py "user_id" --threads 10 --extensive
```

### 3. Monitor Progress
- Script shows real-time progress with thread IDs
- Tracks are saved immediately after analysis
- Database count displayed at start
- Summary statistics at completion

---

## ⚠️ Important Notes

### Rate Limits
- **YouTube:** ~10,000 requests/day (scripts detect and stop automatically)
- **Spotify:** 30 requests/second (scripts have built-in delays)
- **Best Practice:** Use 10 threads max, avoid running multiple scripts simultaneously

### Browser Requirements
- Close ALL browsers before running scripts
- Scripts need to read browser cookies for YouTube authentication
- Cookies stored in browser's secure storage (read-only access)

### Processing Time
- **Per track:** 10-30 seconds (download + analysis)
- **100 tracks:** ~20-50 minutes with 10 threads
- **1000 tracks:** ~3-8 hours with 10 threads
- **Extensive mode:** 10-50x longer (processes entire artist discographies)

### Disk Space
- **Temporary:** ~50-100 MB per thread (audio files during analysis)
- **Permanent:** Audio files are IMMEDIATELY deleted after analysis
- **Database:** ~1 KB per track

---

## 💡 Pro Tips

1. **Start Small:** Test with `--tracks 10` before processing thousands
2. **Use Batch Mode:** Create `playlists.txt` for unattended processing
3. **Extensive Mode:** Use for curated playlists to build comprehensive artist coverage
4. **Check Database:** Scripts show count at start - verify tracks are being added
5. **Monitor Threads:** 10 threads is optimal for most systems
6. **Close Browsers:** Required for age-restricted content access

---

## 🐛 Troubleshooting

### "Chrome is not closed" error
- Close all Chrome windows and tabs
- Wait 5 seconds
- Run script again

### "Rate limit detected"
- YouTube has daily quota limits
- Wait 24 hours or try different IP
- Reduce threads to 4-5

### "Track not found on Spotify"
- YouTube video title doesn't match Spotify database
- Track might not be available on Spotify
- Script automatically skips and continues

### "Database connection failed"
- Check `DATABASE_URL` in secrets.json
- Verify PostgreSQL is running
- Test connection with `psql` command

---

## 📝 Additional Resources

- **Audio Features README:** `AUDIO_FEATURES_README.md` - Detailed feature explanations
- **YouTube Librosa README:** `YOUTUBE_LIBROSA_README.md` - YouTube processing details
- **Development Mode:** `DEVELOPMENT_MODE_INSTRUCTIONS.md` - Setup for development

---

## 🎯 Common Workflows

### Build Database from Scratch
```bash
# 1. Process your liked songs (personal taste baseline)
python3 build_audio_features_youtube.py --threads 10

# 2. Add curated playlists (expand genres)
python3 build_audio_features_from_youtube_playlist.py --batch --threads 10

# 3. Deep dive into favorite artists
python3 build_audio_features_from_spotify_user.py --artist "ARTIST_ID" --threads 10
```

### Expand Existing Database
```bash
# Add new playlists without duplicates (skips existing)
python3 build_audio_features_from_youtube_playlist.py "NEW_PLAYLIST" --threads 10

# Extensive mode on curated playlist (auto-skips processed artists)
python3 build_audio_features_from_spotify_user.py "user_id" --extensive --threads 10
```

### Update Metadata
```bash
# Re-analyze tracks with overwrite flag
python3 build_audio_features_youtube.py --overwrite --threads 10 --tracks 500
```

---

## 📈 Performance Benchmarks

**System:** M1 MacBook Pro, 16GB RAM, 100 Mbps internet

| Script | Tracks | Threads | Time | Success Rate |
|--------|--------|---------|------|--------------|
| YouTube (Liked) | 1000 | 10 | 4.2 hrs | 94% |
| YouTube Playlist | 278 | 10 | 1.8 hrs | 91% |
| Spotify User | 500 | 10 | 2.1 hrs | 96% |
| Extensive Mode | 50 tracks → 850 tracks | 10 | 8.5 hrs | 89% |

*Success rate = tracks successfully analyzed / total attempted (excludes unavailable videos)*

---

**Last Updated:** November 23, 2025

For issues or questions, check the existing README files or review the script source code for detailed docstrings.
