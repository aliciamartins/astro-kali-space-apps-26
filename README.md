# Flame in Freefall by AstroKali

.  *  .  +  .  *  .  +  .

For the **"Flame in Freefall: AI-Powered Fire Safety Insights from Microgravity Combustion Data"** challenge, we built an interactive web dashboard and AI-powered Research Assistant backed by NASA's Physical Sciences Informatics (PSI) database.

Live deployment: https://aliciamartins.github.io/astro-kali-space-apps-26/

## Features

- **Core PSI Combustion Catalog (`index.html`)**: Displays all public NASA PSI Combustion Science investigations (`data/dashboard_data.json`), KPI metrics (investigations, indexed files, repository volume, file categories), multi-faceted filtering (text search, sub-research area, file category, hardware/facility, sort order, files-only toggle), and direct download links to `psi.nasa.gov`.
- **Interactive Topic Explorer & Investigation Research Dossier**: Clickable sub-research area and repository category chips, plus a full Research Dossier modal for each study containing scientific objectives, hypotheses, experimental approaches, research impacts, Principal Investigators & team contacts, peer-reviewed publications with DOIs, and a filterable file browser.
- **AI-Powered Research Chat Assistant**:
  - Interprets natural-language research questions into structured filter queries (accessions, keywords/synonyms, sub-research areas, hardware, investigators, file categories, file extensions, and sort criteria).
  - Executes scored filtered searches across all investigation metadata, publications, and repository files, and optionally auto-syncs results to the main dashboard table.
  - Synthesizes findings with inline clickable `PSI-XXX` citations, Dataset & Publication DOI links, direct `psi.nasa.gov` data file links, and CSV/JSON manifest exports.
  - Runs 100% client-side on static GitHub Pages using the built-in PSI Scientific Retrieval & Synthesis Engine, with an optional browser-stored **Gemini API Key (`gemini-2.5-flash` / `gemini-2.0-flash`)** option for generative synthesis grounded strictly on the retrieved PSI records.
- **NASA PSI API Snapshot Builder (`psi_api_client.py`, `build_data.py`)**: Fetches investigation metadata, enriched repository details (`contacts`, `publications`, `hypothesis`, `approach`, `researchImpacts`, `experimentHardware`, `totalFileSize`), and file summaries into `data/dashboard_data.json`.

## Running Locally

1. (Optional) Refresh or enrich the PSI snapshot:
   ```bash
   python build_data.py
   # Or enrich only existing studies in data/dashboard_data.json:
   python build_data.py --enrich-existing
   ```
2. Serve the repository over HTTP (required for `fetch('./data/dashboard_data.json')`):
   ```bash
   python -m http.server 8080
   ```
3. Open `http://localhost:8080` in your browser.

Per Aspera Ad Astra


                |
               / \
              |   |
              | O |
              |   |
             /|   |\
            / |___| \
              /|||\
             ( ||| )
           (  (   )  )
         (  (       )  )
        (_______________)
