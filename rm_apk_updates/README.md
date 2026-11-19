# RM APK och appuppdateringar 0.2.0 – publik HTTPS

Uppladdningen använder **https://ha.msdev.bid/v1/apk/** via Cloudflare Tunnel; klienten behöver inte Tailscale. Videor, kamerareserv, HA-webbsidan och Androids befintliga uppdateringsflöde ändras inte.

## Installera separat HA-app

1. Använd tilläggsrepot https://github.com/solefors/rm-camera-relay (endast tilläggskod, inte privata RM-apprepot). Kör `ha store reload` och installera **RM APK och appuppdateringar**, version **0.2.0**. Om APK-appen ännu inte syns måste dess mapp först publiceras i repot; koden finns också under projektets `ha-addons/rm_apk_updates` och kan installeras lokalt via `/addons/rm_apk_updates`.
2. Ange APK:ns verkliga `package_name`. Standard `com.example.rmdckentreprenadab` kommer från befintliga tester; kontrollera mot riktig APK.
3. Starta. Tillägget lyssnar på **0.0.0.0:8100 inne i sin container**, utan host_network eller publicerad värdport. Cloudflared måste nå samma HA-container-nätverk. Ingen Tailscale-port används.
4. HA-konfigurationen monteras i tillägget som `/homeassistant`. **HA Core /config/www/apk motsvarar /local/apk/**. `/config/www/local/apk` skulle ge `/local/local/apk/`, därför används `/config/www/apk` för att behålla den befintliga adressen. Kontometadata/temporärfiler finns bara i `/homeassistant/.rm-apk-uploads`, utanför www.

## Cloudflare Tunnel – behåll nuvarande HA-regel

Lägg en mer specifik regel **före** nuvarande hela-värden-regeln för ha.msdev.bid. Ändra inte nuvarande HA-service (ofta homeassistant:8123). Tunneln får inte routa hela HA-värden till APK-appen.

För lokalt hanterad cloudflared används följande ingressregler i den befintliga tunnelfilen. Container-DNS för den här appen från repot är `69daba89-rm-apk-updates`; kontrollera faktisk app/container innan användning:

```yaml
ingress:
  - hostname: ha.msdev.bid
    path: ^/v1/apk(/.*)?$
    service: http://69daba89-rm-apk-updates:8100
  # Behåll befintliga HA-, övriga värd- och sista catch-all-regler här.
```

För fjärrhanterad tunnel: Cloudflare Zero Trust → Networks → Tunnels → din tunnel → Public hostnames/Published application routes: lägg **samma hostname ha.msdev.bid**, Path `^/v1/apk(/.*)?$`, HTTP service `69daba89-rm-apk-updates:8100`. Behåll befintlig ha.msdev.bid-regel utan path. Public HTTPS är port 443, TLS termineras hos Cloudflare; HTTP 8100 stannar på HA:s interna containernät. Exponera aldrig 8100 i routern/LAN och aktivera inte noTLSVerify.

### För ditt Cloudflared-tillägg på Home Assistant

Den granskade standardappen (homeassistant-apps/app-cloudflared, tidigare brenner-tobias) har **inte path i additional_hosts-schema**, och `run_parameters` tillåter inte egen --config. Lägg alltså inte till en påhittad `path` där. För samma ha.msdev.bid behöver tillägget använda en **fjärrhanterad tunnel**:

1. Säkerhetskopiera nuvarande Cloudflared-inställningar och dokumentera alla befintliga domäner och HA-target. Behåll nuvarande tunnel tills de nya routningarna är klara.
2. I Cloudflare Zero Trust, skapa en fjärrhanterad Cloudflared-tunnel (eller migrera den befintliga via Cloudflares stöd om det erbjuds). Lägg HA-routen för `ha.msdev.bid` med samma HA-service som idag och alla andra befintliga värdar.
3. Lägg dessutom APK-routen ovan med path och intern service **före** HA:s standardroute. DNS för ha.msdev.bid ska peka på den tunnel som innehåller båda reglerna; skapa inte två konkurrerande tunnel-CNAME.
4. I HA Cloudflared-tilläggets konfiguration ange dess `tunnel_token` via HA:s lokala inställningsfält, spara och starta om. Token kommer från Cloudflares Install connector-steg och ska aldrig delas med Lovable/chatten. Vid tokenläge ignoreras `external_hostname`/`additional_hosts`; alla gamla regler måste därför finnas i Cloudflare före bytet.
5. Kontrollera att HA-webbsidan och /local/apk/app-update.json fungerar som tidigare, och att /v1/apk/space ger 401 JSON. Om HA slutar fungera återställ din sparade tunnelkonfiguration och DNS innan felsökning.

Det här kräver administratörsåtkomst till din HA och Cloudflare, som inte finns i den här körningen. Ingen tunnel/DNS-inställning är ändrad här.

Verifiera från cloudflared-containern/HA-värdterminalen:
```sh
curl --fail http://69daba89-rm-apk-updates:8100/healthz
# För lokalt hanterad tunnel (byt fil till din befintliga konfigurationsfil):
cloudflared tunnel ingress validate --config /path/to/config.yml
cloudflared tunnel ingress rule --config /path/to/config.yml https://ha.msdev.bid/v1/apk/space
```

Från dator UTAN Tailscale, testa:
```sh
curl -i https://ha.msdev.bid/v1/apk/space
```
Förväntat **HTTP 401** och JSON om kortlivad behörighet; 404/HA-inloggningssida betyder fel tunnelregel, 502 betyder intern service ej nåbar. Ingen certifikatvarning och inget `-k`. `/healthz` är en intern kontroll och behöver inte exponeras publikt. Cloudflare får inte cachelagra /v1/apk/* eller app-update.json; inga challenge-sidor får ersätta API-svar, PATCH och OPTIONS måste tillåtas. Behåll övriga tunnel-/Access-skydd; riktad API-policy måste acceptera RM-behörighetskontrollen.

## Behörighet och hemligheter

Lovable Secrets innehåller `RM_APK_UPLOAD_PERMISSION_KEY`; den har genererats säkert och finns endast på RM-servern. Inga nya nycklar eller HA-token behövs på HA eller i Vercel. Vercel vidarebefordrar RM-grant/introspection till Lovable.

RM-sessionen skickas enbart till RM `/api/public/apk-admin`, som verifierar aktiv profil och adminroll och utfärdar ett AES-GCM-krypterat, 60-sekunders tillstånd. Webbläsaren skickar endast det opaka tillståndet i Authorization till HA, aldrig RM-sessionen, nycklar eller URL-parametrar. HA kontrollerar tillståndet online mot RM på varje operation; RM kontrollerar sessionen och rollen igen. Utgångna/återkallade tillstånd nekas. Ingen tillståndslagring i webbläsaren/manifestet eller accessloggning.

## Uppladdning och publicering

Filhanterare → **APK och appuppdateringar**, bredvid **Videor**. Välj `rmlarm.apk`, versionCode/versionName och anteckningar. Diskkontroll, procent, byte, kB/s, ETA och status visas. Delarna är 2 MiB, inte en låg totalgräns. Servern reserverar ledigt diskutrymme. Pausa/återuppta; efter omladdning välj samma fil/metadata så hittas bytepositionen genom konto och SHA-256.

API: GET `/v1/apk/space`; POST `/v1/apk/uploads`; GET/PATCH `/v1/apk/uploads/{id}`; POST `/v1/apk/uploads/{id}/finish`.

Storlek, hash, verkligt Android-paket/version och giltig APK-signatur verifieras före publicering. `rmlarm.apk` ersätts atomiskt och manifestet skrivs sist med **samma format**: versionCode, versionName, apkUrl, message. Versionsunik `rmlarm-<sha256>.apk` under /local/apk/ håller gamla manifest användbara även vid avbrott mellan två filbyten. Android kontrollerar signerarkontinuitet. Gamla APK-filer och pausade uppladdningar raderas inte automatiskt.

## Återstående verklig verifiering

HA-installation/containerbyggning, tunnelns path-routing, stor riktig APK från dator och mobil utan Tailscale, avbrott/återupptagning samt Androidinstallation kräver åtkomst till HA/tunneln. Lokala tester är inte bevis på dessa steg. Dela aldrig nycklar eller sessionstoken i chatten.
