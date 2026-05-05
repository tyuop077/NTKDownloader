# NTK Downloader

Novel scraper & EPUB bundler for [ntk01.com](https://ntk01.com/).

This application bypasses site protections to securely fetch raw chapters and compiles them into clean, standard-compliant EPUB files for offline reading.

## Features
* **Cloudflare & API Bypass:** Uses `curl_cffi` to impersonate Chrome and dynamically processes HMAC-signed API payloads.
* **XOR Decryption:** Reconstructs token/proof payloads to fetch and decrypt XOR-obfuscated chapter content.
* **Automated EPUB Generation:** Cleans HTML, structures text, strips trackers, and builds standard-compliant EPUB files.
* **Novelpia Fallback:** Attempts to scrape Novelpia directly if specific chapters are marked as "Not Ready" on the frontend.
* **Session Caching:** Saves your headers/cookies and caches downloaded chapters to prevent re-downloading during aborted runs or new updates.

## Installation & Running from Source

**Prerequisites:** Python 3.8+

1. Clone or download the repository.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Launch the application:
   ```bash
   python app.py
   ```
2. Click the **"🌐 Open ntk01.com/-"** button in the interface.
3. Once the site opens, press `F12` to open Developer Tools and navigate to the **Network** tab.
4. Refresh the page. Right-click the root document request (named `-`).
5. Select **Copy -> Copy as cURL (bash)**.
6. Paste the *entire* copied cURL command into the top text box in the application. (The app will automatically parse your cookies and user-agent from this command).
7. Enter one or more Novel URLs (e.g., `https://ntk01.com/novel/12345`) or simply the novel IDs into the bottom text box, each on a new line.
8. Adjust the concurrent worker limit (1-5) and click **Start Download**.

*Note: Completed EPUBs and intermediate JSON caches are saved to your system's `Downloads/ntk01epubs` directory.*

## Disclaimer
This tool is provided strictly for educational purposes and security research. The user assumes full responsibility for any actions taken using this software.
