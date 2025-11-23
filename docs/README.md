# Music Discovery App - Decoupled Architecture

This is the static frontend for the Music Discovery App, designed to be hosted on GitHub Pages while connecting to a Flask backend API hosted on Railway.

## Architecture

- **Frontend**: Static HTML/CSS/JS hosted on GitHub Pages (`docs/` folder)
- **Backend**: Flask API hosted on Railway (main app.py)
- **Communication**: CORS-enabled REST API calls from frontend to backend

## Deployment Steps

### 1. Deploy Backend to Railway (Already Done ✅)

Your backend is already deployed at:
```
https://release-radar-scripts-production.up.railway.app
```

Make sure these environment variables are set in Railway:
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `BASE_URL` (should be your Railway URL)
- `FLASK_SECRET_KEY`
- `LASTFM_API_KEY` (optional)

### 2. Update Spotify App Redirect URI

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Edit your app
3. Add **BOTH** redirect URIs:
   - `https://release-radar-scripts-production.up.railway.app/callback` (backend)
   - `https://gbonez.github.io/user-playlist-generator/` (frontend - for OAuth redirect back)

### 3. Enable GitHub Pages

1. Push this code to your GitHub repository
2. Go to your repository on GitHub
3. Navigate to **Settings** → **Pages**
4. Under "Source", select:
   - Branch: `main`
   - Folder: `/docs`
5. Click **Save**
6. Your frontend will be available at: `https://gbonez.github.io/user-playlist-generator/`

### 4. Deploy Backend Changes

Push the updated code and redeploy on Railway:

```bash
git add .
git commit -m "Add CORS support and static frontend"
git push origin main
```

Railway will automatically redeploy with the CORS changes.

## How It Works

1. User visits GitHub Pages URL: `https://gbonez.github.io/user-playlist-generator/`
2. Frontend loads and checks authentication status via API call to Railway backend
3. User clicks "Connect with Spotify" → redirected to Railway backend `/login`
4. Backend handles Spotify OAuth → redirects back to backend `/callback`
5. Backend sets session cookie → redirects user back to GitHub Pages
6. Frontend detects authentication and loads dashboard
7. All subsequent API calls (playlists, run script, job status) go through Railway backend
8. Session is maintained via cookies with `credentials: 'include'` in fetch calls

## Files

- `index.html` - Single page app with login and dashboard views
- `style.css` - Modern, Spotify-inspired styling
- `app.js` - Frontend logic and API communication
- `README.md` - This file

## Local Testing

To test locally before deploying to GitHub Pages:

1. Open `docs/app.js` and temporarily set:
   ```javascript
   const API_BASE_URL = 'http://localhost:5000';
   ```

2. Run backend locally:
   ```bash
   python app.py
   ```

3. Serve frontend locally:
   ```bash
   cd docs
   python -m http.server 8000
   ```

4. Open `http://localhost:8000` in your browser

## API Endpoints Used

The frontend communicates with these backend endpoints:

- `GET /api/playlists` - Get user's Spotify playlists
- `POST /api/run_script` - Start music discovery script
- `GET /api/job_status/<job_id>` - Check script execution status
- `GET /login` - Initiate Spotify OAuth
- `GET /callback` - Handle Spotify OAuth callback
- `GET /logout` - Clear session

## Troubleshooting

### CORS Errors
- Make sure Railway backend has the updated code with flask-cors enabled
- Check that `API_BASE_URL` in `app.js` matches your Railway URL

### Authentication Not Working
- Verify both redirect URIs are added in Spotify Dashboard
- Check that cookies are enabled in browser
- Ensure Railway backend has `BASE_URL` environment variable set

### Can't Load Playlists
- Check browser console for API errors
- Verify Railway backend is running
- Test backend directly: `curl https://your-railway-url.up.railway.app/api/playlists`
