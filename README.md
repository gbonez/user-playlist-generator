# Music Discovery Web App

A web application that allows users to log in with their Spotify account and run a lite version of the music discovery script to find new music tailored to their taste.

## 🚀 Quick Production Deployment

**This app is ready for immediate deployment to Railway, Heroku, or any Python hosting service.**

### Perfect User Experience:
1. **User visits your app** → sees clean login page
2. **Clicks "Connect with Spotify"** → redirected to Spotify OAuth  
3. **Authorizes your app** → automatically redirected back
4. **Lands on dashboard** → can immediately run the music discovery script
5. **No manual steps** → seamless, professional flow

See `RAILWAY_DEPLOY.md` for deployment instructions.

## Features

- **Spotify OAuth Integration**: Secure login with Spotify account
- **Lite Script Version**: Simplified music discovery without database operations, SMS notifications, or whitelist functionality
- **Playlist Management**: Users can select their own playlists to add discovered music to
- **Real-time Status**: Live updates on script execution progress
- **Last.fm Integration**: Optional Last.fm username for enhanced recommendations
- **Clean Interface**: Modern, responsive web design with dark/light theme support

## How It Works

1. **Connect**: Users log in securely through Spotify's OAuth system
2. **Choose**: Select a playlist they own where new music will be added
3. **Configure**: Set number of tracks (1-50) and optionally provide Last.fm username
4. **Discover**: Algorithm finds new music based on:
   - User's liked songs and listening patterns
   - Artist playlists and user-curated playlists
   - Last.fm similar artists (if username provided)
   - Track validation to avoid duplicates
5. **Update**: Adds selected tracks and removes tracks older than 7 days

## Differences from Main Script

This lite version excludes:
- ❌ Database operations and storage
- ❌ SMS notifications upon completion
- ❌ Whitelist functionality for user profiles
- ❌ Complex caching and persistence

But includes:
- ✅ Core music discovery algorithm
- ✅ Spotify playlist manipulation
- ✅ Last.fm integration
- ✅ Track validation and duplicate prevention
- ✅ Web interface for easy use

## Setup

### Environment Variables

Required:
- `SPOTIFY_CLIENT_ID`: Your Spotify app client ID
- `SPOTIFY_CLIENT_SECRET`: Your Spotify app client secret

Optional:
- `LASTFM_API_KEY`: Last.fm API key for enhanced recommendations
- `BASE_URL`: Base URL for the app (default: http://localhost:5000)
- `FLASK_SECRET_KEY`: Secret key for Flask sessions (auto-generated if not provided)
- `FLASK_ENV`: Set to 'development' for debug mode
- `PORT`: Port to run the app on (default: 5000)

For Selenium (web scraping):
- `CHROME_BIN`: Path to Chrome binary (if needed)
- `CHROMEDRIVER_PATH`: Path to ChromeDriver (auto-detected if using chromedriver-binary)

### Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set up Spotify app:
   - Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new app
   - Add redirect URI: `http://localhost:5000/callback` (or your BASE_URL + /callback)
   - Copy Client ID and Client Secret to environment variables

3. (Optional) Set up Last.fm API:
   - Go to [Last.fm API](https://www.last.fm/api)
   - Create an account and get an API key
   - Set `LASTFM_API_KEY` environment variable

### Running

```bash
python app.py
```

Visit `http://localhost:5000` to use the application.

## File Structure

```
user-playlist-generator/
├── app.py                 # Flask web application
├── lite_script.py         # Simplified music discovery script
├── requirements.txt       # Python dependencies
├── README.md             # This file
└── templates/
    ├── base.html         # Base template with styling
    ├── login.html        # Login page
    ├── dashboard.html    # Main dashboard
    └── error.html        # Error pages
```

## Usage

1. Open the web app in your browser
2. Click "Connect with Spotify" to authenticate
3. Select a playlist you own from the dropdown
4. Choose how many new songs to add (1-50)
5. Optionally enter your Last.fm username for better recommendations
6. Click "Start Discovery" and watch the progress
7. New tracks will be added to your selected playlist automatically

## Security & Privacy

- Uses official Spotify OAuth for secure authentication
- No persistent storage of user data
- Tokens are session-based only
- All operations happen through your authorized Spotify account
- No external databases or data collection

## Troubleshooting

**Common Issues:**

1. **ChromeDriver not found**: Install chromedriver-binary or set CHROMEDRIVER_PATH
2. **Spotify authentication fails**: Check CLIENT_ID, CLIENT_SECRET, and redirect URI
3. **No playlists shown**: Make sure you own playlists or they are collaborative
4. **Script fails**: Check that your selected playlist is owned by you

**Debug Mode:**

Set `FLASK_ENV=development` to enable debug mode with detailed error messages.

## License

This project is for educational and personal use. Respect Spotify's and Last.fm's terms of service when using their APIs.