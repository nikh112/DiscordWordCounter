# Discord Word Leaderboard Bot

An asynchronous Discord bot built with Python that tracks, caches, and ranks word frequency across a server using persistent SQLite database caching!

* **Asynchronous:** Utilizes `asyncio` and `aiohttp` for non-blocking message fetching
* **Caching:** Implements an embedded SQLite database to store word frequencies
* **Text Processing:** Uses RegEx to sanitize inputs and handle complex matching (strict vs. substring).
