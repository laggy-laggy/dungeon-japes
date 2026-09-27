# Open the current campaign

You need **Python 3.9+**. From this folder, run:

```bash
python -m http.server 8000 --directory dist
```

On Windows, `py -m http.server 8000 --directory dist` works if Python is installed with the launcher. Open <http://localhost:8000/>. The site runs on your computer and requires no internet connection while viewing. Stop the server with Ctrl+C.

## Import a different Roll20 campaign

Keep a separate copy of this entire folder for each campaign. You need **Python 3.9+** to import. **Node.js and npm** are only needed if you edit or rebuild the frontend.

1. Copy `campaign.example.json` to `campaign.json`. Set the subtitle and IANA timezone (for example `Europe/Brussels`). The campaign title is read from the Roll20 archive page's HTML title. You can set `"title": "Your preferred title"` in `campaign.json` to override it. Leave `players` empty for a first import.
2. Export the Roll20 chat pages to one JSON file or a folder of page JSON/HTML files using the same archive format. Keep that export safely as your backup.
3. In this project folder, run:

   ```bash
   python scripts/import_campaign.py "/path/to/your-export.json"
   ```

   On Windows use, for example, `py scripts\import_campaign.py "C:\Users\You\Downloads\roll20-chat-archive.json"`. You can optionally add `--expect-pages 108` when you know exactly how many numbered pages you expect.
4. The command writes `data/campaign.sqlite` (complete queryable archive), `public/report.json` (public aggregates for the frontend), and refreshes `dist/report.json` when a built site is present. It prints each Roll20 account ID with recorded aliases. Edit `campaign.json` to set preferred names and mark the GM and guests:

   ```json
   "players": {
     "-EXAMPLE_ACCOUNT_ID": {"name": "Preferred name", "role": "gm"},
     "-SECOND_ACCOUNT_ID": {"name": "Guest name", "role": "guest"}
   }
   ```

   Omitted accounts default to `"role": "player"`. The roles are `player`, `gm`, and `guest`. Account IDs are the reliable way to connect aliases and multiple characters to one person.
5. After editing the config, refresh the report without reparsing:

   ```bash
   python scripts/build_data.py data/campaign.sqlite --config campaign.json --output public/report.json
   ```

   This also refreshes `dist/report.json` if the site was built. Restart or refresh the browser. The first import works immediately with default names, but mark the GM and guests before presenting or comparing player awards.

For separate output paths, `import_campaign.py` accepts `--db`, `--report`, and `--config`. The frontend uses the default `public/report.json`; when serving the built site it uses `dist/report.json`.

### Inspect the SQLite database

Use [DB Browser for SQLite](https://sqlitebrowser.org/) or the built-in `sqlite3` module. Examples:

```bash
python -c "import sqlite3; db=sqlite3.connect('data/campaign.sqlite'); print(db.execute('SELECT COUNT(*) FROM numbered_die_details WHERE sides=20').fetchone())"
```

```sql
SELECT player_id, COUNT(*) AS raw_d20s, AVG(face) AS mean_face,
       SUM(face = 20) AS natural_20s, SUM(face = 1) AS natural_1s
FROM numbered_die_details
WHERE sides = 20
GROUP BY player_id
ORDER BY raw_d20s DESC;
```

To compare controllers of a single sheet, group by both IDs:

```sql
SELECT character_id, player_id, COUNT(*) AS raw_d20s, AVG(face) AS mean_face,
       SUM(face = 20) AS natural_20s, SUM(face = 1) AS natural_1s
FROM numbered_die_details
WHERE sides = 20 AND character_id IS NOT NULL
GROUP BY character_id, player_id;
```

The public report also stores these d20 totals under each character's `byController` field. Explore only lists sheets with at least 50 public d20 rolls across all controllers, then recalculates the chart and ledger using the selected controller's own rolls.

The SQLite tables are `source_pages`, `messages`, `rolls`, and `dice`. Views `roll_details`, `die_details`, `numbered_die_details`, and `text_chat` make queries easier. The raw database preserves page HTML and original message/roll JSON, including private message categories; **keep it private**. The site uses only the derived `report.json` with public aggregates, the recorded public moment examples, and their public source message IDs.

### Development

Install dependencies once and start the live development server:

```bash
npm install
npm run dev
```

After editing frontend code, run `npm run build` to replace `dist`. This project uses React, TypeScript, Vite, Motion, and Apache ECharts. `package-lock.json` locks the tested dependency versions. No online CDN is needed by the built app. The SQLite importer and report builder use the Python standard library.

### Publish on GitHub Pages

This repository includes `.github/workflows/pages.yml`. Create a **public** GitHub repository, extract the repository-ready ZIP, and commit its contents (including `.github` and `.gitignore`) to the root of its `main` branch. In **Settings → Pages**, choose **GitHub Actions** as the source. The workflow installs frontend dependencies, builds `dist/`, and publishes only that directory when you push to `main`. The site URL is `https://YOUR_USERNAME.github.io/YOUR_REPOSITORY/`; Vite uses relative asset paths for project subpaths. After regenerating `public/report.json`, review and commit the new report to update the site.

`campaign.json`, `data/`, exports, SQLite files, and `dist/` are ignored by Git. Keep your archive and database in the local project folder; scripts and `public/report.json` go into Git. A `.gitignore` rule does not remove a file that was committed previously, and it does not filter a ZIP uploaded through GitHub's web interface. Inspect the public `report.json` before sharing: it includes aggregate names, player and sheet IDs, and selected public moment examples.

## Data flow

`Roll20 export → scripts/roll20_parse.py → data/campaign.sqlite → scripts/build_data.py + campaign.json → public/report.json → Story and Explore`

The first command is wrapped by `scripts/import_campaign.py`. The browser reads only the generated JSON and does not need a live SQLite service.
