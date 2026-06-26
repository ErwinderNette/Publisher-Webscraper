# Gmail OAuth für QC-E-Mail-Entwürfe

1. [Google Cloud Console](https://console.cloud.google.com/) → neues Projekt oder bestehendes wählen
2. **APIs & Services** → **Bibliothek** → **Gmail API** aktivieren (im **gleichen Projekt** wie der Desktop-OAuth-Client aus Schritt 4–5)
3. **OAuth consent screen** → Internal (Workspace) oder External mit Testnutzer `es@uppr.de`
4. **Credentials** → **Create Credentials** → **OAuth client ID** → **Desktop app** (nicht „Web application“ / nicht der n8n-Client)
5. JSON herunterladen als `credentials.json` in diesen Ordner legen  
   Die Datei muss `"installed": { ... }` enthalten (nicht nur `"web":`).
6. Beim ersten QC-Lauf mit Handlungsbedarf öffnet sich der Browser für OAuth:
   - **`js@uppr.de`** → Publisher-Entwürfe (`token_js.json`)
   - **`es@uppr.de`** → Versand der QC-Benachrichtigung (`token_es.json`, Legacy: `token.json`)

### Fehler `name: gmail  version: v1` oder Gmail-Entwürfe schlagen fehl

Oft: Gmail API im **Desktop-Projekt** nicht aktiviert, oder defekte Python-Pakete.

1. [Gmail API aktivieren](https://console.cloud.google.com/apis/library/gmail.googleapis.com) – richtiges Projekt oben auswählen
2. 2–5 Minuten warten
3. QC erneut: `python main.py` (OAuth mit `token.json` meist nicht erneut nötig)

Optional Pakete reparieren: `pip install --force-reinstall google-api-python-client`

### Fehler `redirect_uri_mismatch`

Ursache: `credentials.json` ist ein **Web-Client** (z. B. Redirect nur `https://n8n.uppr.de/...`). Der QC nutzt einen **lokalen Desktop-Flow** (`http://localhost:…/`).

**Lösung:** Neuen OAuth-Client **Desktop app** anlegen, JSON ersetzen, ggf. altes `token.json` löschen, QC erneut starten.

### Konten

| Konto | Zweck | Token-Datei |
|-------|--------|-------------|
| `js@uppr.de` | Publisher-Entwürfe | `token_js.json` |
| `es@uppr.de` | Versand QC-Benachrichtigung | `token_es.json` |

Beim OAuth-Fenster das angezeigte Konto verwenden („Anderes Konto“ falls nötig). Welches Konto du autorisierst, bestimmt das jeweilige Postfach.

Deaktivieren ohne API: in `config/settings.py` → `gmail_enabled = False` (`.eml`/`.html` bleiben verfügbar).
