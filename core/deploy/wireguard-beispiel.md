# WireGuard-Beispiel für das Beachhub-Hauptsystem

Das Admin-UI ist ausschließlich über WireGuard erreichbar (siehe `Caddyfile`, das nur auf
`10.8.0.1:8443` lauscht). Dieses Dokument zeigt eine funktionierende Beispielkonfiguration für
die VM (WireGuard-Server) und die Peers. IP-Adressen, Schlüssel und Endpunkt sind Platzhalter und
müssen für den echten Betrieb ersetzt werden.

## Netz

- VM (Server): `10.8.0.1/24`, UDP-Port `51820`
- Peer „Betreiber-Laptop“: `10.8.0.10/32`
- Peer „Zweiter Entwickler“: `10.8.0.11/32`
- Peer „Halle“ (Platzhalter, erst Stufe 3): `10.8.0.20/32`

In der Hetzner-Cloud-Firewall darf für die VM **nur** `51820/udp` von außen offen sein. SSH nur
über eine separate, eingeschränkte Regel (z. B. eigene IP) oder ebenfalls nur über WireGuard.
Der Admin-UI-Port `8443` wird **nicht** in der Cloud-Firewall freigegeben – er ist nur im
WireGuard-Netz erreichbar.

## Schlüssel erzeugen

Für den Server und jeden Peer ein eigenes Schlüsselpaar erzeugen:

```bash
wg genkey | tee privatekey | wg pubkey > publickey
```

Private Schlüssel bleiben jeweils auf dem Gerät, das sie erzeugt hat; nur die öffentlichen
Schlüssel werden ausgetauscht.

## Server-Konfiguration (`/etc/wireguard/wg0.conf` auf der VM)

```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <privater-schluessel-server>

# Peer: Betreiber-Laptop
[Peer]
PublicKey = <oeffentlicher-schluessel-betreiber-laptop>
AllowedIPs = 10.8.0.10/32

# Peer: Zweiter Entwickler
[Peer]
PublicKey = <oeffentlicher-schluessel-zweiter-entwickler>
AllowedIPs = 10.8.0.11/32

# Peer: Halle (Platzhalter, erst Stufe 3 – Hallen-API/Anwesenheit)
# [Peer]
# PublicKey = <oeffentlicher-schluessel-halle>
# AllowedIPs = 10.8.0.20/32
```

Aktivieren und beim Booten automatisch starten:

```bash
systemctl enable --now wg-quick@wg0
```

## Client-Konfiguration (Beispiel „Betreiber-Laptop“)

```ini
[Interface]
Address = 10.8.0.10/32
PrivateKey = <privater-schluessel-betreiber-laptop>
DNS = 1.1.1.1

[Peer]
PublicKey = <oeffentlicher-schluessel-server>
Endpoint = <oeffentliche-ip-der-vm>:51820
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
```

Analog für „Zweiter Entwickler“ mit `Address = 10.8.0.11/32`.

Nach dem Verbindungsaufbau ist das Admin-UI unter `https://10.8.0.1:8443/admin` erreichbar
(siehe `docs/betrieb/hauptsystem.md`, Abschnitt „Zugriff“).

## Peer „Halle“ (Stufe 3)

Der Peer für die Halle ist hier nur als Platzhalter (`10.8.0.20`) vorgesehen. Er wird erst in
Stufe 3 aktiviert, wenn der Hallendienst (`hall/`) über eine API mit dem Hauptsystem spricht
(Präsenz/Anwesenheit, Betreiber-Alarme). Bis dahin bleibt der Peer-Eintrag im Server auskommentiert.
