# Database Modifier

A comprehensive web-based tool for searching, editing, and managing entries in the audio features database.

## Features

### 🔍 Extensive Search Capabilities

#### Text Search
- **Track Name**: Search by full or partial track name (case-insensitive)
- **Artist Name**: Search by full or partial artist name (case-insensitive)
- **Spotify Track ID**: Search by Spotify track ID

#### Range-Based Filters
- **Tempo (BPM)**: Search tracks within a specific tempo range
- **Energy (0-1)**: Filter by energy level
- **Danceability (0-1)**: Filter by danceability score
- **Mood Positive (0-1)**: Filter by valence/positivity
- **Acousticness (0-1)**: Filter by acoustic vs electric
- **Instrumental (0-1)**: Filter by instrumental vs vocal content
- **Popularity (0-100)**: Filter by track popularity

#### Other Filters
- **Musical Key**: Filter by specific musical key (C, C#, D, etc.)
- **Results per page**: Choose 10, 25, 50, or 100 results per page

### ✏️ Edit Capabilities

Edit any of the following fields for each track:
- Track name and artist name
- Tempo (BPM)
- Musical key
- Energy, danceability, mood
- Acousticness, instrumental
- Popularity
- Brightness (Hz)
- Loudness

### 🗑️ Delete Functionality

- Remove unwanted or duplicate tracks from the database
- Confirmation prompt before deletion to prevent accidents

### 📄 Pagination

- Navigate through large result sets easily
- Customizable results per page
- Page counter shows current position

## Usage

### Accessing the Tool

1. Navigate to the main dashboard
2. Click your profile avatar in the top-right corner
3. Select "🗄️ Database Modifier" from the dropdown menu

### Searching

1. **Enter search criteria** in any combination:
   - Use text fields for track/artist names
   - Use range fields (min/max) for numeric values
   - Select a musical key if desired

2. **Click "🔍 Search Database"** to execute the search

3. **View results** showing:
   - Track and artist names
   - Key audio features (tempo, energy, danceability, etc.)
   - Spotify track ID
   - Edit and Delete buttons

### Editing Tracks

1. **Click "Edit"** on any track in the results
2. **Modify fields** in the modal dialog
3. **Click "Save Changes"** to commit updates
4. Results will automatically refresh

### Deleting Tracks

1. **Click "Delete"** on any track in the results
2. **Confirm** the deletion (cannot be undone)
3. Results will automatically refresh

### Tips for Effective Searching

- **Combine multiple filters** for precise results (e.g., artist name + tempo range)
- **Use partial names** - searches are case-insensitive and match substrings
- **Leave fields empty** that you don't want to filter by
- **Use "Clear All"** to reset all search fields
- **Press Enter** in any field to trigger search

## API Endpoints

The database modifier uses the following backend endpoints:

### Search
```
GET /api/database/search
```
Parameters: track_name, artist_name, spotify_id, tempo_min, tempo_max, energy_min, energy_max, etc.

### Update
```
PUT /api/database/update/<track_id>
```
Body: JSON object with fields to update

### Delete
```
DELETE /api/database/delete/<track_id>
```

## Database Schema

The tool interacts with the `audio_features` table containing:

**Metadata:**
- `id` (Primary Key)
- `spotify_track_id` (Unique)
- `artist_name`
- `track_name`
- `popularity`
- `created_at`

**Rhythm Features:**
- `tempo_bpm` - Beats per minute
- `key_musical` - Musical key (0-11)
- `beat_regularity` - Rhythm consistency

**Spectral Features:**
- `brightness_hz` - Spectral centroid
- `treble_hz` - High frequency content
- `fullness_hz` - Spectral bandwidth
- `dynamic_range` - Contrast

**Temporal Features:**
- `percussiveness` - Sharp transients
- `loudness` - Overall volume

**Computed Features:**
- `energy` - Intensity (0-1)
- `danceability` - Groove (0-1)
- `mood_positive` - Valence (0-1)
- `acousticness` - Acoustic vs electric (0-1)
- `instrumental` - Instrumental content (0-1)

## Security Notes

- Currently no authentication required for database operations
- Consider adding authentication for production use
- All operations are logged on the backend
- Use with caution as deletions cannot be undone

## Future Enhancements

Potential features to add:
- Bulk edit/delete operations
- Export search results to CSV
- Advanced filtering with AND/OR logic
- Undo functionality for deletions
- User authentication and permissions
- Audit log for all changes
- Duplicate detection tool
- Batch import/export

## Troubleshooting

**Search returns no results:**
- Try broadening your search criteria
- Check that range values are valid (min < max)
- Use "Clear All" and try again

**Edit/Delete fails:**
- Check browser console for errors
- Ensure database connection is active
- Verify track ID exists in database

**Page loads slowly:**
- Reduce results per page
- Add more specific search filters
- Check database connection speed
