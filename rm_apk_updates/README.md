# RM APK och appuppdateringar 0.4.0 – automatisk versionsläsning

Webbläsaren (dator eller mobil) laddar upp till RM-appen. RM-servern kontrollerar att du är aktiv, godkänd administratör och skickar filen vidare i delar om 2 MiB till detta tillägg genom **Home Assistants egen ingress** på den befintliga adressen `https://ha.msdev.bid`. Ingen Tailscale, ingen Android-app och **ingen ändring av Cloudflared/Cloudflare** behövs. Kameraflödet ändras inte.

## Varför `/v1/apk/space` gav 404

Cloudflared-tillägget (lokalt hanterad tunnel) bygger sin tunnelkonfiguration från tilläggsinställningarna: först en regel `ha.msdev.bid → Home Assistant`, sedan `additional_hosts`. Schemat tillåter bara `hostname`, `service` och `disableChunkedEncoding` – **ingen `path`** – och HA-regeln ligger först. Alla anrop till `ha.msdev.bid/v1/apk/...` hamnar därför i Home Assistant, som inte har den sökvägen → 404. Det går inte att lägga en sökvägsregel för samma värdnamn i det tillägget utan att byta tunnelhantering.

Lösningen: tillägget har nu `ingress: true` (port 8100). Home Assistant når det själv via `/api/hassio_ingress/<token>/`, som redan går genom din nuvarande HA-rutt. **Ingen rad i Cloudflared-konfigurationen ska ändras.**

## Installera / uppdatera

1. Inställningar → Appar (Tillägg) → Tilläggsbutik → ⋮ → **Sök efter uppdateringar** (eller `ha store reload`).
2. Öppna **RM APK och appuppdateringar** → **Uppdatera** till **0.4.0**. Byggsteget kan ta några minuter.
3. Kontrollera Konfiguration: `package_name` = APK:ns riktiga paketnamn (används bara för APK-publiceringen).
4. Starta. Loggen ska visa `RM APK listening on internal port 8100 for Home Assistant ingress only.`
5. Info-fliken ska visa **Öppna webbgränssnitt** (ingress aktiv). Sidopanel behövs inte.

Krav: RM-serverns Home Assistant-token (`HA_TOKEN`) tillhör en HA-administratör – ingress-sessioner kräver det.

## Säkerhet

- Tillägget tar bara emot anrop från Supervisors ingress-proxy (172.30.32.2), ingen värdport publiceras.
- Varje anrop bär ett 60-sekunders krypterat RM-tillstånd som tillägget kontrollerar online mot RM (aktiv, godkänd administratör).
- HA-token och ingress-session finns bara på RM-servern, aldrig i webbläsaren.
- Filnamn: bokstäver a–z, siffror, `.`, `-`, `_`, max 128 tecken. Inga sökvägar, dolda filer eller `app-update.json` (skrivs av APK-publiceringen).
- Delar sparas privat i `/config/.rm-apk-uploads`, utanför `www`. Efter full uppladdning kontrolleras storlek och SHA-256, och filen flyttas atomiskt till `/config/www/apk/<namn>`; en befintlig fil med samma namn ersätts i ett steg (läsare ser gammal eller ny fil, aldrig en halv).

## Använda

RM → Dashboard → Filhanterare → **APK och appuppdateringar** → **Ladda upp fil till Home Assistant**. Procent, MB, kB/s och återstående tid visas. Pausa/Återuppta fortsätter från senaste bekräftade byte. Filen nås sedan på `https://ha.msdev.bid/local/apk/<namn>`.

I rutan **APK och appuppdateringar** väljer du bara APK-filen och eventuellt versionsanteckningar. Servern läser `packageName`, `versionCode` och `versionName` ur APK:ns verkliga Android-manifest och verifierar signaturen. Endast `com.example.rmdckentreprenadab` tillåts. Även APK-filer valda i den generella filrutan går igenom samma kontroll.

Efter fullständig uppladdning och godkänd kontroll ersätts `/config/www/apk/rmlarm.apk` atomiskt. Därefter ersätts `app-update.json` atomiskt med verklig versionskod, versionsnamn och `apkUrl: https://ha.msdev.bid/local/apk/rmlarm.apk`. Befintliga andra JSON-fält och versionsanteckningar bevaras om inga nya anteckningar anges. Paket-, signatur-, metadata- eller hashfel lämnar båda publicerade filerna orörda. Skrivfel vid publicering återställer föregående filer. Varje filbyte är atomiskt; två separata filer kan inte bytas i en gemensam atomisk operation vid strömavbrott.

Adminpanelen visar det verifierade paketnamnet, versionskoden, versionsnamnet och bekräftar att APK och JSON har publicerats. JSON laddas aldrig upp separat.

## Test från dator utan Tailscale

1. Skapa `upload-test.txt` med innehållet `test-1`, ladda upp. Öppna `https://ha.msdev.bid/local/apk/upload-test.txt?x=1` → `test-1`.
2. Ändra innehållet till `test-2`, ladda upp samma namn → sidan visar `test-2` (ersatt).
3. Ladda upp en APK-stor fil (t.ex. 40–80 MB) och kontrollera att hastighet/återstående tid visas och att filen kan laddas ner.
4. `https://ha.msdev.bid/` öppnar fortfarande Home Assistant.

API (internt, via ingress): GET `/v1/files/space`; POST `/v1/files/uploads` `{name,size,sha256}`; GET/PATCH `/v1/files/uploads/{id}` (`Upload-Offset`); POST `/v1/files/uploads/{id}/finish`. APK-publicering: samma under `/v1/apk/`, POST `{size,sha256,message?}`. Äldre klienters explicit angivna versionsfält verifieras fortfarande mot APK:n. Finish/status returnerar `publication: {packageName,versionCode,versionName,apkUrl,manifestUrl}` efter lyckad publicering.
