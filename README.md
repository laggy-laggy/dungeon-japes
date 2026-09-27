# Roll20 Wrapped

A locally hosted anniversary presentation plus a data explorer. The bundled example is **Dungeon Japes**. This repository includes the public report; import a Roll20 export locally to generate the full SQLite database.

Artwork attribution is in [CREDITS.md](CREDITS.md).

## Open the current campaign

You need **Python 3.9+**. From this folder, run:

```bash
python -m http.server 8000 --directory dist
```

On Windows, `py -m http.server 8000 --directory dist` works if Python is installed with the launcher. Open <http://localhost:8000/>. The site runs on your computer and requires no internet connection while viewing. Stop the server with Ctrl+C.

Use the **Story / Explore** switch at the top left. In Story mode, use the onscreen Next button, Right Arrow, or Space to advance. Four title slides divide the story into The adventure so far, Moments, The rolls, and Awards. The busiest day and the other Moments reveal their result on the next advance; the worst roll has an honorable mention before the final reveal. The rolls chapter shows dice sizes, the campaign-wide d20 face distribution, skills, and saving throws. Each award has a podium reveal (third, second, first) followed by the full ranking chart. The Dice Goblin ranks Roll20 players by all individual public dice, including dice without a sheet ID. The Main Character and the final natural 20/1 character comparison use confirmed sheet-linked d20s, including stand-in controller rolls. Explore retains per-die and per-player filters; character dice volume is not ranked because many dice lack sheet IDs. The GM appears in a contrasting color in applicable player charts but never on a podium. The left controls can jump between chapters. Fullscreen collapses the sidebar automatically; you can also collapse it manually.

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

## Statistical rules in this edition

- A **total public roll** is one distinct public Roll20 message with a die result; it is not necessarily one command entered. A **numbered die** is one raw face. The raw database has additional categories and random-table/Fate results that are not ordinary die faces.
- The presentation includes public `general` and `rollresult` messages, excludes whisper, GM-only, secret, and hidden messages, and shows GM and guests separately when configured.
- Natural 20 and natural 1 rankings use highest percentages of raw d20 faces. Both dice in advantage/disadvantage count. The explorer shows counts, sample sizes, and 95% confidence intervals (Wilson method). The mean uses raw faces before modifiers; it already accounts for the number of rolls, but the uncertainty changes with sample size.
- Skill usage counts one public standard skill check per sheet-linked message; it excludes saves, initiative, tool checks, typed commands, GM, and guests. In this campaign there are 1,144 such checks. Saving throw counts use the same sheet-linked public roll rule for the six ability saves (833 total). The three death saves are kept separate.
- Character results group by **sheet ID**, not alias. Player totals include every public die, while character totals include only dice with confirmed sheet IDs. For this campaign, 205 of Arti’s 208 d10 results lack a sheet ID: 147 are damage-template dice under a Gil-related chat name and 58 are standalone dice. Many template damage rolls immediately follow a sheet-linked attack, but chat adjacency alone is insufficient proof to assign them automatically. The unassigned dice remain in SQLite and in the generated per-player aggregates for future analysis. Character average/rate presentation slides and the Explore character view require at least 50 raw d20 faces across all controllers; Explore can then show any selected controller's own sample on those sheets.
- Monthly calmest/busiest labels ignore the first and last partial months. Dates use the configured timezone. An activity date is not a verified session boundary.
- Chat-post classification is heuristic and can include pasted reference material. Damage expressions may be generated without being applied to a target. Rankings are descriptive, not evidence that anyone's dice are biased.
- “Moments” derives a party-wide streak of consecutive single-d20 public roll posts, local-date d20 totals, and verified double-extreme advantage/disadvantage rolls. A different campaign only shows moment slides supported by its own data. Here, 1 December 2024 is featured for nat 1s because three dates tie at eight, and it has the highest rate among them (8 of 93). A date is only a session proxy.

## Data flow

`Roll20 export → scripts/roll20_parse.py → data/campaign.sqlite → scripts/build_data.py + campaign.json → public/report.json → Story and Explore`

The first command is wrapped by `scripts/import_campaign.py`. The browser reads only the generated JSON and does not need a live SQLite service.
