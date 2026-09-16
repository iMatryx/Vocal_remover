# browser_profile/

Placeholder gol pentru mount-ul profilului de browser folosit ca sursă
"live" de cookies YouTube (fără export manual). Are prioritate față de
`backend/cookies/youtube_cookies.txt` când e configurat.

Fiecare utilizator/mașină are alt browser și altă cale de profil, deci
setarea e per-deployment, nu hardcodată în cod:

1. În `vocal-separator/.env` (nivel compose, lângă `docker-compose.yml` —
   NU `backend/.env`), setează:
   ```
   YOUTUBE_BROWSER_PROFILE_HOST_PATH=/calea/ta/spre/profilul/de/Firefox
   ```
   Vezi `vocal-separator/.env.example` pentru căi tipice pe macOS/Linux/Windows.
   Găsești folderul exact de profil vizitând `about:profiles` în Firefox.

2. În `backend/.env`, setează:
   ```
   YOUTUBE_COOKIES_BROWSER=firefox
   ```

3. `docker compose up -d backend celery` (nu necesită rebuild de imagine).

**Doar Firefox e fiabil aici.** Chrome/Edge/Brave/Vivaldi/Opera criptează
cookie-urile cu o cheie ținută în keychain-ul sistemului de operare al
gazdei (macOS Keychain, Windows DPAPI) — un container Linux nu are cum să
ajungă la ea, deci decriptarea eșuează. Pe o gazdă Linux, cookie-urile
Chromium pot fi totuși accesibile prin `YOUTUBE_COOKIES_KEYRING`
(BASICTEXT/GNOMEKEYRING/KWALLET) — vezi `backend/.env.example`.

Dacă `YOUTUBE_COOKIES_BROWSER` nu e setat sau acest folder rămâne gol,
aplicația trece automat pe fallback-ul din `backend/cookies/`.
