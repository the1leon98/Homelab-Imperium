# Homelab-Imperium 🚀 — Das ultimative HomeOS-Dashboard

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen?style=for-the-badge)](https://github.com/the1leon98/Homelab-Imperium)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-orchestrated-blue?style=for-the-badge&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=for-the-badge)](https://opensource.org/licenses/MIT)

---

## 🌌 1. Die Vision: Dein persönliches HomeOS

**Homelab-Imperium** ist kein starrer Zusammenschluss isolierter Anwendungen, sondern ein hochintegriertes, ressourcenschonendes und extrem sicheres Orchestrierungs-Dashboard (CasaOS-Style). Entwickelt für den anspruchsvollen Headless-Betrieb auf einem minimierten **Ubuntu Server 24.04 LTS**, fungiert die Plattform als zentrales Nervensystem deines digitalen Haushalts.

Unter der Haube arbeitet ein leistungsstarkes **FastAPI-Backend** als *Universal-Übersetzer*. Es abstrahiert Systemaufrufe, verwaltet Container-Lebenszyklen und bündelt verteilte künstliche Intelligenz, während das **Vanilla-JS-Frontend** im atemberaubenden, performanten **Glassmorphismus- & Cyber-Dark-Design** erstrahlt. Keine unnötigen Framework-Abhängigkeiten, keine Build-Pipelines — nur pure, ultraschnelle Webschnittstellen.

---

## 🛠️ 2. Die 11 funktionalen Kern-Module

Das Imperium bündelt elf spezialisierte Anwendungsbereiche in einer einzigen, konsistenten UI:

*   🖥️ **System-Monitor**: Echtzeit-Diagnose von CPU-Last (pro Kern), RAM-Belegung, HDD-Kapazitäten und System-Temperaturen via `psutil`.
*   💼 **Finanz-Zentrale**: Ein lokales, sicheres Ledger-System zur Erfassung von Einnahmen und Ausgaben mit automatischen Budget-Warnungen.
*   🏎️ **3D Automotive Workbench**: Interaktive Vektor-Fahrzeugmodelle (Auto & Motorrad) mit aufschwenkbarer Motorhaube und leuchtenden Glow-Effekten für überfällige Wartungsarbeiten.
*   🩺 **Holografisches Bio-Tracking**: Ein biometrisches Gesundheitstagebuch, das Verletzungen und Schmerzpunkte direkt auf einem rot glühenden, holografischen 3D-Körper-Modell (SVG) visualisiert.
*   🤖 **AI Studio (4 Agenten)**: Ein vereinheitlichtes Chat-Interface für den schnellen Wechsel zwischen dem **Fachinformatiker-Tutor**, dem **Motoren-Konstrukteur**, dem **Health-Director** und dem **Kreativ-Brainstormer**.
*   📦 **Datei-Bunker**: Ein performanter Dateimanager mit Unterstützung für Drag-and-Drop-Uploads, Stream-Downloads und asynchrone Konvertierungen.
*   🎬 **Medien-Bunker**: Ein vollständig eingebetteter HTML5-Videoplayer, der geschützte Streaming-Schnittstellen direkt aus dem gekapselten Jellyfin-Backend abfragt.
*   🎓 **Ausbildungs-Portal**: Notenspiegel-Verwaltung mit gewichteten GPA-Berechnungen und einer automatischen PDF-RAG-Vektorpipeline für Schulskripte.
*   💻 **Code-Agent**: Eine isolierte Entwicklungsumgebung zur automatisierten Code-Analyse, Syntax-Optimierung und Sandbox-Ausführung.
*   ⚙️ **Integrierte Web-IDE**: VS Code-Server (code-server) nahtlos integriert als proxy-gesicherter, isolierter Entwicklungs-Workspace.
*   🎵 **Musik-Player**: Ein lokaler Streaming-Dienst, der ID3-Tags und Cover-Artworks einliest und Audiodateien direkt an dein Endgerät streamt.

---

## 🛡️ 3. Architektur & Sicherheits-Härtung

Das System basiert auf dem unumstößlichen Prinzip der **Schnittstellen-Isolation**:
┌─────────────────────────────────────────┐
│        FRONTEND (Cyber-Dark UI)         │
│    Vanilla HTML5 + CSS + Hash-Router    │
└────────────────────┬────────────────────┘
                     │ (/api/* Secure Tunnel)
                     ▼
┌─────────────────────────────────────────┐
│         BACKEND (FastAPI Engine)        │
│     Universal-Translator & AI-Router    │
└─────┬──────────┬──────────┬──────────┬──┘
      │          │          │          │
      ▼          ▼          ▼          ▼
   Postgres    Chroma     Ollama    Jellyfin
   (SQL-DB)   (Vectors)  (AI-LLM)   (Stream)

### 🔒 Die Goldenen Sicherheits-Regeln:
1.  **Das Iframe-Verbot**: Das Frontend kommuniziert ausschließlich mit dem FastAPI-Backend über `/api`. Direkte Netzwerkzugriffe auf Jellyfin, ChromaDB, Ollama oder das Dateisystem sind strikt untersagt. Die einzige Ausnahme bildet der über Caddy-Sicherheits-Header geschützte code-server.
2.  **Dateisystem-Schutz**: Jede Datei-Interaktion wird im Backend über einen normalisierenden Pfad-Traversal-Guard geschützt, um Angriffe über relative Pfadangaben (`../`) vollständig zu blockieren.
3.  **Kryptografischer Port-Knocking-Schutz**: Der Server blockiert jeglichen unautorisierten Netzwerkverkehr und antwortet auf keinen Ping. Erst eine exakte Klopfsequenz an den TCP-Ports **4000 -> 5000 -> 6000** öffnet den SSH-Port für exakt 3 Sekunden.
4.  **Hardware-Schlüssel-Pflicht**: Passwörter sind deaktiviert. Die Authentifizierung erfolgt ausschließlich über kryptografische SSH-Schlüssel, die auf einem physischen USB-Stick (Sicherheitsschlüssel) aufbewahrt werden.

---

## 📂 4. Repository-Struktur
.
├── Caddyfile              # Reverse Proxy & SSL
├── docker-compose.yml     # Container-Orchestrierung
├── requirements.txt       # Python-Abhängigkeiten
├── .env.example           # Config-Vorlage
├── config/
│   └── agents/            # Prompts der 4 AI-Agenten
│       ├── it_tutor.yaml
│       ├── auto_engineer.yaml
│       ├── medical_health.yaml
│       └── brainstorm_agent.yaml
├── docs/
│   ├── architektur.md     # Systemarchitektur
│   └── security_hardening.md # Server-Härtung (knockd)
├── backend/
│   └── app/
│       ├── main.py        # FastAPI-Einstieg & CORS
│       ├── config.py      # Pydantic Settings
│       ├── database.py    # Postgres-Verbindung
│       ├── models.py      # Relationale SQL-Modelle
│       ├── schemas.py     # Pydantic-Schemas
│       ├── routers/       # HTTP-Endpunkte (AI, Files)
│       └── services/      # Business-Logik & Clients
└── frontend/
    └── static/            # Single-Page-App Assets
        ├── index.html     # HTML5 SPA Shell
        ├── styles/        # CSS (Tokens & Glassmorphism)
        └── js/            # ES-Module & Views
---

## 📅 5. Deployment & Produktions-Roadmap

Die Software befindet sich aktuell in einem vollständig implementierten, produktionsbereiten **Dry-Run-Zustand** auf einem portablen Speichermedium.

*   [x] **Phase 0 & Phase 1**: Architektur-Initialisierung, API-Design, Design-Tokens-Extraktion und vollständige Scaffolding-Erstellung abgeschlossen.
*   [x] **Lokaler Testlauf**: Erfolgreiche Validierung des asynchronen FastAPI-Services und der interaktiven SVGs (3D-Modelle für Auto und Körper-Hologramm).
*   [ ] **Inbetriebnahme (August 2026)**: Sobald das physische LAN-Netzwerk eingerichtet ist, erfolgt der direkte Transfer auf den physischen, auf **16 GB RAM** aufgerüsteten HP Laptop-Server. Der Start des gesamten Stacks erfolgt über einen einzigen Befehl:

```bash
docker compose up -d --build
````

---

## ⚖️ 6. Lizenz & Urheberrecht

Dieses Projekt ist Open-Source und unter den Bedingungen der **MIT-Lizenz** lizenziert. 

Das bedeutet für dich:
* **Freie Nutzung:** Du darfst den Code des Homelab-Imperiums kostenlos für private oder kommerzielle Zwecke nutzen, modifizieren und verbreiten.
* **Bedingung:** Der originale Urheberrechtsvermerk und der Text der MIT-Lizenz müssen in allen Kopien oder wesentlichen Teilen der Software enthalten sein.
* **Keine Gewährleistung:** Die Software wird "wie besehen" (as is) bereitgestellt, ohne jegliche ausdrückliche oder implizite Garantie.

Die vollständigen Lizenzbedingungen findest du in der separaten `LICENSE`-Datei.

```text
Copyright (c) 2026 the1leon98

Hiermit wird allen Personen, die eine Kopie dieser Software und der 
zugehörigen Dokumentationsdateien erhalten, die Erlaubnis erteilt, 
die Software uneingeschränkt zu nutzen, einschließlich des Rechts, 
sie zu verwenden, zu kopieren, zu verändern, zu fusionieren, zu 
veröffentlichen, zu verbreiten, unterzulizensieren und/oder zu verkaufen.
```
