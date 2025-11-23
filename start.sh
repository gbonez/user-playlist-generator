#!/bin/bash
# start.sh - Easy startup script for local testing

echo "🎵 Starting Music Discovery Web App..."
echo ""

# Check if secrets.json exists
if [ -f "secrets.json" ]; then
    echo "✅ Found secrets.json - using it for configuration"
    
    # Extract values from secrets.json for validation
    CLIENT_ID=$(python3 -c "import json; f=open('secrets.json'); d=json.load(f); print(d.get('SPOTIFY_CLIENT_ID', ''))")
    CLIENT_SECRET=$(python3 -c "import json; f=open('secrets.json'); d=json.load(f); print(d.get('SPOTIFY_CLIENT_SECRET', ''))")
    BASE_URL_CONFIG=$(python3 -c "import json; f=open('secrets.json'); d=json.load(f); print(d.get('BASE_URL', 'http://localhost:5000'))")
    
    echo ""
elif [ -f "load_env.sh" ]; then
    echo "📁 Found load_env.sh - loading environment variables"
    source load_env.sh
    CLIENT_ID="$SPOTIFY_CLIENT_ID"
    CLIENT_SECRET="$SPOTIFY_CLIENT_SECRET"
    BASE_URL_CONFIG="$BASE_URL"
    echo ""
elif [ -f ".env" ]; then
    echo "📄 Found .env file - loading environment variables"
    set -o allexport
    source .env
    set +o allexport
    echo "✅ Loaded environment variables from .env"
    CLIENT_ID="$SPOTIFY_CLIENT_ID"
    CLIENT_SECRET="$SPOTIFY_CLIENT_SECRET" 
    BASE_URL_CONFIG="$BASE_URL"
    echo ""
else
    echo "⚠️  No configuration found!"
    echo "   Please create one of:"
    echo "   - secrets.json (recommended for local testing)"
    echo "   - .env file"
    echo "   - Set environment variables manually"
    echo ""
    echo "Required variables:"
    echo "   SPOTIFY_CLIENT_ID"
    echo "   SPOTIFY_CLIENT_SECRET"
    echo ""
    exit 1
fi

# Set some defaults if not set
export BASE_URL=${BASE_URL_CONFIG:-"http://localhost:5000"}
export FLASK_ENV=${FLASK_ENV:-"development"}
export PORT=${PORT:-5000}

echo "🚀 Configuration:"
echo "   Base URL: $BASE_URL"
echo "   Environment: $FLASK_ENV"
echo "   Port: $PORT"
echo ""

# Check if required variables are set
if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "❌ Missing required Spotify credentials!"
    echo "   Please check your configuration file."
    echo "   CLIENT_ID: ${CLIENT_ID:0:10}..."
    echo "   CLIENT_SECRET: ${CLIENT_SECRET:0:10}..."
    exit 1
fi

echo "✅ Spotify credentials found"
echo "   Client ID: ${CLIENT_ID:0:10}..."
echo "   Client Secret: ${CLIENT_SECRET:0:10}..."
echo ""

echo "✅ Starting Flask application..."
echo "   Open your browser to: $BASE_URL"
echo "   Press Ctrl+C to stop"
echo ""

# Start the Flask app
python3 app.py