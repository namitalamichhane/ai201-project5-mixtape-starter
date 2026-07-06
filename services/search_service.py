"""
services/search_service.py — Mixtape

Handles song search logic.
"""

from app import db
from models import Song, Tag, song_tags


def search_songs(query: str) -> list[dict]:
    """
    Search for songs by title or artist name.
    """
    results = (
        db.session.query(Song)
        .filter(
            db.or_(
                Song.title.ilike(f"%{query}%"),
                Song.artist.ilike(f"%{query}%"),
            )
        )
        .all()
    )
    return [song.to_dict() for song in results]

def get_song(song_id: str) -> dict:
    """
    Get a single song by ID.

    Args:
        song_id: The UUID of the song.

    Returns:
        A song dict, or raises ValueError if not found.
    """
    song = db.session.get(Song, song_id)
    if not song:
        raise ValueError(f"Song {song_id} not found")
    return song.to_dict()
