# RM Camera Relay 0.2.0 – Tailscale Serve

Endast camera.ipc_substream och camera.ipc_substream_2. RM-inloggning, aktiv godkänd profil och kamerabehörighet krävs. 60-sekunders ES256-grants och native P-256-klientbevis krävs för varje mediaanrop; online-introspektion före öppning/förnyelse. HA-token och ursprungliga streamresurser stannar lokalt. Kamerareserven och HA:s vanliga listener ändras inte.

## Uppdatera reläet

1. Home Assistant → Settings → Apps → Install app → ⋮ → Repositories: https://github.com/solefors/rm-camera-relay
2. Uppdatera listan, stoppa gamla reläet och uppdatera till **0.2.0**.
3. Gamla portmappningen 8443 → 8099/tcp tas bort. Reläet använder nu host_network och binder **endast 127.0.0.1:8099** med HTTP. Tailscale Serve äger HTTPS-port 8443. Publicera aldrig HTTP på LAN eller internet.
4. Konfiguration:

```yaml
rm_grant_public_jwk: '<din befintliga publika grant-JWK>'
relay_private_jwk_file: /ssl/rm-camera-relay-private.jwk
```

Ta bort gamla tls_cert_file/tls_key_file om de finns kvar i sparad konfiguration. Inga nya nyckelpar krävs för uppdateringen. Reläets privata JWK behövs fortfarande, men inga lokala TLS-certifikat eller TLS-nyckelfiler. /ssl är read-only i reläet. Starta reläet.

## Tailscale-appen ghcr.io/hassio-addons/tailscale:0.30.7

Exakt version installerar CLI i **/opt/tailscale** (inte vanliga PATH), och använder HA-värdens nätverk. share_homeassistant kan bara dela HA, inte ändra proxy till reläet. services gäller svc:-namn och är inte HA-nodens MagicDNS-adress. Lägg till separat nod-Serve på 8443 via CLI.

1. Aktivera MagicDNS och HTTPS Certificates i Tailscale-admin. Befintliga nodnamnet ska vara homeassistant.tailc16667.ts.net. Serve hanterar certifikat och privata TLS-nycklar i Tailscale-appens tillstånd; de behöver inte exporteras till /ssl.
2. Behåll fungerande HA-delning **share_homeassistant: serve** och **share_on_port: 443** om HA redan delas där. Ändra inte HA:s övriga inställningar eller befintliga services. Använd aldrig Funnel för reläet. Starta Tailscale helt före nästa steg; versionen gör en engångsrensning av gamla Serve-inställningar före första start.
3. Kommandona körs inne i Tailscale-containern. Vanliga Terminal-appen har separat filsystem. Använd säker HA OS-värdåtkomst, t.ex. debug-SSH port 22222 eller fysisk HA-konsol med login, och identifiera rätt container:

```sh
docker ps --format '{{.Names}} {{.Image}}' | grep 'ghcr.io/hassio-addons/tailscale:0.30.7'
```

Ersätt TAILSCALE_CONTAINER nedan med exakt namn från svaret. Har du inte värdåtkomst behövs den innan kommandona kan köras; stäng inte av skydd, dela inte SSH-nycklar och skapa inte en ny Tailscale-nod på en dator som ersättning.

```sh
docker exec TAILSCALE_CONTAINER /opt/tailscale version
docker exec TAILSCALE_CONTAINER curl --fail --silent --show-error http://127.0.0.1:8099/healthz
docker exec TAILSCALE_CONTAINER /opt/tailscale serve status
```

Lokalt svar: HTTP 200 och {"ok":true}. Om /opt/tailscale saknas i rätt container, stoppa och kontrollera image/version. Kontrollera att 8443 inte redan används av en annan Serve/Funnel eller host-process; ersätt inte en befintlig tjänst. Lägg till:

```sh
docker exec TAILSCALE_CONTAINER /opt/tailscale serve --bg --https=8443 --set-path=/ http://127.0.0.1:8099
docker exec TAILSCALE_CONTAINER /opt/tailscale serve status
```

