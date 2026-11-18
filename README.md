# RM Camera Relay

Separat Home Assistant OS-tillägg. Bara `camera.ipc_substream` och `camera.ipc_substream_2`. Inga appfiler eller hemligheter finns här. Installation, TLS och riktig Android-uppspelning är ännu inte verifierade.

## Installera

1. Home Assistant: Settings → Apps → Install app → ⋮ → Repositories.
2. Lägg till `https://github.com/solefors/rm-camera-relay` och uppdatera listan.
3. Installera RM Camera Relay, men starta inte före nyckel-/TLS-konfiguration.
4. Generera två P-256-nyckelpar på en betrodd dator i `rm_camera_relay`: `python3 -m venv /tmp/rm-keys-env`, `/tmp/rm-keys-env/bin/pip install -r requirements.txt`, `/tmp/rm-keys-env/bin/python generate_keys.py /tmp/rm-keys`. Verktyget skriver bara privata filer, inga nyckelvärden till stdout.
5. Lägg `rm-camera-grant-private.jwk` som RM-serverhemlighet `RM_CAMERA_GRANT_PRIVATE_JWK` och `rm-camera-relay-public.jwk` som serverkonfiguration `RM_CAMERA_RELAY_PUBLIC_JWK` i Lovable Secrets. Publicera senaste RM-kod och bygg senaste main på Vercel. RM endpoints och korrekt nyckelpar krävs; tillägget fungerar inte fristående från RM-behörighetskontrollen.
6. Kopiera endast `rm-camera-relay-private.jwk` till HA `/ssl/rm-camera-relay-private.jwk`. Lägg aldrig privata nycklar i repo, chatt eller loggar.
7. Utfärda ett giltigt Tailscale-certifikat på befintliga HA-Tailscale-noden via `tailscale cert homeassistant.tailc16667.ts.net`. Kopiera certifikat/full kedja till `/ssl/homeassistant.tailc16667.ts.net.crt` och TLS-privatnyckel till `/ssl/homeassistant.tailc16667.ts.net.key`. Certifikatförnyelse och omstart av tillägget måste ordnas lokalt; ingen automatisk förnyelse ingår. Skydda filerna mot andra tillägg/backuper.
8. Ange `rm_grant_public_jwk` med innehållet från `rm-camera-grant-public.jwk`. Behåll `relay_private_jwk_file`, `tls_cert_file`, `tls_key_file` på ovanstående `/ssl`-vägar. Privata värden anges inte i YAML. SUPERVISOR_TOKEN tilldelas automatiskt av HA OS; ange inte HA_TOKEN eller TAILSCALE_AUTH_KEY.
9. Behåll containerport 8099 → HA-värdport 8443; starta tillägget och granska loggar lokalt utan att dela hemligheter.
10. Begränsa Tailscale ACL/grants till rätt Android-enheter → HA TCP 8443. Testa `https://homeassistant.tailc16667.ts.net:8443/healthz` från telefon med Tailscale/mobildata utan att kringgå TLS; förväntat `{"ok":true}`. Hälsosvar bevisar inte kamera-/RM-behörighet.

## TLS/Tailscale

Tillägget terminerar TLS 1.2+ på containerport 8099, mappad till HA-värdport 8443. Certifikatet måste vara giltigt för homeassistant.tailc16667.ts.net. Befintligt Tailscale måste leverera TCP till denna värdport; fungerande HA/Tailscale Serve på 443 bevisar inte 8443. Om nuvarande Tailscale-tillägg inte exponerar värdportar behövs separat verifierad TCP-förmedling. Ingen HTTP-nedgradering, trust-all, routerport eller Funnel. HA 443/8123 och befintlig skyddad molnreserv ändras inte.

Android öppnar `POST https://homeassistant.tailc16667.ts.net:8443/v1/streams/open`, body exakt `{}`, med 60-sekunders RM-grant i Authorization och native P-256-signerat X-RM-Proof på varje open/renew/media/close. Grant fås med RM-inloggning från `POST https://www.msdev.bid/api/mobile/camera-grant`. Grant och bevis ska aldrig lämnas till JavaScript; native Android-transport och fysisk uppspelning återstår. Fullständigt native-kontrakt finns i app-projektets HA-CAMERA-RELAY.md. Tillåtna resurser är endast lokala Supervisor-HLS-resurser omskrivna till opaque referenser; inga originalresurser eller HA-credentials lämnar tillägget.

Detta repo publicerar körbar kod och byggrecept, inte en redan testad HA OS-installation. Verklig Supervisor-HLS-kompatibilitet, certifikatförnyelse, Tailscale-nåbarhet och Android-video måste verifieras före aktivering.
