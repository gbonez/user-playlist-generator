# Spotify App Setup Instructions

## Quick Setup for Local Testing

### 1. Update Spotify App Redirect URI
Go to your Spotify app at: https://developer.spotify.com/dashboard
- Click on your app (the one with client ID: da06cf57559f4a4d84298837e7000103)
- Click "Settings" 
- In "Redirect URIs", add: `http://localhost:5000/callback`
- Click "Save"

### 2. Start the Application
```bash
cd user-playlist-generator
./start.sh
```

Or manually:
```bash
cd user-playlist-generator
python3 app.py
```

### 3. Open in Browser
Visit: http://localhost:5000

## What Users Will Experience

1. **Login Page**: Users see a welcome page and click "Connect with Spotify"
2. **Spotify OAuth**: They'll be redirected to Spotify to authorize your app
3. **Dashboard**: After authorization, they return to your app and can:
   - See their Spotify profile info
   - Select one of their playlists 
   - Set number of songs to add (1-50)
   - Optionally enter their Last.fm username
   - Click "Start Discovery" to run the lite script
4. **Real-time Status**: They'll see live updates as the script runs
5. **Results**: When complete, they'll see how many songs were added/removed

## How It Works

- **Your app credentials** authenticate the app with Spotify
- **Each user logs in** with their own Spotify account  
- **Users can only modify** their own playlists
- **The script runs** with their permissions on their music data
- **No data is stored** - it's all processed in real-time

## Files Added for Local Testing

- `secrets.json` - Your configuration (gitignored)
- `load_env.sh` - Environment loader script (gitignored)  
- `.gitignore` - Prevents sensitive files from being committed
- `start.sh` - Easy startup script

## Security Notes

- `secrets.json` is in `.gitignore` so it won't be committed to git
- Users authenticate directly with Spotify - you never see their passwords
- Each user can only access their own playlists and data