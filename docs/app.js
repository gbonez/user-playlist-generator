// Configuration - Use root paths (Railway URL)
const API_BASE_URL = '';  // Empty string means same origin (root path)

// State management
let currentJobId = null;
let statusCheckInterval = null;

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    checkAuthStatus();
});

// Check if user is authenticated
async function checkAuthStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/auth/status`, {
            credentials: 'include',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (response.ok) {
            const data = await response.json();
            if (data.authenticated) {
                // User is authenticated, show dashboard
                showDashboard(data.user);
                loadPlaylists();
            } else {
                showLogin();
            }
        } else {
            // User not authenticated, show login
            showLogin();
        }
    } catch (error) {
        console.error('Error checking auth status:', error);
        showLogin();
    }
}

// Show login page
function showLogin() {
    document.getElementById('loginPage').classList.add('active');
    document.getElementById('dashboardPage').classList.remove('active');
}

// Show dashboard page
function showDashboard(user) {
    document.getElementById('loginPage').classList.remove('active');
    document.getElementById('dashboardPage').classList.add('active');

    // Update user name if provided
    if (user && user.display_name) {
        document.getElementById('userName').textContent = `Welcome, ${user.display_name}!`;
    }
}

// Login with Spotify
function loginSpotify() {
    // Redirect to backend OAuth endpoint
    window.location.href = `${API_BASE_URL}/login`;
}

// Logout
async function logout() {
    try {
        await fetch(`${API_BASE_URL}/logout`, {
            credentials: 'include'
        });
    } catch (error) {
        console.error('Logout error:', error);
    }

    // Clear any running job checks
    if (statusCheckInterval) {
        clearInterval(statusCheckInterval);
        statusCheckInterval = null;
    }

    showLogin();
}

// Load user's playlists
async function loadPlaylists() {
    const select = document.getElementById('playlistSelect');
    select.innerHTML = '<option value="">Loading playlists...</option>';

    try {
        const response = await fetch(`${API_BASE_URL}/api/playlists`, {
            credentials: 'include',
            headers: {
                'Accept': 'application/json'
            }
        }); if (!response.ok) {
            throw new Error('Failed to load playlists');
        }

        const data = await response.json();

        if (data.playlists && data.playlists.length > 0) {
            select.innerHTML = '<option value="">Select a playlist...</option>';

            data.playlists.forEach(playlist => {
                const option = document.createElement('option');
                option.value = playlist.id;
                option.textContent = `${playlist.name} (${playlist.tracks_total} tracks)${playlist.is_owner ? '' : ' - Collaborative'}`;
                select.appendChild(option);
            });
        } else {
            select.innerHTML = '<option value="">No playlists found</option>';
        }
    } catch (error) {
        console.error('Error loading playlists:', error);
        select.innerHTML = '<option value="">Error loading playlists</option>';
        showError('Failed to load playlists. Please try refreshing the page.');
    }
}

// Start discovery process
async function startDiscovery() {
    const playlistId = document.getElementById('playlistSelect').value;
    const maxSongs = parseInt(document.getElementById('maxSongs').value);
    const lastfmUsername = document.getElementById('lastfmUsername').value.trim();

    // Validation
    if (!playlistId) {
        showError('Please select a playlist');
        return;
    }

    if (maxSongs < 1 || maxSongs > 50) {
        showError('Number of songs must be between 1 and 50');
        return;
    }

    // Disable start button
    const startBtn = document.getElementById('startBtn');
    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';

    // Show progress section
    const progressSection = document.getElementById('progressSection');
    progressSection.classList.remove('hidden');
    document.getElementById('resultDetails').classList.add('hidden');

    try {
        const response = await fetch(`${API_BASE_URL}/api/run_script`, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify({
                playlist_id: playlistId,
                max_songs: maxSongs,
                lastfm_username: lastfmUsername || null
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Failed to start discovery');
        }

        const data = await response.json();
        currentJobId = data.job_id;

        // Update status message
        document.getElementById('statusMessage').textContent = 'Discovery started! Finding new music...';
        document.getElementById('progressFill').style.width = '30%';

        // Start polling for status
        statusCheckInterval = setInterval(() => checkJobStatus(currentJobId), 2000);

    } catch (error) {
        console.error('Error starting discovery:', error);
        showError(error.message);

        // Re-enable button
        startBtn.disabled = false;
        startBtn.textContent = 'Start Discovery';
        progressSection.classList.add('hidden');
    }
}

// Check job status
async function checkJobStatus(jobId) {
    try {
        const response = await fetch(`${API_BASE_URL}/api/job_status/${jobId}`, {
            credentials: 'include',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error('Failed to get job status');
        }

        const job = await response.json();

        // Update progress
        if (job.status === 'running') {
            document.getElementById('statusMessage').textContent = `Analyzing your music taste and finding new tracks...`;
            document.getElementById('progressFill').style.width = '60%';
        } else if (job.status === 'completed') {
            // Job completed
            clearInterval(statusCheckInterval);
            statusCheckInterval = null;

            document.getElementById('progressFill').style.width = '100%';
            document.getElementById('statusMessage').textContent = 'Discovery completed!';

            // Show results
            if (job.result) {
                showResults(job.result);
            }

            // Re-enable button
            const startBtn = document.getElementById('startBtn');
            startBtn.disabled = false;
            startBtn.textContent = 'Start Discovery';

        } else if (job.status === 'failed') {
            // Job failed
            clearInterval(statusCheckInterval);
            statusCheckInterval = null;

            showError(job.error || 'Discovery failed. Please try again.');

            // Re-enable button
            const startBtn = document.getElementById('startBtn');
            startBtn.disabled = false;
            startBtn.textContent = 'Start Discovery';

            document.getElementById('progressSection').classList.add('hidden');
        }

    } catch (error) {
        console.error('Error checking job status:', error);
        clearInterval(statusCheckInterval);
        statusCheckInterval = null;
        showError('Lost connection to server. Please refresh and try again.');
    }
}

// Show results
function showResults(result) {
    const resultDetails = document.getElementById('resultDetails');
    resultDetails.classList.remove('hidden');

    let html = '<h4>✨ Discovery Complete!</h4><ul>';

    if (result.tracks_added !== undefined) {
        html += `<li>Added ${result.tracks_added} new tracks</li>`;
    }

    if (result.tracks_removed !== undefined) {
        html += `<li>Removed ${result.tracks_removed} old tracks (7+ days)</li>`;
    }

    if (result.playlist_name) {
        html += `<li>Updated playlist: "${result.playlist_name}"</li>`;
    }

    if (result.message) {
        html += `<li>${result.message}</li>`;
    }

    html += '</ul>';
    resultDetails.innerHTML = html;
}

// Show error message
function showError(message) {
    const statusMessage = document.getElementById('statusMessage');
    statusMessage.innerHTML = `<div class="error">${message}</div>`;
}

// No need to handle OAuth callback redirect anymore - backend handles it
