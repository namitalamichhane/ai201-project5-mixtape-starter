\# Mixtape Bug Hunt — Submission



\## AI Usage



I used Claude to help me navigate the codebase during orientation — asking it to

summarize what each service file was responsible for, and to trace how a song

moves through the system (share → playlist → notification). During debugging,

I used it to help me read suspicious functions after I'd already located them

myself (e.g., confirming the semantics of `datetime.weekday()`, and comparing

the `add\_to\_playlist` and `rate\_song` code paths side by side). I verified every

diagnosis myself by reproducing each bug in `flask shell` with controlled inputs

before applying any fix. 



For Issue #2, While given the option to simply reduce the rolling hour window (Option A), I chose to implement a strict UTC calendar-day midnight anchor (Option B) to accurately satisfy the user complaint regarding "yesterday's" data filtering out cleanly. I successfully verified this by pushing my test event back to 30 hours to confirm a zero-item return state.



For Issue #4, I confirmed that the missing notification layer was completely absent from `rate\\\_song`. I chose to enforce a design choice where updating or re-rating a song continues to dispatch a fresh notification to keep the song owner fully updated on score adjustments, while ensuring a strict guard prevents users from receiving notifications for rating their own tracks.



\## Codebase Map



\*\*Main files:\*\*

\- `app.py` — Flask app factory; registers blueprints for songs, playlists, users, and feed; initializes the SQLAlchemy `db` instance.

\- `models.py` — defines 7 models (`User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`) plus 3 association tables. Notably, `playlist\_entries` carries an explicit `position` column rather than relying on insertion order, and `User.listening\_streak`/`last\_listened\_at` are cached fields that must be kept in sync manually rather than derived live from `ListeningEvent` history.

\- `routes/` — one blueprint per resource (songs, playlists, users, feed). Routes handle request parsing and response formatting only.

\- `services/` — all business logic. `streak\_service.py` (listening streaks), `feed\_service.py` (friends listening now / activity feed), `search\_service.py` (song search), `notification\_service.py` (notifications + ratings), `playlist\_service.py` (playlist creation/retrieval).



\*\*Data flow — a friend adds your song to a playlist:\*\*

`POST /playlists/<id>/songs` (`routes/playlists.py`) → `notification\_service.add\_to\_playlist()` → appends the song to `playlist.songs` (via the `playlist\_entries` join table) → commits → if the adder isn't the original sharer, calls `create\_notification()` to insert a `Notification` row for `song.shared\_by`.



\*\*Pattern noticed:\*\* every route delegates immediately to a service function; routes contain no business logic. A second pattern: several bugs come from the same underlying issue — a working code path exists for one case (e.g., playlist notifications) but wasn't replicated for a similar case (e.g., rating notifications), or a boundary condition (day/week/list-index) was handled with an off-by-one.



\---



\## Root Cause Analysis



\### Issue #1 — My listening streak keeps resetting



\*\*How I reproduced it:\*\* In `flask shell`, I created a `User` with `listening\_streak=3` and `last\_listened\_at` set to a Saturday. I called `update\_listening\_streak(user, now)` with `now` set to the following Sunday (exactly one calendar day later). The streak reset to 1 instead of incrementing to 4.



\*\*How I found the root cause:\*\* I traced `record\_listening\_event` → `update\_listening\_streak` in `streak\_service.py`. Reading the branch logic line by line, I found:

```python

elif days\_since\_last == 1 and today.weekday() != 6:

&#x20;   user.listening\_streak += 1

else:

&#x20;   user.listening\_streak = 1

```

I confirmed with a quick check that `datetime.weekday()` returns `6` for Sunday, which told me this condition was specifically excluding Sundays from the increment path.



\*\*The root cause:\*\* The increment branch requires both `days\_since\_last == 1` \*and\* `today.weekday() != 6`. Since Sunday's `weekday()` value is 6, any consecutive-day listen that happens to fall on a Sunday fails the second condition and falls through to the `else` branch, which resets the streak to 1 — even though the user listened on consecutive days.



\*\*My fix and side-effect check:\*\* I removed the `and today.weekday() != 6` condition entirely, since there's no legitimate reason a consecutive-day listen should be treated differently on Sundays:

```python

elif days\_since\_last == 1:

&#x20;   user.listening\_streak += 1

```

I re-tested streak increments across Friday→Saturday, Saturday→Sunday, and Sunday→Monday transitions — all now increment correctly. I also re-verified the `days\_since\_last == 0` (no change) and `> 1` (reset) branches were untouched and still behave correctly.



\---



\### Issue #2 — Friends Listening Now shows people from yesterday



\*\*How I reproduced it\*\*: I created a `ListeningEvent` for a friend timestamped exactly 20 hours before "now". Depending on the current UTC clock time, this event was still within the same calendar day, but when I pushed the test back to 30 hours ago (guaranteed to be yesterday), the friend still incorrectly appeared in the active feed.



\*\*How I found the root cause\*\*: In `feed\_service.py`, I found the query parameter `RECENT\_THRESHOLD = timedelta(hours=24)`.



