# Music Discovery Web App - Railway Deployment

## Quick Railway Deployment

### 1. Environment Variables to Set in Railway:

**Required:**
```
SPOTIFY_CLIENT_ID=da06cf57559f4a4d84298837e7000103
SPOTIFY_CLIENT_SECRET=60e275b1b5ea4f219c58a7a8f34300b2
BASE_URL=https://your-railway-app-url.railway.app
```

**Optional but Recommended:**
```
LASTFM_API_KEY=e763042a7bcfc92596504933d213014c
FLASK_SECRET_KEY=your_random_secret_here
FLASK_ENV=production
CHROME_BIN=/usr/bin/google-chrome
CHROMEDRIVER_PATH=/usr/bin/chromedriver
```

### 2. Update Spotify App Redirect URI:

1. Go to https://developer.spotify.com/dashboard
2. Click on your app (ID: da06cf57...)  
3. Add redirect URI: `https://your-railway-app-url.railway.app/callback`
4. Save changes

### 3. Deploy Steps:

1. **Create new Railway project**
2. **Connect to GitHub repo** or **deploy from CLI**
3. **Set environment variables** in Railway dashboard
4. **Deploy** - Railway will automatically:
   - Detect Python app
   - Install requirements from `requirements.txt`
   - Run using `Procfile` 
   - Build with Python 3.11

### 4. Perfect Production Flow:

✅ **User Experience:**
1. User visits `https://your-app.railway.app`
2. Sees login page, clicks "Connect with Spotify"
3. Redirected to Spotify OAuth
4. After authorization, **automatically redirected back** to your app
5. Lands on dashboard, ready to use the script
6. **No manual steps or copy-pasting required**

### 5. Files Ready for Production:

- ✅ `app.py` - Clean Flask app without manual callback workarounds
- ✅ `lite_script.py` - Music discovery engine
- ✅ `templates/` - Clean UI without manual auth flows
- ✅ `Procfile` - Railway deployment config
- ✅ `runtime.txt` - Python version
- ✅ `requirements.txt` - Dependencies
- ✅ `.gitignore` - Protects secrets

The local redirect issue will be completely solved in production because:
- Your app runs on `https://your-app.railway.app`
- Spotify redirects to `https://your-app.railway.app/callback` 
- Same domain = seamless flow!

## Testing Locally (Optional)

If you want to test the full flow locally, you'd need to:
1. Set `BASE_URL=http://localhost:5001` in secrets.json
2. Add `http://localhost:5001/callback` to your Spotify app redirect URIs
3. But this isn't necessary - production deployment will work perfectly!