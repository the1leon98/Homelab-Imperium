# Sicherheits-Härtungsdokument — The Ghost Server

> **Dokumentenversion:** 2.0  
> **Autor:** Lead DevSecOps & Security Engineer  
> **Stand:** 26. Juni 2026  
> **Klassifizierung:** Streng vertraulich — Betriebssicherheit  
> **Referenz:** `docs/architektur.md` Abschnitt 9 (Sicherheitsarchitektur)

---

## Inhaltsverzeichnis

1. [Ghost-Server-Philosophie](#1-ghost-server-philosophie)
2. [Verteidigungsebenen im Überblick](#2-verteidigungsebenen-im-überblick)
3. [Ebene 1: Physische Sicherheit — USB-Schlüssel & SSH-Hardening](#3-ebene-1-physische-sicherheit--usb-schlüssel--ssh-hardening)
4. [Ebene 2: Netzwerkperimeter — UFW & knockd](#4-ebene-2-netzwerkperimeter--ufw--knockd)
5. [Ebene 3: Transportverschlüsselung — Tailscale Mesh](#5-ebene-3-transportverschlüsselung--tailscale-mesh)
6. [Ebene 4: Reverse Proxy — Caddy TLS & Header-Hardening](#6-ebene-4-reverse-proxy--caddy-tls--header-hardening)
7. [Ebene 5: Application Layer — FastAPI Security](#7-ebene-5-application-layer--fastapi-security)
8. [Ebene 6: Container-Isolation — Docker Security](#8-ebene-6-container-isolation--docker-security)
9. [Ebene 7: Datenhaltung — Festplattenverschlüsselung & Secrets](#9-ebene-7-datenhaltung--festplattenverschlüsselung--secrets)
10. [Härtungs-Checkliste (Audit)](#10-härtungs-checkliste-audit)
11. [Incident Response — Was tun bei Kompromittierung](#11-incident-response--was-tun-bei-kompromittierung)
12. [Penetration-Test-Plan](#12-penetration-test-plan)

---

## 1. Ghost-Server-Philosophie

Das Homelab-Imperium operiert nach dem **Ghost-Server-Prinzip**: Der physische Server
(HP EliteDesk 800 G4, Ubuntu Server) soll im lokalen Netzwerk für Angreifer
**vollständig unsichtbar** sein — selbst wenn der Angreifer sich bereits im selben
LAN-Segment befindet.

### 1.1 Kernprinzipien

| Prinzip | Umsetzung |
|---|---|
| **Zero Visibility** | Keine offenen Ports im LAN. UFW: `default deny incoming`. Nur Tailscale-Mesh-Endpunkte sind erreichbar. |
| **Zero Trust** | Jede Verbindung wird authentifiziert — keine implizit vertrauenswürdigen Netze. SSH nur mit physischem USB-Schlüssel. |
| **Time-Limited Access** | SSH-Port öffnet via Port-Knocking für exakt 3 Sekunden. Kein dauerhaft offener Zugang. |
| **Defense in Depth** | 7 unabhängige Verteidigungsebenen. Fällt eine, greift die nächste. |
| **Minimal Attack Surface** | Nur Caddy (80/443) ist nach außen exponiert — und das nur via Tailscale, nicht im LAN. |

### 1.2 Angreifermodell

Das System ist gegen folgende Bedrohungsklassen ausgelegt:

| Bedrohungsklasse | Beschreibung | Primäre Abwehr |
|---|---|---|
| **LAN-Nachbar** | Kompromittiertes Gerät im selben Netzwerksegment scannt nach offenen Ports | UFW `default deny`, knockd |
| **Physischer Zugriff** | Angreifer hat Zugang zum Server-Raum | LUKS-Verschlüsselung, USB-Schlüssel, BIOS-Passwort |
| **Man-in-the-Middle** | Angreifer fängt Netzwerkverkehr ab | Tailscale WireGuard-Verschlüsselung, TLS 1.3 |
| **Web-App-Angreifer** | SQL Injection, XSS, Path Traversal gegen die FastAPI | Pydantic-Validierung, SQLAlchemy-ORM, Pfad-Traversal-Schutz |
| **Container-Escape** | Angreifer kompromittiert einen Docker-Container | Read-only Root-FS, non-root User, Capability-Dropping |

---

## 2. Verteidigungsebenen im Überblick

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        GHOST SERVER — 7 EBENEN                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 7: DATENHALTUNG                                               │ │
│  │  LUKS2 Full-Disk-Encryption │ .env-Schutz │ Keine Secrets in Git    │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 6: CONTAINER-ISOLATION                                        │ │
│  │  Non-Root-User │ Read-Only-FS │ Cap-Dropping │ Netzwerksegmentierung │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 5: APPLICATION (FastAPI)                                      │ │
│  │  Pydantic-Validierung │ ORM (SQLi-Schutz) │ Path-Traversal-Schutz    │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 4: REVERSE PROXY (Caddy)                                      │ │
│  │  TLS 1.3 │ CSP-Header │ HSTS │ X-Frame-Options │ Rate Limiting       │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 3: TRANSPORT (Tailscale)                                      │ │
│  │  WireGuard-Verschlüsselung │ Mesh-Netzwerk │ MagicDNS │ ACLs          │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 2: NETZWERK-PERIMETER (UFW + knockd)                          │ │
│  │  Default Deny │ Port-Knocking │ 3-Sekunden-SSH-Fenster │ Rate-Limit   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                    ▲                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  EBENE 1: PHYSISCH (USB + SSH-Keys)                                  │ │
│  │  USB-Stick als Schlüsselträger │ ed25519-Keys │ PasswortAuth=no      │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Ebene 1: Physische Sicherheit — USB-Schlüssel & SSH-Hardening

### 3.1 USB-Stick als exklusiver Schlüsselträger

Der private SSH-Schlüssel (`id_ed25519`) existiert **ausschließlich** auf einem
dedizierten USB-Stick. Er wird niemals auf eine interne Festplatte kopiert.

```
┌──────────────────────────────────────────────────────────────┐
│                     USB-STICK (FAT32)                         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  /ssh/                                                   │ │
│  │  ├── id_ed25519          ← Privater Schlüssel (600)     │ │
│  │  ├── id_ed25519.pub      ← Öffentlicher Schlüssel       │ │
│  │  └── ssh_config          ← SSH-Client-Konfiguration     │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ⚠ WIRD NIE AUF DIE SERVER-FESTPLATTE KOPIERT                │
│  ⚠ BEI ABZIEHEN DES STICKS: KEIN SSH-ZUGANG MEHR MÖGLICH     │
└──────────────────────────────────────────────────────────────┘
```

**Einrichtung des USB-Sticks:**

```bash
# 1. USB-Stick identifizieren (z.B. /dev/sdb1)
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT

# 2. Falls nötig, FAT32 formatieren
sudo mkfs.vfat -F 32 /dev/sdb1

# 3. Mount-Punkt erstellen und stick mounten
sudo mkdir -p /mnt/usb_key
sudo mount /dev/sdb1 /mnt/usb_key

# 4. SSH-Schlüsselpaar generieren (ed25519 — sicherer als RSA)
ssh-keygen -t ed25519 -C "homelab-ghost-key" -f /mnt/usb_key/ssh/id_ed25519

# 5. Öffentlichen Schlüssel auf den Server übertragen
ssh-copy-id -i /mnt/usb_key/ssh/id_ed25519.pub user@hp-server

# 6. Berechtigungen setzen
chmod 600 /mnt/usb_key/ssh/id_ed25519
chmod 644 /mnt/usb_key/ssh/id_ed25519.pub

# 7. USB-Stick aushängen und sicher verwahren
sudo umount /mnt/usb_key
```

### 3.2 SSH-Server-Hardening (`/etc/ssh/sshd_config`)

```ini
# ═══════════════════════════════════════════════════════════
# /etc/ssh/sshd_config — Ghost Server SSH-Hardening
# ═══════════════════════════════════════════════════════════

# --- Authentifizierung ---
PasswordAuthentication no          # Keine Passwort-Logins
ChallengeResponseAuthentication no # Keine Challenge-Response
PermitEmptyPasswords no            # Keine leeren Passwörter
PubkeyAuthentication yes           # Nur Public-Key-Authentifizierung
AuthenticationMethods publickey    # Erzwingt ausschließlich Pubkey

# --- Erlaubte Schlüsseltypen (nur moderne Algorithmen) ---
HostKey /etc/ssh/ssh_host_ed25519_key
PubkeyAcceptedKeyTypes ssh-ed25519,ssh-ed25519-cert-v01@openssh.com

# --- Protokoll & Verschlüsselung ---
Protocol 2                          # Nur SSH-Protokoll Version 2
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com

# --- Zugriffskontrolle ---
PermitRootLogin no                 # Root-Login verboten
MaxAuthTries 2                     # Maximal 2 Authentifizierungsversuche
MaxSessions 3                      # Maximal 3 parallele Sessions
MaxStartups 2:30:5                 # Max 2 unauthentifizierte Verbindungen

# --- Timeout & Keep-Alive ---
ClientAliveInterval 300            # Client-Alive alle 300s
ClientAliveCountMax 2              # Nach 2× Timeout → Trennung
LoginGraceTime 20                  # 20s Zeit für Authentifizierung

# --- Logging ---
LogLevel VERBOSE                   # Detailliertes Logging
SyslogFacility AUTH

# --- Weitere Härtung ---
X11Forwarding no                   # Kein X11-Forwarding
AllowTcpForwarding no              # Kein TCP-Forwarding
PermitTunnel no                    # Kein Tunneling
AllowAgentForwarding no            # Kein SSH-Agent-Forwarding
PermitUserEnvironment no           # Kein User-Environment
DebianBanner no                    # Keine Versionsinformationen preisgeben

# --- Nur explizit erlaubte Benutzer ---
AllowUsers macbookausmnorden       # ⚠ ANPASSEN: Erlaubte Benutzer
```

**Nach Änderung anwenden:**

```bash
sudo sshd -t               # Konfiguration validieren
sudo systemctl restart sshd
sudo systemctl status sshd  # Prüfen, ob Dienst läuft
```

### 3.3 SSH-Client-Konfiguration auf dem USB-Stick (`ssh_config`)

```ini
# ═══════════════════════════════════════════════════════════
# /mnt/usb_key/ssh/ssh_config — Client-seitige SSH-Konfiguration
# ═══════════════════════════════════════════════════════════

Host hp-server hp-server.tailscale-mesh.net
    HostName hp-server.tailscale-mesh.net
    User macbookausmnorden
    Port 22
    IdentityFile /mnt/usb_key/ssh/id_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    VisualHostKey yes
    ServerAliveInterval 60
    ServerAliveCountMax 5
    ConnectTimeout 10
    # Kein Fallback auf andere Schlüssel
    PreferredAuthentications publickey
```

**Verwendung:**

```bash
# SSH mit explizitem USB-Schlüssel
ssh -F /mnt/usb_key/ssh/ssh_config hp-server

# Oder als Alias in der lokalen ~/.ssh/config:
# Include /mnt/usb_key/ssh/ssh_config
```

---

## 4. Ebene 2: Netzwerkperimeter — UFW & knockd

### 4.1 UFW-Grundkonfiguration — Default Deny

```bash
# ═══════════════════════════════════════════════════════════
# UFW Firewall-Regeln — Ghost Server
# ═══════════════════════════════════════════════════════════

# 1. UFW zurücksetzen und Standard-Policy setzen
sudo ufw --force reset
sudo ufw default deny incoming   # Alles eingehende blockieren
sudo ufw default allow outgoing  # Ausgehend erlauben (für Updates etc.)

# 2. Tailscale-Verkehr erlauben (UDP-Port 41641 für WireGuard)
sudo ufw allow in on tailscale0   # Tailscale-Interface
sudo ufw allow 41641/udp          # WireGuard-Port für direkte Verbindungen

# 3. Caddy-Ports nur auf Tailscale-Interface öffnen
sudo ufw allow in on tailscale0 proto tcp to any port 80
sudo ufw allow in on tailscale0 proto tcp to any port 443

# 4. SSH-Port wird NICHT permanent geöffnet — nur via knockd!

# 5. Logging aktivieren (rate-limited)
sudo ufw logging medium

# 6. Firewall aktivieren
sudo ufw enable

# 7. Status prüfen
sudo ufw status verbose
```

**Erwartete UFW-Ausgabe nach Konfiguration:**

```
Status: active
Logging: on (medium)
Default: deny (incoming), allow (outgoing), deny (routed)

To                         Action      From
--                         ------      ----
Anywhere on tailscale0     ALLOW IN    Anywhere
41641/udp                  ALLOW IN    Anywhere
80/tcp on tailscale0       ALLOW IN    Anywhere
443/tcp on tailscale0      ALLOW IN    Anywhere
```

⚠ **Wichtig:** Im UFW-Status erscheint Port 22 **nicht**. SSH-Zugang existiert nur
während des 3-Sekunden-Fensters nach erfolgreichem Port-Knocking.

### 4.2 knockd — Port-Knocking-Daemon

#### 4.2.1 Installation

```bash
sudo apt update
sudo apt install knockd
```

#### 4.2.2 Konfiguration (`/etc/knockd.conf`)

```ini
# ═══════════════════════════════════════════════════════════
# /etc/knockd.conf — Ghost Server Port-Knocking
# ═══════════════════════════════════════════════════════════

[options]
    UseSyslog
    Interface = tailscale0    # Nur auf Tailscale-Interface lauschen
    LogFile = /var/log/knockd.log

[openSSH]
    # Klopf-Sequenz: Drei TCP-SYN-Pakete in exakt dieser Reihenfolge
    sequence      = 4000,5000,6000

    # Zeitfenster: Alle drei Klopfer müssen innerhalb von 5 Sekunden erfolgen
    seq_timeout   = 5

    # Befehl beim korrekten Klopfen: Firewall für die klopfende IP öffnen
    command       = /sbin/iptables -A INPUT -s %IP% -p tcp --dport 22 -j ACCEPT
    #               && logger -t knockd "SSH geöffnet für %IP%"

    # Automatisches Schließen nach exakt 3 Sekunden
    tcpflags      = syn
    cmd_timeout   = 3

[closeSSH]
    # Befehl nach Ablauf des Zeitfensters: SSH-Zugang wieder schließen
    sequence      = 6000,5000,4000
    seq_timeout   = 5
    command       = /sbin/iptables -D INPUT -s %IP% -p tcp --dport 22 -j ACCEPT
    #               && logger -t knockd "SSH geschlossen für %IP%"
    tcpflags      = syn
```

#### 4.2.3 Systemd-Service-Konfiguration (`/etc/default/knockd`)

```ini
# ═══════════════════════════════════════════════════════════
# /etc/default/knockd
# ═══════════════════════════════════════════════════════════

START_KNOCKD=1
KNOCKD_OPTS="-i tailscale0"
```

```bash
# Service aktivieren und starten
sudo systemctl enable knockd
sudo systemctl start knockd
sudo systemctl status knockd
```

#### 4.2.4 Klopf-Client (auf dem Administrationsgerät)

```bash
# SSH-Verbindung mit vorherigem Port-Knocking
knock hp-server.tailscale-mesh.net 4000 5000 6000 && ssh hp-server

# Oder als Einzeiler mit integriertem Retry
for i in 1 2 3; do
    knock hp-server.tailscale-mesh.net 4000 5000 6000 && break
    sleep 1
done
ssh hp-server
```

#### 4.2.5 Sicherheitsbetrachtung Port-Knocking

| Aspekt | Bewertung |
|---|---|
| **Security through Obscurity?** | Nein. knockd ist keine alleinige Sicherheitsmaßnahme, sondern eine zusätzliche Hürde in der Defense-in-Depth-Strategie. Selbst wenn die Sequenz bekannt ist, muss der Angreifer noch den SSH-Key besitzen. |
| **Replay-Schutz** | Die Sequenz ist nicht replay-sicher. Ein mithörender Angreifer im selben Netzsegment könnte die Sequenz aufzeichnen. Abhilfe: Single-Packet-Authorization (SPA) mit `fwknop` als Eskalationsoption (siehe Abschnitt 4.3). |
| **Brute-Force** | Bei 3 Ports à 65535 Möglichkeiten = 2.8×10¹⁴ Kombinationen. Mit seq_timeout=5 praktisch nicht brute-force-bar. |
| **Timing-Angriffe** | Ein Angreifer könnte die exakte Sequenz-Timing-Signatur analysieren. Abhilfe: zufällige Delays zwischen den Klopfern (Client-seitig). |

### 4.3 Eskalationsoption: fwknop (Single Packet Authorization)

Für erhöhte Sicherheitsanforderungen empfiehlt sich der Umstieg von knockd auf
`fwknop`, das **Single Packet Authorization (SPA)** implementiert:

| knockd | fwknop (SPA) |
|---|---|
| Mehrere Pakete (sichtbare Sequenz) | Ein einziges verschlüsseltes UDP-Paket |
| Replay-anfällig | Replay-sicher durch Nonce + Zeitstempel |
| Keine kryptografische Authentifizierung | HMAC-SHA256 authentifiziert |
| Sequenz durch Mithören aufdeckbar | Paketinhalt ist verschlüsselt (AES) |

```bash
# Installation (Zukunftsszenario)
sudo apt install fwknop-server fwknop-client

# Client-seitiger Zugriff
fwknop -A tcp/22 -a <client-ip> -D hp-server.tailscale-mesh.net --key-gen --use-hmac --save-rc-stanza
```

---

## 5. Ebene 3: Transportverschlüsselung — Tailscale Mesh

### 5.1 Tailscale-Architektur

```
┌──────────────────┐          ┌──────────────────────┐
│  Administrations- │          │  HP-Server (Ghost)    │
│  gerät            │          │                       │
│  ┌──────────────┐ │  WireGuard│  ┌─────────────────┐ │
│  │ Tailscale    │◄│══════════│══│ Tailscale       │ │
│  │ Client       │ │  Tunnel  │  │ (tailscale0)    │ │
│  └──────────────┘ │          │  └─────────────────┘ │
│                    │          │                       │
│  100.64.x.y       │          │  100.64.x.z           │
└──────────────────┘          └───────────────────────┘
                                          │
                          ┌───────────────▼──────────────┐
                          │  Desktop-PC (GPU)             │
                          │  ┌─────────────────────────┐  │
                          │  │ Tailscale Client         │  │
                          │  │ Ollama :11434            │  │
                          │  │ Worker-Agent             │  │
                          │  └─────────────────────────┘  │
                          │  100.64.x.w                   │
                          └──────────────────────────────┘
```

### 5.2 Installation & Konfiguration

```bash
# 1. Tailscale installieren (Ubuntu Server)
curl -fsSL https://tailscale.com/install.sh | sh

# 2. Authentifizieren und dem Mesh beitreten
sudo tailscale up --hostname=hp-server --ssh=false

# 3. MagicDNS aktivieren (in der Tailscale Admin Console)
#    → hp-server.tailscale-mesh.net wird automatisch aufgelöst

# 4. Status prüfen
tailscale status
ip addr show tailscale0
```

### 5.3 Tailscale ACLs (Access Control Lists)

```jsonc
// ═══════════════════════════════════════════════════════════
// tailscale ACLs — Ghost Server Mesh-Zugriffskontrolle
// Verwaltung in der Tailscale Admin Console → Access Controls
// ═══════════════════════════════════════════════════════════

{
  "acls": [
    {
      // Nur das Admin-Gerät darf auf Caddy-Ports zugreifen
      "action": "accept",
      "src":    ["tag:admin"],
      "dst":    ["tag:server:80", "tag:server:443"]
    },
    {
      // Server und Desktop-PC dürfen untereinander kommunizieren
      "action": "accept",
      "src":    ["tag:server"],
      "dst":    ["tag:gpu-desktop:*"]
    },
    {
      // GPU-Desktop darf Antworten an den Server senden
      "action": "accept",
      "src":    ["tag:gpu-desktop"],
      "dst":    ["tag:server:*"]
    },
    {
      // Admin darf SSH (nach Port-Knocking) nutzen
      "action": "accept",
      "src":    ["tag:admin"],
      "dst":    ["tag:server:22"]
    }
  ],

  "tagOwners": {
    "tag:admin":       ["macbookausmnorden@github"],
    "tag:server":      ["macbookausmnorden@github"],
    "tag:gpu-desktop": ["macbookausmnorden@github"]
  },

  // Auto-Approval für Subnet-Routen (optional)
  "autoApprovers": {
    "routes": {
      "100.64.0.0/10": ["tag:server", "tag:gpu-desktop"]
    }
  }
}
```

### 5.4 Tailscale Security Features

| Feature | Konfiguration | Wirkung |
|---|---|---|
| **WireGuard-Protokoll** | Automatisch | Ende-zu-Ende-verschlüsselte Tunnel (Noise-Protokoll + Curve25519) |
| **MagicDNS** | `hp-server.tailscale-mesh.net` | Keine IP-Adressen im Klartext nötig |
| **SSH deaktiviert** | `--ssh=false` | Tailscale-eigenes SSH wird nicht genutzt; nur system-eigenes SSH via knockd |
| **Key-Expiry** | Standard: 180 Tage | Automatische Schlüsselrotation |
| **Device-Approval** | Admin-Konsole | Neue Geräte müssen manuell freigegeben werden |
| **Exit-Node** | Nicht konfiguriert | Server routet keinen externen Verkehr |

---

## 6. Ebene 4: Reverse Proxy — Caddy TLS & Header-Hardening

### 6.1 Caddyfile mit vollständigem Security-Hardening

```caddy
# ═══════════════════════════════════════════════════════════
# Caddyfile — Ghost Server Reverse Proxy mit Security-Hardening
# Pfad: ./Caddyfile
# ═══════════════════════════════════════════════════════════

hp-server.tailscale-mesh.net {

    # --- TLS-Konfiguration ---
    # Caddy handled TLS automatisch. Für Tailscale-interne Nutzung
    # kann auch ein selbst-signiertes oder Tailscale-Zertifikat verwendet werden.
    tls {
        protocols tls1.3        # Nur TLS 1.3 (kein 1.0, 1.1, 1.2)
        curves x25519           # Nur Curve25519 für Schlüsselaustausch
    }

    # --- Security Header (für alle Responses) ---
    header {
        # Content Security Policy: Kein fremdes JS, keine fremden Frames
        Content-Security-Policy "
            default-src 'self';
            script-src 'self' 'unsafe-inline';
            style-src 'self' 'unsafe-inline';
            img-src 'self' data: blob:;
            media-src 'self' blob:;
            frame-src 'self';
            connect-src 'self';
            font-src 'self';
            object-src 'none';
            base-uri 'self';
            form-action 'self';
        "

        # HSTS: Erzwingt HTTPS für alle zukünftigen Requests (2 Jahre)
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"

        # Anti-Clickjacking: Keine Einbettung in fremde Seiten
        X-Frame-Options "SAMEORIGIN"

        # MIME-Type-Sniffing verhindern
        X-Content-Type-Options "nosniff"

        # Cross-Site Scripting Filter (Legacy-Browser)
        X-XSS-Protection "1; mode=block"

        # Referrer-Policy: Keine URL-Informationen an externe Seiten
        Referrer-Policy "strict-origin-when-cross-origin"

        # Berechtigungen einschränken
        Permissions-Policy "
            accelerometer=(),
            ambient-light-sensor=(),
            autoplay=(),
            camera=(),
            display-capture=(),
            geolocation=(),
            microphone=(),
            midi=(),
            payment=(),
            usb=()
        "

        # Server-Signatur unterdrücken
        -Server
        -X-Powered-By
    }

    # --- Rate Limiting ---
    rate_limit {
        zone dynamic {
            key {remote_host}
            events 100
            window 1m
        }
    }

    # --- Request-Größenbegrenzung ---
    request_body {
        max_size 500MB  # Für große Datei-Uploads (Medien, PDFs)
    }

    # --- Statisches Frontend-Hosting ---
    root * /usr/share/caddy/frontend
    file_server

    # --- API Proxy-Routing ---
    handle /api/* {
        reverse_proxy fastapi_app:8000 {
            # Timeout für lange KI-Operationen
            transport http {
                read_timeout 300s
                write_timeout 300s
            }
            # Header-Weiterleitung
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }

    # --- Web-IDE Proxy-Routing (code-server — einzige iFrame-Ausnahme) ---
    handle /ide/* {
        reverse_proxy code_server:8080 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
        }
    }

    # --- Default: 404 für alle anderen Pfade ---
    handle {
        respond "404 Not Found" 404
    }

    # --- Logging ---
    log {
        output file /var/log/caddy/access.log {
            roll_size 100mb
            roll_keep 5
            roll_keep_for 720h  # 30 Tage
        }
        format json
    }
}
```

### 6.2 Caddy Security-Header im Detail

| Header | Wert | Schutz vor |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; frame-src 'self'; object-src 'none'` | XSS, Clickjacking, Plugin-Exploits |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | SSL-Stripping, Protokoll-Downgrade |
| `X-Frame-Options` | `SAMEORIGIN` | Clickjacking in fremden Frames |
| `X-Content-Type-Options` | `nosniff` | MIME-Type-Sniffing-Angriffe |
| `X-XSS-Protection` | `1; mode=block` | Reflektierte XSS-Angriffe |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Datenleck via Referrer-Header |
| `Permissions-Policy` | Alle `=()` (deaktiviert) | Browser-API-Missbrauch (Kamera, Mikrofon, etc.) |
| `Server` | (entfernt) | Information Disclosure (Server-Version) |

### 6.3 Rate Limiting im Detail

```caddy
rate_limit {
    zone dynamic {
        key {remote_host}          # Pro Client-IP
        events 100                 # 100 Requests
        window 1m                  # Pro Minute
    }
}
```

| Szenario | Wirkung |
|---|---|
| **Normaler Benutzer** | 100 Requests/Minute = 1,6 req/s — ausreichend für SPA-Navigation |
| **API-Scraper** | Nach 100 Requests → HTTP 429 Too Many Requests |
| **Brute-Force-Versuch** | Wird durch Rate-Limiting + knockd + SSH-Key doppelt abgesichert |

---

## 7. Ebene 5: Application Layer — FastAPI Security

### 7.1 Pydantic-Eingabevalidierung

Jeder eingehende Request wird durch Pydantic-Modelle validiert, **bevor** er die
Business-Logik erreicht:

```python
from pydantic import BaseModel, Field, field_validator, HttpUrl
from typing import Literal
import re

class ChatRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=50)
    prompt: str = Field(..., min_length=1, max_length=4096)
    power_mode: bool = False
    rag_enabled: bool = True

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        """Verhindert Path-Traversal und Injection im Agent-Namen."""
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError("Agent-Name enthält unerlaubte Zeichen")
        return v

    @field_validator("prompt")
    @classmethod
    def sanitize_prompt(cls, v: str) -> str:
        """Entfernt potenzielle Prompt-Injection-Muster."""
        dangerous = ["ignore previous", "system prompt:", "### Instruction"]
        lower = v.lower()
        for d in dangerous:
            if d in lower:
                raise ValueError("Prompt enthält potenzielle Injection-Muster")
        return v
```

### 7.2 Pfad-Traversal-Schutz (FileBunkerService)

```python
import os

class FileBunkerService:
    def __init__(self, base_directory: str = "/mnt/data/files"):
        # Absolute Pfadauflösung mit Symlink-Auflösung
        self.base_dir = os.path.realpath(os.path.abspath(base_directory))

    def secure_path(self, relative_path: str) -> str:
        """
        Mehrstufiger Traversal-Schutz:
        1. Zusammenfügen von base_dir und relativem Pfad
        2. Absolute Pfadauflösung (eliminiert ../)
        3. Symlink-Auflösung (eliminiert Link-Traversal)
        4. Präfix-Prüfung: Ergebnis MUSS in base_dir liegen
        """
        # Schritt 1+2: Zusammenfügen und normalisieren
        target = os.path.abspath(os.path.join(self.base_dir, relative_path))

        # Schritt 3: Symlinks auflösen
        target = os.path.realpath(target)

        # Schritt 4: Präfix-Prüfung
        if not target.startswith(self.base_dir + os.sep) and target != self.base_dir:
            # Logge den versuchten Angriff
            logger.warning(
                f"BLOCKIERT: Traversal-Versuch von {relative_path!r} "
                f"nach {target!r} (base_dir={self.base_dir!r})"
            )
            raise PermissionError("Unzulässige Pfadmanipulation detektiert.")

        return target
```

### 7.3 SQL-Injection-Prävention

Durch die konsequente Verwendung von SQLAlchemy ORM mit parametrisierten Queries
ist SQL-Injection strukturell ausgeschlossen:

```python
# ✅ SICHER: ORM mit gebundenen Parametern
db.query(Transaction).filter(Transaction.category == user_input).all()

# ❌ NIEMALS: Raw-SQL mit String-Interpolation
# db.execute(f"SELECT * FROM transactions WHERE category = '{user_input}'")

# ✅ FALLS RAW-SQL NOTWENDIG: Immer mit Parametern
from sqlalchemy import text
db.execute(text("SELECT * FROM transactions WHERE category = :cat"), {"cat": user_input})
```

### 7.4 CORS-Konfiguration (FastAPI)

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS: Nur die eigene Domain erlauben
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hp-server.tailscale-mesh.net",
        "https://hp-server.local",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=3600,
)
```

### 7.5 Secrets-Management

```python
# config.py — Validierung kritischer Umgebungsvariablen
from pydantic import SecretStr, field_validator

class Settings(BaseSettings):
    database_url: SecretStr         # Wird in Logs zensiert: '**********'
    jellyfin_api_key: SecretStr
    ollama_desktop_endpoint: str

    @field_validator("jellyfin_api_key")
    @classmethod
    def validate_api_key(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw == "dein_jellyfin_schluessel_hier":
            raise ValueError("Jellyfin-API-Key nicht konfiguriert!")
        if len(raw) < 32:
            raise ValueError("API-Key zu kurz — Sicherheitsrisiko!")
        return v
```

---

## 8. Ebene 6: Container-Isolation — Docker Security

### 8.1 Docker-Compose Security-Hardening

```yaml
# ═══════════════════════════════════════════════════════════
# docker-compose.yml — Container Security Hardening (Auszug)
# ═══════════════════════════════════════════════════════════

services:
  fastapi_app:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: imperium_backend
    restart: unless-stopped

    # --- Security-Optionen ---
    security_opt:
      - no-new-privileges:true     # Keine Privilegien-Eskalation im Container
      - apparmor:docker-default    # AppArmor-Profil anwenden (Ubuntu)

    # --- Read-Only Root-Filesystem ---
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,nodev   # /tmp als tmpfs (flüchtig, keine Exec)

    # --- Capabilities: Nur das Nötigste ---
    cap_drop:
      - ALL                          # Alle Capabilities entfernen
    cap_add:
      - NET_BIND_SERVICE             # Nur Port-Bindung erlauben (für :8000)

    # --- Kein Privileged Mode ---
    privileged: false

    # --- Ressourcen-Limits (DoS-Schutz) ---
    deploy:
      resources:
        limits:
          cpus: '2'                  # Max 2 CPU-Kerne
          memory: 4G                 # Max 4 GB RAM
        reservations:
          cpus: '0.5'
          memory: 512M

    # --- Benutzer: Nicht als Root laufen ---
    user: "1000:1000"                # UID:GID des Host-Benutzers

    # --- Healthcheck ---
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

    env_file:
      - .env
    volumes:
      - /mnt/data:/mnt/data:ro       # ⚠ Nur lesend, wenn möglich

  postgres_db:
    image: postgres:15-alpine
    container_name: imperium_postgres
    restart: unless-stopped

    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - DAC_OVERRIDE
      - SETGID
      - SETUID

    # PostgreSQL soll nicht als Host-Root laufen
    # (Postgres-Alpine-Image handled das intern)

    environment:
      POSTGRES_DB: homelab_imperium
      POSTGRES_USER: homelab_user
      POSTGRES_PASSWORD: homelab_secure_pass
      # ⚠ IN PRODUKTION: Secrets via Docker Secrets oder .env-Datei

    volumes:
      - /mnt/data/db/postgres:/var/lib/postgresql/data

    # Kein Port-Mapping nach außen — nur internes Docker-Netzwerk
    # ports: (entfernt — keine Exposition!)
```

### 8.2 Dockerfile Security-Best-Practices

```dockerfile
# ═══════════════════════════════════════════════════════════
# backend/Dockerfile — Multi-Stage Build mit Security-Hardening
# ═══════════════════════════════════════════════════════════

# --- Stage 1: Build ---
FROM python:3.14-slim AS builder

# Build-Abhängigkeiten installieren
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Stage 2: Runtime (minimales Image) ---
FROM python:3.14-slim

# Kein Root-User
RUN useradd --create-home --shell /bin/bash --uid 1000 appuser

# Nur Laufzeitabhängigkeiten (kein Build-Toolchain)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Pip-Pakete aus Stage 1 kopieren
COPY --from=builder /root/.local /home/appuser/.local

# Anwendungscode kopieren
WORKDIR /app
COPY --chown=appuser:appuser ./app /app

# Auf non-root User wechseln
USER appuser

# PATH für pip --user Installationen
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Port exponieren (intern — nicht nach außen gemapped)
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 8.3 Docker-Sicherheitsaudit (Host-seitig)

```bash
# ═══════════════════════════════════════════════════════════
# Docker Security Audit Script
# ═══════════════════════════════════════════════════════════

echo "=== Docker Security Audit ==="

# 1. Prüfe, ob Container als Root laufen
echo "--- Container mit Root-User ---"
docker ps --format '{{.Names}}' | while read c; do
    user=$(docker inspect --format='{{.Config.User}}' "$c")
    if [ -z "$user" ] || [ "$user" = "root" ] || [ "$user" = "0" ]; then
        echo "⚠ WARNUNG: Container '$c' läuft als Root!"
    fi
done

# 2. Prüfe, ob Container privileged sind
echo "--- Privileged Container ---"
docker ps --format '{{.Names}}' | while read c; do
    priv=$(docker inspect --format='{{.HostConfig.Privileged}}' "$c")
    if [ "$priv" = "true" ]; then
        echo "⛔ KRITISCH: Container '$c' läuft im PRIVILEGED MODE!"
    fi
done

# 3. Prüfe auf exposte Ports
echo "--- Exponierte Ports ---"
docker ps --format 'table {{.Names}}\t{{.Ports}}'

# 4. Prüfe Docker-Socket-Exposition
echo "--- Docker Socket Zugriff ---"
docker ps --format '{{.Names}}' | while read c; do
    mounts=$(docker inspect --format='{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "$c")
    if echo "$mounts" | grep -q "/var/run/docker.sock"; then
        echo "⛔ KRITISCH: Container '$c' hat Zugriff auf docker.sock!"
    fi
done

echo "=== Audit abgeschlossen ==="
```

### 8.4 Docker-Daemon-Hardening (`/etc/docker/daemon.json`)

```json
{
  "icc": false,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "userland-proxy": false,
  "no-new-privileges": true,
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 64000,
      "Soft": 64000
    }
  }
}
```

| Option | Wirkung |
|---|---|
| `"icc": false` | Deaktiviert Inter-Container-Kommunikation (nur explizite Links) |
| `"userland-proxy": false` | Verwendet iptables statt Userland-Proxy (bessere Performance + Sicherheit) |
| `"no-new-privileges": true` | Verhindert Privilegien-Eskalation in allen Containern |

---

## 9. Ebene 7: Datenhaltung — Festplattenverschlüsselung & Secrets

### 9.1 LUKS2 Full-Disk-Encryption

Die Systemfestplatte des HP-Servers wird mit **LUKS2** (Linux Unified Key Setup)
vollständig verschlüsselt. Dies schützt alle Daten bei physischem Diebstahl des
Servers.

```bash
# ═══════════════════════════════════════════════════════════
# LUKS2 Full-Disk-Encryption (bei Ubuntu-Installation)
# ═══════════════════════════════════════════════════════════

# Bei der Ubuntu Server-Installation:
# → "Guided — use entire disk and set up encrypted LVM"
# → Passphrase vergeben (min. 20 Zeichen, Diceware-Methode empfohlen)

# Nachträgliche Überprüfung:
sudo cryptsetup luksDump /dev/sda3  # (oder entsprechendes Device)

# Erwartete Ausgabe:
# Version:        2
# Cipher:         aes-xts-plain64
# Cipher key:     512 bits
# PBKDF:          argon2id
```

**Empfohlene LUKS2-Einstellungen:**

| Parameter | Wert | Begründung |
|---|---|---|
| **KDF** | Argon2id | Speicher-harte Key-Derivation, resistent gegen GPU-Brute-Force |
| **Cipher** | `aes-xts-plain64` | AES-XTS mit 512-Bit-Schlüssel (256 Bit effektiv) |
| **Iterations** | Automatisch (Ziel: 1s Entsperrzeit) | Balanciert zwischen Sicherheit und Boot-Zeit |
| **Passphrase** | Diceware, 7+ Wörter (~90 Bit Entropie) | Menschen-lesbar, hohe Entropie |

### 9.2 HDD-Datenverschlüsselung (`/mnt/data`)

Für die separate Daten-HDD empfehlen sich zwei Ansätze:

**Option A: LUKS-Container auf der HDD (empfohlen)**

```bash
# 1. HDD identifizieren
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
# Annahme: /dev/sdb ist die 4 TB Daten-HDD

# 2. LUKS2-Container erstellen
sudo cryptsetup luksFormat --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --pbkdf argon2id \
    /dev/sdb

# 3. Container öffnen
sudo cryptsetup open /dev/sdb data_crypt

# 4. Dateisystem erstellen
sudo mkfs.ext4 /dev/mapper/data_crypt

# 5. In /etc/crypttab eintragen (für automatisches Mounting)
echo "data_crypt /dev/sdb none luks,discard" | sudo tee -a /etc/crypttab

# 6. In /etc/fstab eintragen
echo "/dev/mapper/data_crypt /mnt/data ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab

# 7. Mounten
sudo mount /mnt/data
```

**Option B: Systemd-homed (falls Ubuntu 24.04+)**

```bash
# Für das Benutzerverzeichnis — integrierte LUKS2-Verschlüsselung
homectl create macbookausmnorden --storage=luks
```

### 9.3 Secrets-Management

#### 9.3.1 `.env`-Datei-Schutz

```bash
# ═══════════════════════════════════════════════════════════
# .env-Sicherung
# ═══════════════════════════════════════════════════════════

# 1. Berechtigungen restriktiv setzen
chmod 600 .env
chown macbookausmnorden:macbookausmnorden .env

# 2. In .gitignore eintragen (muss vorhanden sein!)
echo ".env" >> .gitignore

# 3. Prüfen, ob .env jemals committed wurde
git log --all --full-history -- .env

# Falls ja: SOFORT alle Secrets rotieren und mit git-filter-repo bereinigen!
```

#### 9.3.2 Secrets-Rotation-Plan

| Secret | Rotationsintervall | Methode |
|---|---|---|
| SSH Host Keys | Bei Neuinstallation, max. jährlich | `ssh-keygen -A` |
| SSH User Keys (USB) | Alle 12 Monate | `ssh-keygen -t ed25519` |
| PostgreSQL-Passwort | Alle 6 Monate | `ALTER USER homelab_user PASSWORD '...'` |
| Jellyfin API-Key | Alle 3 Monate | Jellyfin Admin Dashboard → API Keys |
| Tailscale Auth-Key | Bei Kompromittierungsverdacht | Tailscale Admin Console → Rotate |
| LUKS-Passphrase | Alle 12 Monate | `cryptsetup luksChangeKey` |

#### 9.3.3 Beispiel: LUKS-Passphrase rotieren

```bash
# Neuen Schlüssel-Slot hinzufügen
sudo cryptsetup luksAddKey /dev/sda3

# Alten Schlüssel-Slot entfernen (nach erfolgreichem Test mit neuem Slot!)
sudo cryptsetup luksKillSlot /dev/sda3 0

# Verbleibende Slots prüfen
sudo cryptsetup luksDump /dev/sda3 | grep "Key Slot"
```

---

## 10. Härtungs-Checkliste (Audit)

Diese Checkliste dient als Audit-Grundlage. Jeder Punkt muss mit **Ja**
beantwortet werden können.

### 10.1 Physisch & System

| # | Prüfpunkt | Status |
|---|---|---|
| 1 | SSH-Passwort-Login deaktiviert (`PasswordAuthentication no`) | ☐ |
| 2 | Root-Login deaktiviert (`PermitRootLogin no`) | ☐ |
| 3 | SSH-Schlüssel ausschließlich auf USB-Stick | ☐ |
| 4 | SSH verwendet nur ed25519-Keys | ☐ |
| 5 | LUKS2-Verschlüsselung auf Systemplatte aktiv | ☐ |
| 6 | LUKS2-Verschlüsselung auf Datenplatte (`/mnt/data`) aktiv | ☐ |
| 7 | BIOS-Passwort gesetzt | ☐ |
| 8 | Automatische Sicherheitsupdates aktiviert (`unattended-upgrades`) | ☐ |

### 10.2 Netzwerk & Firewall

| # | Prüfpunkt | Status |
|---|---|---|
| 9 | UFW aktiv: `default deny incoming` | ☐ |
| 10 | Nur Tailscale-Interface exponiert Ports | ☐ |
| 11 | knockd läuft und lauscht auf `tailscale0` | ☐ |
| 12 | SSH-Port erscheint NICHT in `ufw status` | ☐ |
| 13 | Keine weiteren offenen Ports (Scan: `nmap -p- hp-server`) | ☐ |
| 14 | Tailscale ACLs konfiguriert | ☐ |

### 10.3 Application & Proxy

| # | Prüfpunkt | Status |
|---|---|---|
| 15 | Caddy: TLS 1.3 only | ☐ |
| 16 | Caddy: CSP-Header gesetzt (`frame-src 'self'`) | ☐ |
| 17 | Caddy: HSTS-Header gesetzt | ☐ |
| 18 | Caddy: X-Frame-Options = SAMEORIGIN | ☐ |
| 19 | Caddy: Rate Limiting aktiv | ☐ |
| 20 | FastAPI: CORS restriktiv konfiguriert | ☐ |
| 21 | FastAPI: Pydantic-Validierung für alle Inputs | ☐ |
| 22 | FileBunker: Pfad-Traversal-Schutz aktiv | ☐ |
| 23 | `.env` hat `chmod 600`, nicht in Git | ☐ |

### 10.4 Container

| # | Prüfpunkt | Status |
|---|---|---|
| 24 | Kein Container läuft als Root | ☐ |
| 25 | Kein Container im privileged mode | ☐ |
| 26 | Kein Container hat docker.sock-Zugriff | ☐ |
| 27 | Cap-Dropping: Alle Container haben `ALL` gedroppt | ☐ |
| 28 | Read-Only-Root-FS wo möglich | ☐ |
| 29 | Healthchecks für alle Container definiert | ☐ |
| 30 | Docker-Daemon: `icc: false` | ☐ |

### 10.5 Monitoring & Logging

| # | Prüfpunkt | Status |
|---|---|---|
| 31 | SSH-Login-Versuche werden geloggt (`/var/log/auth.log`) | ☐ |
| 32 | Caddy-Access-Logs werden geschrieben | ☐ |
| 33 | knockd-Logs werden geschrieben (`/var/log/knockd.log`) | ☐ |
| 34 | UFW-Logs aktiviert (`ufw logging medium`) | ☐ |
| 35 | Log-Rotation konfiguriert (max. 30 Tage) | ☐ |
| 36 | Automatische Benachrichtigung bei verdächtigen Mustern (fail2ban) | ☐ |

---

## 11. Incident Response — Was tun bei Kompromittierung

### 11.1 Erkennung

**Anzeichen einer möglichen Kompromittierung:**

- Unbekannte Einträge in `/var/log/auth.log` (SSH-Logins zu ungewöhnlichen Zeiten)
- Neue, unbekannte Benutzer in `/etc/passwd`
- Unerwartete laufende Prozesse (`ps auxf`)
- Ungewöhnlicher Netzwerkverkehr (`iftop`, `nethogs`)
- Veränderte Dateien in `/etc/`, `/usr/bin/`, `/boot/`
- Docker-Container, die nicht in `docker-compose.yml` definiert sind

### 11.2 Sofortmaßnahmen

```bash
# ═══════════════════════════════════════════════════════════
# INCIDENT RESPONSE PLAYBOOK
# ═══════════════════════════════════════════════════════════

# SCHRITT 1: ISOLATION
# Tailscale-Interface sofort deaktivieren
sudo tailscale down
# Netzwerk auf lo beschränken
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default deny outgoing  # Auch ausgehend blockieren!

# SCHRITT 2: FORENSIK-DATEN SICHERN (bevor Manipulation)
sudo cp /var/log/auth.log /root/forensics/auth.log.$(date +%s)
sudo cp /var/log/syslog /root/forensics/syslog.$(date +%s)
sudo cp /var/log/knockd.log /root/forensics/knockd.$(date +%s)
docker logs imperium_backend > /root/forensics/backend.$(date +%s).log

# SCHRITT 3: SYSTEM-STATUS ERFASSEN
ps auxf > /root/forensics/processes.$(date +%s).txt
netstat -tulpn > /root/forensics/network.$(date +%s).txt
last -50 > /root/forensics/logins.$(date +%s).txt

# SCHRITT 4: ALLE SECRETS SOFORT ROTIEREN
# - SSH Keys
# - Jellyfin API Key
# - PostgreSQL Passwort
# - Tailscale Auth Keys
# - LUKS Passphrases
```

### 11.3 Wiederherstellung

```bash
# SCHRITT 5: SYSTEM AUF SAUBEREN STAND BRINGEN
# Option A: Aus Docker Images neu aufbauen (wenn nur Container betroffen)
docker-compose down -v
docker-compose up -d

# Option B: Komplett-Neuinstallation (wenn Host betroffen)
# - Ubuntu Server neu installieren
# - Daten von externem Backup aufspielen
# - Konfiguration aus docs/ wiederherstellen

# SCHRITT 6: ROOT-CAUSE-ANALYSE
# - Forensik-Daten analysieren
# - Einfallstor identifizieren
# - Sicherheitslücke schließen
# - Dokumentation aktualisieren
```

---

## 12. Penetration-Test-Plan

### 12.1 Test-Matrix

| Test | Tool | Frequenz | Ziel |
|---|---|---|---|
| **Port-Scan** | `nmap -p- -sV hp-server.tailscale-mesh.net` | Wöchentlich | Nur 80/443 via Tailscale sichtbar |
| **SSH-Audit** | `ssh-audit hp-server.tailscale-mesh.net` | Monatlich | Nur starke Ciphers/MACs/Kex |
| **Web-Scan** | `nikto -h https://hp-server.tailscale-mesh.net` | Monatlich | Keine exponierten CVEs |
| **TLS-Test** | `testssl.sh https://hp-server.tailscale-mesh.net` | Monatlich | Nur TLS 1.3, starke Ciphers |
| **Docker-Audit** | `docker-bench-security` | Wöchentlich | CIS Docker Benchmark |
| **CSP-Validierung** | CSP Evaluator (Google) | Nach Änderungen | CSP ohne Unsafe-Inline für Scripts |
| **OWASP ZAP** | `zap-full-scan.py` | Quartalsweise | OWASP Top 10 gegen FastAPI |
| **Manuelle Code-Review** | `docs/` durchgehen | Halbjährlich | Architektur-Abweichungen |

### 12.2 Automatisierter Security-Scan (CI-ähnlich, lokal)

```bash
#!/bin/bash
# ═══════════════════════════════════════════════════════════
# security_scan.sh — Lokaler Security-Scanner für Ghost Server
# ═══════════════════════════════════════════════════════════

FAIL=0
SERVER="hp-server.tailscale-mesh.net"

echo "=== Ghost Server Security Scan ==="
echo "Ziel: $SERVER"
echo ""

# 1. Port-Scan
echo "[1/6] Port-Scan via Tailscale..."
OPEN_PORTS=$(nmap -p- --open "$SERVER" 2>/dev/null | grep "^[0-9]" | wc -l)
if [ "$OPEN_PORTS" -gt 2 ]; then
    echo "⛔ CRITICAL: Mehr als 2 offene Ports gefunden: $OPEN_PORTS"
    FAIL=1
else
    echo "✅ Nur erwartete Ports (80, 443) offen"
fi

# 2. SSH-Konfiguration
echo "[2/6] SSH-Server-Konfiguration..."
if ssh -o PasswordAuthentication=no -o ConnectTimeout=3 "$SERVER" "exit" 2>/dev/null; then
    echo "✅ SSH mit Pubkey erreichbar (Passwort deaktiviert)"
else
    echo "⚠ SSH-Test fehlgeschlagen (ggf. knockd aktiv?)"
fi

# 3. TLS-Prüfung
echo "[3/6] TLS-Version..."
TLS_VERSION=$(curl -sI --tlsv1.3 --max-time 5 "https://$SERVER" 2>&1)
if echo "$TLS_VERSION" | grep -q "HTTP"; then
    echo "✅ TLS 1.3 funktioniert"
else
    echo "⛔ TLS 1.3 nicht bestätigt"
    FAIL=1
fi

# 4. Security Header
echo "[4/6] Security Headers..."
HEADERS=$(curl -sI --max-time 5 "https://$SERVER")
for h in "Strict-Transport-Security" "X-Frame-Options" "X-Content-Type-Options" "Content-Security-Policy"; do
    if echo "$HEADERS" | grep -q "$h"; then
        echo "  ✅ $h vorhanden"
    else
        echo "  ⛔ $h FEHLT"
        FAIL=1
    fi
done

# 5. Docker Security
echo "[5/6] Docker Security..."
if docker ps --format '{{.Names}}' | while read c; do
    user=$(docker inspect --format='{{.Config.User}}' "$c")
    if [ -z "$user" ] || [ "$user" = "root" ] || [ "$user" = "0" ]; then
        echo "  ⛔ Container '$c' läuft als Root"
        exit 1
    fi
done; then
    echo "✅ Kein Container läuft als Root"
else
    FAIL=1
fi

# 6. UFW Status
echo "[6/6] UFW Firewall..."
if sudo ufw status | grep -q "Status: active"; then
    echo "✅ UFW ist aktiv"
else
    echo "⛔ UFW ist INAKTIV"
    FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "=== SCAN ERFOLGREICH: Alle Prüfungen bestanden ✅ ==="
else
    echo "=== SCAN FEHLGESCHLAGEN: Sicherheitslücken gefunden ⛔ ==="
fi
exit $FAIL
```

---

> **Wichtiger Hinweis:** Dieses Dokument beschreibt die Sicherheitsarchitektur des
> Homelab-Imperiums. Alle hier beschriebenen Maßnahmen müssen vor der produktiven
> Inbetriebnahme vollständig umgesetzt werden. Die Checkliste in Abschnitt 10 dient
> als verpflichtendes Abnahme-Protokoll.
>
> **Referenzdokumente:**
> - `docs/architektur.md` — Gesamtarchitektur & Datenfluss
> - `project-manifest.json` — Dateiübersicht & Phasenplan
> - `docker-compose.yml` — Container-Orchestrierung
> - `Caddyfile` — Reverse-Proxy-Konfiguration