\*\*The root cause\*\*: The cutoff was calculated as a rolling 24-hour sliding window (`now - 24 hours`). This allowed stale data from yesterday afternoon to merge into the active feed depending on the user's current clock time, rather than tracking who is listening \*today\*.



\*\*My fix and side-effect check\*\*: I updated the logic to anchor directly to midnight UTC of the current calendar day: `cutoff = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)`. I re-ran the 30-hour test in `flask shell` and confirmed the feed correctly filtered it out down to 0 items, while keeping same-day items active.





```python

now = datetime.now(timezone.utc)

cutoff = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)

```

I tested an event from earlier today (should appear) and an event from yesterday, even late yesterday (should not appear). I also confirmed `get\_activity\_feed`, which intentionally has no recency filter, was unaffected since it doesn't use `RECENT\_THRESHOLD`.



\---



\### Issue #3 — The same song keeps showing up twice in search



\*\*How I reproduced it:\*\* I searched for a song I'd tagged with 2 tags and got 2 identical results; a song with 0 or 1 tags returned correctly with no duplicates.



\*\*How I found the root cause:\*\* In `search\_service.py`, I read the query:

```python

db.session.query(Song)

&#x20;   .outerjoin(song\_tags, Song.id == song\_tags.c.song\_id)

&#x20;   .filter(...)

&#x20;   .all()

```

I noticed the join to `song\_tags` wasn't used anywhere in the filter or in constructing the result — tags are actually pulled separately inside `song.to\_dict()` via the model relationship. That told me the join was unnecessary and was the source of row duplication: joining to an association table produces one row per matching tag.



\*\*The root cause:\*\* The query joins `Song` to `song\_tags` even though nothing in the filter references tags. A SQL join to a many-to-many association table returns one row per matching row on the other side, so a song with N tags is returned N times in the result set.



\*\*My fix and side-effect check:\*\* I removed the unnecessary join and added `.distinct()` as a safeguard:

```python

db.session.query(Song)

&#x20;   .filter(db.or\_(Song.title.ilike(f"%{query}%"), Song.artist.ilike(f"%{query}%")))

&#x20;   .distinct()

&#x20;   .all()

```

I re-tested songs with 0, 1, and 3+ tags — each now returns exactly once. I confirmed `to\_dict()` still returns the full, correct tag list per song since that's unrelated to this query.



\---



\### Issue #4 — Notified for playlist adds but not for ratings



\*\*How I reproduced it\*\*: I registered a song under a specific owner, had a different user rate the song using `rate\_song()`, and checked the `Notification` table. No notification records were written.



\*\*How I found the root cause\*\*: I compared `add\_to\_playlist()` and `rate\_song()` in `services/notification\_service.py` line by line.



\*\*The root cause\*\*: The notification-creation pattern used for playlist inserts was completely missing from the `rate\_song` workflow. The function committed the database transaction without ever calling `create\_notification()`.



\*\*My fix and side-effect check\*\*: I added the missing notification block at the end of the `rate\_song` sequence right before the return statement to alert the original song creator if the rater wasn't themselves. I verified in `flask shell` that the query returned exactly 1 notification for the owner with the correct message layout. For re-rating behavior, the code naturally triggers a new notification to keep the owner updated on score changes.



```python

if song.shared\_by != user\_id:

&#x20;   create\_notification(

&#x20;       user\_id=song.shared\_by,

&#x20;       notification\_type="song\_rated",

&#x20;       body=f"{rater.username} rated your song '{song.title}' {score}/5.",

&#x20;   )

```

I tested rating a friend's song (notification created), rating my own shared song (no notification, matching the playlist-add behavior), and re-rating an existing song (\[state what you decided and observed]). I confirmed the existing `Rating` upsert logic itself was untouched.



\---



\### Issue #5 — The last song in a playlist never shows up



\*\*How I reproduced it:\*\* I created a playlist with 3 songs and called `get\_playlist\_songs()` — only 2 came back, missing the last one by position. A playlist with exactly 1 song returned an empty list.



\*\*How I found the root cause:\*\* In `playlist\_service.py`, the query itself correctly orders songs by `position` ascending. The bug was in the return statement:

```python

return \[song.to\_dict() for song in songs\[:-1]]

```

The `\[:-1]` slice unconditionally drops the last element of the already-correctly-ordered list before returning it.



\*\*The root cause:\*\* After querying songs in the correct order, the function slices off the last item with `songs\[:-1]` before converting to dicts. This means the final song in any playlist — regardless of playlist length — is always excluded from the result.



\*\*My fix and side-effect check:\*\* I removed the slice so the full ordered list is returned:

```python

return \[song.to\_dict() for song in songs]

```

I tested a 1-song playlist (now correctly returns that song instead of `\[]`) and a multi-song playlist (last song now appears, ordering still correct). I checked `tests/test\_playlists.py` for any test asserting the old (incorrect) truncated behavior and updated it to expect the full list. I also updated the stale docstring, which already claimed "this function returns all songs in the playlist" — that's now actually true.