Status ska visa 8443 → http://127.0.0.1:8099, tailnet-only, medan HA på 443 finns kvar. Följ eventuell Tailscale-godkännandelänk för HTTPS/Serve. Kör aldrig serve reset, funnel, https+insecure eller en global ersättning av fungerande inställningar.

--bg sparar extra Serve-inställningen i Tailscales tillstånd; 0.30.7:s HA-Serve kör separat på sin konfigurerade port. Kontrollera status och båda adresser efter Tailscale-/HA-omstart och efter framtida uppdateringar. ACL/grants ska tillåta avsedda Android-enheter/användare → HA-noden TCP 8443; behåll befintliga HA-regler. Tailscale-identitet ersätter aldrig RM-behörighet eller klientbevis.

Källor för 0.30.7:
- https://github.com/hassio-addons/addon-tailscale/blob/v0.30.7/tailscale/Dockerfile
- https://github.com/hassio-addons/addon-tailscale/blob/v0.30.7/tailscale/rootfs/etc/s6-overlay/s6-rc.d/share-homeassistant/run
- https://github.com/hassio-addons/addon-tailscale/blob/v0.30.7/tailscale/rootfs/etc/s6-overlay/s6-rc.d/post-tailscaled/run

## Verifiera från telefonen med Tailscale och mobildata

Öppna **https://homeassistant.tailc16667.ts.net:8443/healthz**.

Krav: HTTP 200, {"ok":true}, ingen certifikatvarning och rätt värdnamn. Alternativt från en redan tailnet-ansluten dator:

```sh
curl --fail --show-error --max-time 5 https://homeassistant.tailc16667.ts.net:8443/healthz
```

Använd aldrig -k/--insecure eller Androids trust-all/certifikatundantag. HA på 443 bekräftar inte 8443. Hälsan verifierar process/proxy/TLS, inte grantnyckelmatchning, kamerabehörighet eller faktisk video. Oautentiserad POST /v1/streams/open ska nekas med 401. Kontrollera även fungerande HA 443/8123 och appens kamerareserv.

## Android och hemligheter

Android öppnar **POST https://homeassistant.tailc16667.ts.net:8443/v1/streams/open**, body exakt {}. Ska först få ett 60-sekunders grant från **POST https://www.msdev.bid/api/mobile/camera-grant** med RM-session, entityId och native clientPublicJwk. Varje relay-anrop kräver Authorization: Bearer grant och X-RM-Proof: ES256 JWT med jwk, htm, htu, bh, ath, iat, nonce. htu är exakt EXTERN HTTPS-origin på 8443 + faktisk sökväg, aldrig http://127.0.0.1:8099. Grant/proof hålls i native, aldrig JavaScript, URL, cache eller Logcat. Förnya vid ca 30 sekunder med nytt grant och POST /v1/streams/{id}/renew; varje manifest/segment/nyckel har egen proof. Reläet återger endast opaque resursreferenser.

- Lovable Secrets: befintliga RM_CAMERA_GRANT_PRIVATE_JWK och RM_CAMERA_RELAY_PUBLIC_JWK behålls. Bara grantens publika JWK anges i HA-fältet rm_grant_public_jwk.
- Endast HA: /ssl/rm-camera-relay-private.jwk och automatisk SUPERVISOR_TOKEN. Tailscale äger TLS-nycklarna; inga TLS-filer behöver skapas i /ssl.
- Vercel: inga nya privata nycklar/certifikat; befintlig vidarebefordring till Lovable används.

Skriv aldrig ut privata nycklar. Återanvänd matchande befintliga JWK-par; generate_keys.py skriver privata filer och ska bara användas om ett par verkligen saknas. Ingen separat HA-inloggning behövs.

## Verifieringsgräns

Implementerad loopback och autentiserat syntetiskt HLS-flöde är testade lokalt; fysisk HA-installation, Serve/TLS på din tailnet, Supervisor-HLS och Android-video är inte verifierade. Befintlig proxyreserv och avstängd releasegrind lämnas oförändrade tills verkliga kontroller är godkända.
