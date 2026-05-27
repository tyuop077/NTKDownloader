# NTK Downloader

Novel scraper & EPUB bundler for [ntk01.com](https://ntk01.com/).

This application bypasses site protections to securely fetch raw chapters and compiles them into clean, standard-compliant EPUB files for offline reading.

## Features
* **Cloudflare & API Bypass:** Uses `curl_cffi` to impersonate Chrome and dynamically processes signed API payloads.
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
2. Follow the instructions in the console.

*Note: Completed EPUBs and intermediate JSON caches are saved to your system's `Downloads/ntk01epubs` directory.*

## Disclaimer
This tool is provided strictly for educational purposes and security research. The user assumes full responsibility for any actions taken using this software.
