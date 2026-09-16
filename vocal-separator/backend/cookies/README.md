# cookies/

Pune aici `youtube_cookies.txt` (format Netscape) exportat dintr-un cont
YouTube logat, pentru a trece de verificarea anti-bot a YouTube
("Sign in to confirm you're not a bot") care apare des la request-uri
venite din Docker/server.

Cum exporți cookies-urile:
1. Instalează o extensie de browser gen "Get cookies.txt LOCALLY".
2. Loghează-te pe youtube.com în același browser.
3. Exportă cookies-urile pentru domeniul youtube.com ca `youtube_cookies.txt`.
4. Pune fișierul aici: `backend/cookies/youtube_cookies.txt`.

Fișierul e ignorat de git (conține token-uri de sesiune) — vezi `.gitignore`.
Dacă nu există fișierul, aplicația funcționează la fel ca înainte (fără
cookies), doar că poate primi eroarea de mai sus.

Calea poate fi schimbată din `.env` cu `YOUTUBE_COOKIES_FILE=/alta/cale.txt`.
