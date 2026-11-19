# RM Camera Relay 0.2.1 – bakom Tailscale Serve

Endast `camera.ipc_substream` och `camera.ipc_substream_2`. RM-inloggning, aktiv godkänd profil och kamerabehörighet krävs. Varje mediaanrop kräver ett 60-sekunders ES256-grant och ett P-256-klientbevis från Android; öppning och förnyelse kontrolleras online mot RM. HA-token och kamerans ursprungliga resurser stannar i tillägget. Den vanliga HA-sidan och appens befintliga kameraförmedlare är oförändrade reserver.

## Nätverk: vilken port och hur Tailscale når den

```text
Android (Tailscale) ──HTTPS──> homeassistant.tailc16667.ts.net:8443
                               Tailscale-appen (Serve, TLS-certifikat)
                               ──HTTP──> 127.0.0.1:8099 på HA-värden
                                         RM Camera Relay
```

- Reläet lyssnar på **127.0.0.1:8099/TCP, vanlig HTTP, endast HA-värdens loopback**.
- Både reläet (`host_network: true`) och Tailscale-appen 0.30.7 (`host_network: true`) kör i HA-värdens nätverk. De delar därför samma `127.0.0.1`, trots att de är separata containrar. Ingen Docker-port behöver publiceras.
- `ha apps info` visar därför **`network: null`**. Det är korrekt. Lägg inte till en portmappning: den skulle exponera okrypterad HTTP på LAN och fungerar inte ihop med värdnätverk.
- Port **8443** ägs av Tailscale Serve, inte av reläet.

## 1. Uppdatera till 0.2.1

```sh
ha store reload
ha apps info 69daba89_rm_camera_relay      # version_latest: 0.2.1
ha apps stop 69daba89_rm_camera_relay
ha apps update 69daba89_rm_camera_relay
```

Om `version_latest` fortfarande är äldre: `ha store repair 69daba89` och sedan `ha store reload`. Avinstallera inte.

## 2. Konfiguration

Tilläggets schema har bara två fält:

```yaml
rm_grant_public_jwk: '{"kty":"EC","crv":"P-256","x":"...","y":"...","alg":"ES256","use":"sig"}'
relay_private_jwk_file: /ssl/rm-camera-relay-private.jwk
```

- `rm_grant_public_jwk`: den **publika** grant-nyckeln som RM visade (en rad JSON, utan `d`). `alg`/`use` får finnas med från och med 0.2.1. Fältet får inte vara tomt.
- `relay_private_jwk_file`: reläets privata nyckel, som du skapade på HA. Den ska ligga direkt i `/ssl`, vara läsbar och aldrig lämna HA.
- Inga TLS-certifikat eller TLS-nyckelfiler behövs.

Starta och läs loggen:

```sh
ha apps start 69daba89_rm_camera_relay
ha apps logs 69daba89_rm_camera_relay
```

Lyckad start: `RM Camera Relay listening on http://127.0.0.1:8099 (host loopback only; expose with Tailscale Serve on 8443).`

Vid fel visas en exakt orsak utan nyckelvärden:

| Logg | Åtgärd |
|---|---|
| `rm_grant_public_jwk is empty` | Klistra in den publika grant-nyckeln. |
| `rm_grant_public_jwk is not a public P-256 JWK` | Fel format, eller en privat nyckel med `d` inklistrad. Använd bara den publika raden. |
| `relay_private_jwk_file is missing or unreadable in /ssl` | Kontrollera filnamnet i `/ssl`. |
| `relay_private_jwk_file is not a valid private P-256 JWK` | Filen är skadad, eller är den publika halvan. |
| `SUPERVISOR_TOKEN is missing` | `homeassistant_api` måste vara på (standard i tillägget). |
| `cannot listen on 127.0.0.1:8099` | En annan process på HA-värden använder 8099. |

## 3. Tailscale-appen 0.30.7: Serve 8443 → 127.0.0.1:8099

