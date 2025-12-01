# Genre Matching System Update - Summary

## Overview
This update transforms the genre matching system to use database caching and implements a new progressive genre matching algorithm.

## Changes Made

### 1. Database Schema (`create_artist_genres_table.sql`)
**Updated SQL script with:**
- `artist_name` VARCHAR(500) - Primary key with UNIQUE constraint
- `genres` TEXT[] - Array of genre strings
- `created_at` TIMESTAMP - Record creation time
- `updated_at` TIMESTAMP - Last update time
- **Indexes:**
  - `idx_artist_genres_artist_name` - B-tree index for fast lookups
  - `idx_artist_genres_genres` - GIN index for array searches

**To run:** Execute the SQL script against your PostgreSQL database
```bash
psql $DATABASE_URL < db_creation/create_artist_genres_table.sql
```

---

### 2. Genre Fetching (`lite_script.py` - `get_artist_genres_live()`)
**New behavior:**
1. **Check database first** - Query artist_genres table
2. **Fetch from APIs** if not in database - Spotify, Last.fm, MusicBrainz, Discogs
3. **Save to database** - Cache results for future use

**Benefits:**
- Drastically reduced API calls (only fetch once per artist)
- Faster genre lookups for previously seen artists
- Automatic caching with ON CONFLICT updates

---

### 3. Genre Matching Logic (`lite_script.py` - recommendation generation)
**New progressive matching algorithm:**

#### Phase 1: 3-Genre Matching (100 songs)
- Check up to **100 candidate songs** for **3+ matching genres**
- If match found → Select track and continue
- If no match after 100 songs → Proceed to Phase 2

#### Phase 2: 1-Genre Matching (Indefinite)
- Restart search with **1+ genre requirement**
- Continue **indefinitely** through all candidates until match found
- If match found → Select track and continue

#### Special Case: No Seed Genres
- If seed artist has **no genres** → Use closest distance match regardless of genre
- Still fetch candidate genres for display purposes

**Key improvements:**
- Stricter initial matching (3 genres)
- Fallback ensures a match is always found (1 genre)
- No arbitrary attempt limits - searches until match found
- Handles edge case of artists without genres

---

### 4. Dashboard Display (`dashboard.html`)
**Genre tags now displayed for each song:**
- Shows up to 3 genres per song
- Styled as green rounded tags below artist name
- Example: `indie-rock` `alternative` `rock`

**Visual design:**
- Green background (`rgba(29, 185, 84, 0.2)`)
- Green text (`#1db954`)
- Small rounded pills
- Automatically wraps on small screens

---

## Usage

### Running the SQL Migration
```bash
# Connect to your database and run the script
psql $DATABASE_URL < db_creation/create_artist_genres_table.sql
```

### Genre Matching Behavior

**Example 1: Seed artist with genres**
```
[GENRE] Fetching genres for: Arctic Monkeys
  Database: ['indie-rock', 'alternative', 'rock']
[GENRE PHASE 1] Checking up to 100 songs for 3+ matching genres...
[MATCH] ✓ Found 3 genre matches (required 3): ['indie-rock', 'alternative', 'rock']
```

**Example 2: Fallback to 1-genre after 100 songs**
```
[GENRE PHASE 1] Checking up to 100 songs for 3+ matching genres...
[GENRE PHASE 2] No 3-genre match after 100 songs. Searching with 1+ genre indefinitely...
[MATCH] ✓ Found 1 genre matches (required 1): ['rock']
```

**Example 3: Seed artist with no genres**
```
[WARN] Seed artist 'Unknown Band' has no genres, using closest distance match
[SUCCESS] ✓ Selected: Song Title by Some Artist (distance: 0.1234)
[INFO] Genres: ['pop', 'electronic']
```

---

## API Impact

### Before Update
- **Every recommendation:** 4 API calls per artist (Spotify, Last.fm, MusicBrainz, Discogs)
- **50 songs × 100 candidates:** ~20,000 API calls per generation

### After Update
- **First time:** 4 API calls per artist → Saved to database
- **Subsequent times:** 0 API calls (database lookup)
- **50 songs × 100 candidates:** ~200 API calls (only new artists)

**Estimated reduction: 99% fewer API calls for subsequent generations**

---

## Technical Details

### Database Operations
```python
# Check database
SELECT genres FROM artist_genres WHERE artist_name = %s

# Save to database (with conflict handling)
INSERT INTO artist_genres (artist_name, genres, created_at, updated_at)
VALUES (%s, %s, NOW(), NOW())
ON CONFLICT (artist_name) 
DO UPDATE SET genres = EXCLUDED.genres, updated_at = NOW()
```

### Genre Data Structure
```python
# In lite_script.py
added_songs.append({
    'title': 'Song Name',
    'artist': 'Artist Name',
    'spotify_url': 'https://...',
    'based_on_artist': 'Seed Artist',
    'genres': ['genre1', 'genre2', 'genre3']  # NEW FIELD
})
```

### Dashboard Display
```javascript
// Genres rendered as tags
if (song.genres && song.genres.length > 0) {
    const genreTags = song.genres.map(genre => 
        `<span class="genre-tag">${genre}</span>`
    ).join('');
}
```

---

## Testing Checklist

- [ ] Run SQL migration script
- [ ] Test genre matching with known artist
- [ ] Verify database caching (check logs for "Database:" vs "Fetching from APIs")
- [ ] Test 3-genre matching phase
- [ ] Test fallback to 1-genre matching
- [ ] Test artist with no genres
- [ ] Verify genres display on dashboard
- [ ] Check API rate limits (should see dramatic reduction)

---

## Files Modified

1. **`create_artist_genres_table.sql`** - Added timestamps and better indexes
2. **`lite_script.py`** - Updated `get_artist_genres_live()` and genre matching logic
3. **`dashboard.html`** - Added genre display in song cards

---

## Notes

- Genre data persists across sessions (cached in database)
- Old API-only code removed - now database-first approach
- Genre expansion still active (parent/child genre relationships)
- Database connection errors gracefully handled (falls back to API-only mode)
