# GitHub Pages + Railway Deployment Guide

## Quick Setup Checklist

### ✅ Step 1: Deploy Backend to Railway

1. **Push code to GitHub:**
   ```bash
   git add .
   git commit -m "Add decoupled frontend/backend architecture with CORS"
   git push origin main
   ```

2. **Deploy on Railway:**
   - Railway will auto-detect the changes and redeploy
   - Or manually trigger: `railway up`

3. **Set environment variables on Railway:**
   ```bash
   railway variables set SPOTIFY_CLIENT_ID=your_client_id
   railway variables set SPOTIFY_CLIENT_SECRET=your_client_secret
   railway variables set BASE_URL=https://release-radar-scripts-production.up.railway.app
   railway variables set FLASK_SECRET_KEY=your_secret_key
   railway variables set LASTFM_API_KEY=your_lastfm_key
   ```

### ✅ Step 2: Update Spotify App Settings

1. Go to: https://developer.spotify.com/dashboard
2. Select your app
3. Click "Edit Settings"
4. Add **both** Redirect URIs:
   ```
   https://release-radar-scripts-production.up.railway.app/callback
   https://gbonez.github.io/user-playlist-generator/
   ```
5. Click "Save"

### ✅ Step 3: Enable GitHub Pages

1. Go to your GitHub repository: https://github.com/gbonez/user-playlist-generator
2. Click **Settings** tab
3. Click **Pages** in left sidebar
4. Under "Build and deployment":
   - Source: **Deploy from a branch**
   - Branch: **main**
   - Folder: **/docs**
5. Click **Save**
6. Wait 1-2 minutes for deployment

### ✅ Step 4: Access Your App

**Frontend URL (GitHub Pages):**
```
https://gbonez.github.io/user-playlist-generator/
```

**Backend API URL (Railway):**
```
https://release-radar-scripts-production.up.railway.app
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  User's Browser                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  GitHub Pages (Static Frontend)                 │   │
│  │  https://gbonez.github.io/user-playlist-...    │   │
│  │                                                  │   │
│  │  - index.html (UI)                              │   │
│  │  - style.css (Styling)                          │   │
│  │  - app.js (API calls)                           │   │
│  └──────────────────┬──────────────────────────────┘   │
│                     │                                    │
│                     │ API Calls (CORS enabled)          │
│                     │ - GET /api/playlists              │
│                     │ - POST /api/run_script            │
│                     │ - GET /api/job_status/<id>        │
│                     ▼                                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Railway (Flask Backend API)                    │   │
│  │  https://release-radar-scripts-production...   │   │
│  │                                                  │   │
│  │  - app.py (Flask + CORS)                        │   │
│  │  - lite_script.py (Music discovery logic)      │   │
│  │  - Session management                           │   │
│  │  - Spotify OAuth handling                       │   │
│  └──────────────────┬──────────────────────────────┘   │
│                     │                                    │
│                     │ OAuth & API Calls                 │
│                     ▼                                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Spotify API                                     │   │
│  │  - User authentication                           │   │
│  │  - Playlist management                           │   │
│  │  - Music data                                    │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Testing

### Test Backend API
```bash
# Check if backend is running
curl https://release-radar-scripts-production.up.railway.app/

# Test API endpoint (should return 401 without auth)
curl https://release-radar-scripts-production.up.railway.app/api/playlists
```

### Test Frontend
1. Open: https://gbonez.github.io/user-playlist-generator/
2. Open browser DevTools (F12)
3. Check Console for any errors
4. Try clicking "Connect with Spotify"

## Troubleshooting

### Issue: CORS errors in browser console

**Solution:**
- Verify Railway backend is deployed with latest code
- Check CORS configuration in `app.py`
- Ensure `API_BASE_URL` in `docs/app.js` matches your Railway URL

### Issue: Redirect URI mismatch

**Solution:**
- Check Spotify Dashboard has BOTH URIs added:
  - Backend callback: `https://release-radar-scripts-production.up.railway.app/callback`
  - Frontend: `https://gbonez.github.io/user-playlist-generator/`

### Issue: GitHub Pages shows 404

**Solution:**
- Wait 1-2 minutes after enabling Pages
- Verify `/docs` folder exists in main branch
- Check repository Settings → Pages shows deployment status

### Issue: Authentication not persisting

**Solution:**
- Check browser allows third-party cookies
- Verify `credentials: 'include'` in all fetch calls
- Ensure Railway `BASE_URL` environment variable is set

## Local Development

### Test locally before deploying:

1. **Run backend:**
   ```bash
   python app.py
   ```

2. **Update `docs/app.js` temporarily:**
   ```javascript
   const API_BASE_URL = 'http://localhost:5000';
   ```

3. **Serve frontend:**
   ```bash
   cd docs
   python -m http.server 8000
   ```

4. **Test:**
   - Open http://localhost:8000
   - Should connect to local backend

5. **Before deploying, change back:**
   ```javascript
   const API_BASE_URL = 'https://release-radar-scripts-production.up.railway.app';
   ```

## Next Steps

After successful deployment:

1. ✅ Test the full OAuth flow
2. ✅ Try running discovery on a test playlist
3. ✅ Monitor Railway logs for any errors
4. ✅ Share the GitHub Pages URL with users!

## Support

- Railway Docs: https://docs.railway.app
- GitHub Pages Docs: https://docs.github.com/pages
- Flask-CORS Docs: https://flask-cors.readthedocs.io/