CLI:t finns i `/opt/tailscale` inne i Tailscale-containern. Kör kommandona från HA OS-värdens skal (debug-SSH på port 22222 eller den fysiska konsolen). Terminal-appen har inte Docker-åtkomst.

```sh
docker ps --format '{{.Names}} {{.Image}}' | grep hassio-addons/tailscale
# vanligen: addon_a0d7b954_tailscale
TS=addon_a0d7b954_tailscale

# 1. Reläet nås från Tailscale-containern via delad loopback:
docker exec $TS curl --fail --silent --show-error http://127.0.0.1:8099/healthz
#    förväntat: {"ok":true}

# 2. Lägg till Serve på 8443 (tailnet-only, inte Funnel):
docker exec $TS /opt/tailscale serve --bg --https=8443 http://127.0.0.1:8099

# 3. Kontrollera:
docker exec $TS /opt/tailscale serve status
#    https://homeassistant.tailc16667.ts.net:8443 (tailnet only)
#    |-- / proxy http://127.0.0.1:8099
#    HA på 443 ska finnas kvar oförändrad.
```

- Behåll `share_homeassistant: serve` och `share_on_port: 443` i Tailscale-appen. Sätt inte `share_on_port` till 8443, för då delas HA där i stället.
- Kör aldrig `serve reset`, `funnel`, `https+insecure` eller `-k`.
- I Tailscale-admin ska MagicDNS och HTTPS Certificates vara på. ACL/grants ska tillåta Android-enheterna att nå HA-noden på TCP 8443.
- `--bg` sparar inställningen i Tailscales tillstånd. Kontrollera `serve status` efter omstart av Tailscale eller HA.

Om steg 1 misslyckas körs reläet inte. Läs reläets logg enligt steg 2.

## 4. Verifiera /healthz

Från en telefon eller dator som är ansluten till Tailscale:

```sh
curl --fail --show-error --max-time 5 https://homeassistant.tailc16667.ts.net:8443/healthz
```

Krav: HTTP 200, `{"ok":true}` och ingen certifikatvarning. Följande ska nekas med 401:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST -d '{}' https://homeassistant.tailc16667.ts.net:8443/v1/streams/open
```

`/healthz` visar bara att process, Serve och TLS fungerar. Det bevisar inte nyckelmatchning, kamerabehörighet eller video.

## Android

1. `POST https://www.msdev.bid/api/mobile/camera-grant` med RM-session, `entityId` och native `clientPublicJwk`. Svaret innehåller ett 60-sekunders grant.
2. `POST https://homeassistant.tailc16667.ts.net:8443/v1/streams/open`, body exakt `{}`, `Authorization: Bearer <grant>` och `X-RM-Proof`. `htu` är alltid den externa HTTPS-adressen på 8443, aldrig `http://127.0.0.1:8099`.
3. Förnya med nytt grant via `POST /v1/streams/{id}/renew`. Avsluta med `DELETE /v1/streams/{id}`.

Om något steg misslyckas används appens befintliga kameraförmedlare.

## Var nycklarna finns

- **Lovable Secrets:** `RM_CAMERA_GRANT_PRIVATE_JWK` och `RM_CAMERA_RELAY_PUBLIC_JWK`.
- **Endast HA:** `/ssl/rm-camera-relay-private.jwk` och den automatiska `SUPERVISOR_TOKEN`. TLS-nycklarna ägs av Tailscale.
- **Vercel:** inga nya hemligheter. Vercel vidarebefordrar till Lovable.

Skriv aldrig ut privata nycklar och klistra inte in dem i någon chatt.

## APK och appuppdateringar – separat tillägg

Den nya uppladdningsappen är oberoende av kamerareläet. Se [installation och säker uppladdning](rm_apk_updates/README.md). Den använder intern containerport 8100 och publik HTTPS via ha.msdev.bid/v1/apk/ och Cloudflare Tunnel, utan att ändra kamerareserv eller Serve 8443. Koden finns i projektet; publicering/installation på HA återstår.
