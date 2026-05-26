import os
import time
import json
import hmac
import hashlib
import base64
import re
import zipfile
import threading
import shlex
import webbrowser
import platform
import tkinter as tk
import gzip
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from urllib.parse import urlparse, unquote
from curl_cffi import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import sys
from Crypto.Cipher import AES
import subprocess

APP_VERSION = "1.4.1"

try:
    from build_env import GITHUB_REPO
except ImportError:
    GITHUB_REPO = None

# High DPI Awareness for Windows (Fixes blurriness)
if platform.system() == 'Windows':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# --- CONFIGURATION ---
DELAY_MS = 0
MAX_RETRIES = 5

def get_downloads_folder():
    if os.name == 'nt':
        import winreg
        try:
            return Path(winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"), "{374DE290-123F-4565-9164-39C4925E467B}")[0])
        except:
            return Path.home() / "Downloads"
    else:
        try:
            with open(os.path.expanduser('~/.config/user-dirs.dirs'), 'r') as f:
                for line in f:
                    if line.startswith('XDG_DOWNLOAD_DIR'):
                        val = line.split('=')[1].strip().strip('"')
                        val = os.path.expandvars(val)
                        return Path(os.path.expanduser(val))
        except Exception:
            pass
        return Path.home() / "Downloads"

BASE_DIR = get_downloads_folder() / "ntk01epubs"
CACHE_DIR = BASE_DIR / ".cache"
SESSION_FILE = CACHE_DIR / "session.json"

BASE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

def b64url_decode(data: str) -> bytes:
    padding = '=' * (4 - (len(data) % 4)) if len(data) % 4 != 0 else ''
    return base64.urlsafe_b64decode(data + padding)

def get_cookie_value(session, name):
    for cookie in session.cookies.jar:
        if cookie.name.lower() == name.lower():
            return cookie.value
    return None

def parse_curl_command(curl_command):
    headers = {}
    cookies = {}
    try:
        args = shlex.split(curl_command)
    except ValueError:
        return None, None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('-H', '--header'):
            header_str = args[i+1]
            if ':' in header_str:
                key, val = header_str.split(':', 1)
                key, val = key.strip(), val.strip()
                if key.lower() != 'accept-encoding':
                    headers[key] = val
            i += 2
        elif arg in ('-b', '--cookie'):
            cookie_str = args[i+1]
            for item in cookie_str.split(';'):
                if '=' in item:
                    k, v = item.strip().split('=', 1)
                    cookies[k] = v
            i += 2
        else:
            i += 1

    header_keys_lower = {k.lower(): k for k in headers.keys()}
    if 'cookie' in header_keys_lower:
        actual_cookie_key = header_keys_lower['cookie']
        cookie_str = headers.pop(actual_cookie_key)
        for item in cookie_str.split(';'):
            if '=' in item:
                k, v = item.strip().split('=', 1)
                cookies[k] = v

    return headers, cookies

def sanitize_html_for_epub(html_content):
    if not html_content: return ""
    html_content = re.sub(r'[ ]*(?:&nbsp;|\xa0)+[ ]*', ' ', html_content)
    html_content = html_content.replace("<p> ", "<p>").replace(" </p>", "</p>")
    return html_content

def clean_novelpia_content(json_list):
    content = ""
    for item in json_list:
        text = item.get('text', '')
        if not text: continue
        soup = BeautifulSoup(text, 'html.parser')
        for tag in soup(['script', 'style']): tag.decompose()
        for tag in soup.find_all(class_=["cover-wrapper", "cover-img", "cover-text"]): tag.decompose()
        for hidden in soup.find_all(style=re.compile(r'opacity:\s*0|display:\s*none')): hidden.decompose()

        img = soup.find('img')
        if img and 'src' in img.attrs:
            src = img['src']
            if src.startswith("//"): src = "https:" + src
            content += f'<img src="{src}" />'
        else:
            allowed_tags = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'br', 'sub', 'sup', 'ruby', 'rt', 'rp']
            for tag in soup.find_all(True):
                if tag.name not in allowed_tags:
                    tag.unwrap()
                else:
                    tag.attrs = {}
            clean_html = soup.decode_contents().strip()
            clean_html = clean_html.replace('\u200b', '')
            if clean_html:
                content += f"<p>{clean_html}</p>\n"
    return content

def build_epub(novel_id, file_title, meta_title, chapters):
    epub_path = BASE_DIR / f"{file_title} [{novel_id}].epub"
    with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)

        epub.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''')

        manifest_items, spine_items, nav_points = "", "", ""

        for i, ch in enumerate(chapters, 1):
            ch_id = f"chapter_{i}"
            file_name = f"{ch_id}.html"

            html_content = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{ch['title']}</title></head>
<body><h2>{ch['title']}</h2>"""

            if ch.get('type') == 'html':
                html_content += sanitize_html_for_epub(ch['text'])
            else:
                normalized_text = ch['text'].replace('\r\n', '\n')
                normalized_text = re.sub(r'\n{2,}', '\n', normalized_text)
                for p in normalized_text.split('\n'):
                    if p.strip():
                        html_content += f"<p>{p.strip()}</p>"

            html_content += "</body></html>"

            epub.writestr(f'OEBPS/{file_name}', html_content)
            manifest_items += f'<item id="{ch_id}" href="{file_name}" media-type="application/xhtml+xml"/>\n'
            spine_items += f'<itemref idref="{ch_id}"/>\n'
            nav_points += f'<navPoint id="navPoint-{i}" playOrder="{i}"><navLabel><text>{ch["title"]}</text></navLabel><content src="{file_name}"/></navPoint>\n'

        epub.writestr('OEBPS/content.opf', f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{meta_title}</dc:title><dc:language>ko</dc:language>
    <dc:identifier id="BookId">urn:uuid:ntk01-{novel_id}</dc:identifier>
  </metadata>
  <manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>{manifest_items}</manifest>
  <spine toc="ncx">{spine_items}</spine>
</package>''')

        epub.writestr('OEBPS/toc.ncx', f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:ntk01-{novel_id}"/><meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
  <docTitle><text>{meta_title}</text></docTitle>
  <navMap>{nav_points}</navMap>
</ncx>''')

    return epub_path

class NovelDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"NTK Novel Downloader v{APP_VERSION}")
        self.root.geometry("850x750")
        self.root.minsize(600, 500)

        self.is_downloading = False
        self.cancel_requested = False
        self.current_domain = None

        self.setup_ui()
        self.load_session()

        self.log(f"[*] Initialized. EPUBs will be saved to:\n    {BASE_DIR}")
        self.log("[*] Instructions:")
        self.log("    1. Paste novel URLs in the first box.")
        self.log("    2. Click 'Open [domain]/-' to open the browser page (404 error is intentional).")
        self.log("    3. Open DevTools (Three Dots Menu -> More Tools -> Developer Tools, or F12).")
        self.log("    4. Go to the Network tab and Refresh the page.")
        self.log("    5. Right-click the '-' request -> Copy -> Copy as cURL (bash or POSIX, not Windows).")
        self.log("    6. Paste the entire command into the cURL box and start.\n")
        self.log("    * Note: If DevTools opening is blocked, open a new blank tab, press F12, and navigate to the site manually.\n")

        threading.Thread(target=self.check_for_updates, daemon=True).start()

    def setup_ui(self):
        style = ttk.Style()
        if platform.system() == 'Windows':
            try:
                style.theme_use('vista')
            except tk.TclError:
                try:
                    style.theme_use('xpnative')
                except tk.TclError:
                    style.theme_use('clam')
        else:
            style.theme_use('clam')

        self.paned = tk.PanedWindow(self.root, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=6)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.top_frame = ttk.Frame(self.paned)
        self.paned.add(self.top_frame, minsize=280)

        input_frame = ttk.LabelFrame(self.top_frame, text=" Configuration ", padding="10")
        input_frame.pack(fill=tk.BOTH, expand=True)

        input_frame.columnconfigure(1, weight=1)
        input_frame.rowconfigure(0, weight=1)
        input_frame.rowconfigure(1, weight=1)

        ttk.Label(input_frame, text="Novel URLs:").grid(row=0, column=0, sticky=tk.NW, pady=2)
        self.urls_text = scrolledtext.ScrolledText(input_frame, height=4, width=70)
        self.urls_text.grid(row=0, column=1, sticky=tk.NSEW, pady=2, padx=5)

        ttk.Label(input_frame, text="Paste cURL here:").grid(row=1, column=0, sticky=tk.NW, pady=10)
        self.curl_text = scrolledtext.ScrolledText(input_frame, height=4, width=70)
        self.curl_text.grid(row=1, column=1, sticky=tk.NSEW, pady=10, padx=5)

        self.curl_text.bind("<Control-a>", self.select_all)
        self.curl_text.bind("<Command-a>", self.select_all)
        self.urls_text.bind("<Control-a>", self.select_all)
        self.urls_text.bind("<Command-a>", self.select_all)
        self.urls_text.bind("<KeyRelease>", self.update_domain_btn)

        options_frame = ttk.Frame(input_frame)
        options_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(5, 0))

        ttk.Label(options_frame, text="Parallel Requests:").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=1)
        self.workers_spin = ttk.Spinbox(options_frame, from_=1, to=5, textvariable=self.workers_var, width=5)
        self.workers_spin.pack(side=tk.LEFT, padx=5)

        btn_frame = ttk.Frame(options_frame)
        btn_frame.pack(side=tk.RIGHT)

        self.open_btn = ttk.Button(btn_frame, text="Open None/-", command=self.open_helper, state=tk.DISABLED)
        self.open_btn.pack(side=tk.LEFT, padx=5)

        self.clear_btn = ttk.Button(btn_frame, text="Clear Console", command=self.clear_console)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        self.download_btn = ttk.Button(btn_frame, text="Start Download", command=self.toggle_download)
        self.download_btn.pack(side=tk.LEFT)

        # --- UPDATE BAR ---
        self.update_frame = tk.Frame(self.root, bg='#ff8c00')
        self.update_label = tk.Label(self.update_frame, text="Update Available!", bg='#ff8c00', fg='black', font=('Arial', 9, 'bold'))
        self.update_label.pack(side=tk.LEFT, padx=10, pady=2)

        self.update_btn = ttk.Button(self.update_frame, text="Download & Restart", command=self.on_update_button_click)
        self.update_btn.pack(side=tk.RIGHT, padx=10, pady=2)

        self.update_info = {}

        self.bottom_frame = ttk.Frame(self.paned)
        self.paned.add(self.bottom_frame, minsize=200)

        progress_frame = ttk.Frame(self.bottom_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 5))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(progress_frame, textvariable=self.status_var).pack(anchor=tk.W)

        self.progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X, pady=2)

        console_frame = ttk.LabelFrame(self.bottom_frame, text=" Console Log ", padding="5")
        console_frame.pack(fill=tk.BOTH, expand=True)

        self.console = scrolledtext.ScrolledText(console_frame, state='disabled', bg='#1e1e1e', fg='#d4d4d4', font=('Consolas', 9))
        self.console.pack(fill=tk.BOTH, expand=True)

        self.console.bind("<Control-a>", self.select_all)
        self.console.bind("<Command-a>", self.select_all)

    def select_all(self, event):
        event.widget.tag_add(tk.SEL, "1.0", tk.END)
        event.widget.mark_set(tk.INSERT, "1.0")
        event.widget.see(tk.INSERT)
        return 'break'

    def update_domain_btn(self, event=None):
        urls = self.urls_text.get("1.0", tk.END).strip().split('\n')
        for url in urls:
            url = url.strip()
            if not url or url.isdigit(): continue
            if not url.startswith('http'):
                url_to_parse = 'https://' + url
            else:
                url_to_parse = url

            host = urlparse(url_to_parse).netloc
            if host and '.' in host:
                self.current_domain = host
                self.open_btn.config(state=tk.NORMAL, text=f"Open {host}/-")
                return

        self.current_domain = "ntk01.com"
        self.open_btn.config(state=tk.DISABLED, text="Open ntk01.com/-")

    def clear_console(self):
        self.console.config(state='normal')
        self.console.delete("1.0", tk.END)
        self.console.config(state='disabled')

    def open_helper(self):
        if self.current_domain:
            webbrowser.open(f"https://{self.current_domain}/-")

    def save_session(self, curl_cmd, raw_urls):
        try:
            with open(SESSION_FILE, 'w', encoding='utf-8') as f:
                json.dump({"curl": curl_cmd, "urls": raw_urls, "workers": self.workers_var.get()}, f)
        except Exception as e:
            self.log(f"[-] Failed to save session: {e}")

    def load_session(self):
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get("curl"): self.curl_text.insert(tk.END, data["curl"])
                    if data.get("urls"): self.urls_text.insert(tk.END, data["urls"])
                    if data.get("workers"): self.workers_var.set(data["workers"])
                self.log("[*] Previous session loaded successfully.")
                self.update_domain_btn()
            except Exception:
                pass

    def log(self, message):
        self.root.after(0, self._log_safe, message)

    def _log_safe(self, message):
        self.console.config(state='normal')
        self.console.insert(tk.END, str(message) + "\n")
        self.console.see(tk.END)
        self.console.config(state='disabled')

    def set_progress(self, current, total, text=None):
        self.root.after(0, self._set_progress_safe, current, total, text)

    def _set_progress_safe(self, current, total, text):
        self.progress['maximum'] = total
        self.progress['value'] = current
        if text:
            self.status_var.set(text)

    def fetch_novelpia_fallback(self, session, np_chapter_id):
        url = f"https://novelpia.com/proc/viewer_data/{np_chapter_id}"
        headers = {
            "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0"),
            "Referer": f"https://novelpia.com/viewer/{np_chapter_id}",
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }

        for attempt in range(2):
            try:
                res = session.post(url, headers=headers, timeout=10)
                text = res.text
                if "Authentication required" in text:
                    return None
                data = res.json()
                return clean_novelpia_content(data.get('s', []))
            except Exception as e:
                if attempt == 0:
                    time.sleep(2)
        return None

    def toggle_download(self):
        if self.is_downloading:
            self.cancel_requested = True
            self.download_btn.config(text="Canceling...", state=tk.DISABLED)
            self.log("\n[!] Cancel requested... Waiting for tasks to abort.")
            return

        curl_cmd = self.curl_text.get("1.0", tk.END).strip()
        raw_urls = self.urls_text.get("1.0", tk.END).strip()

        if not curl_cmd or "curl" not in curl_cmd[:10].lower():
            messagebox.showerror("Error", "Please paste a valid cURL command starting with 'curl'.\n\nYou can right-click the request in Developer Tools -> Copy -> Copy as cURL (bash or POSIX, not Windows).")
            return

        if not raw_urls:
            messagebox.showerror("Error", "Please enter at least one novel URL.")
            return

        self.update_domain_btn()
        domain = self.current_domain or "sbxh2.com"

        headers, cookies = parse_curl_command(curl_cmd)
        if not headers or not cookies:
            messagebox.showerror("Error", "Failed to parse headers/cookies from the cURL command.")
            return

        novel_ids = []
        for line in raw_urls.split('\n'):
            line = line.strip()
            if not line: continue
            match = re.search(r'novel/(\d+)', line)
            if match:
                novel_id = match.group(1)
                if novel_id not in novel_ids:
                    novel_ids.append(novel_id)
            elif line.isdigit():
                if line not in novel_ids:
                    novel_ids.append(line)

        if not novel_ids:
            messagebox.showerror("Error", "No valid novel IDs found in the URLs box.")
            return

        self.save_session(curl_cmd, raw_urls)

        self.is_downloading = True
        self.cancel_requested = False
        self.gui_notified = False
        self.download_btn.config(text="Cancel", state=tk.NORMAL)

        threading.Thread(target=self.download_worker, args=(novel_ids, headers, cookies, domain), daemon=True).start()

    def download_worker(self, novel_ids, headers, cookies, domain):
        total_new_chapters = 0

        try:
            self.log(f"\n[*] Parsed Headers and Cookies successfully. Target Domain: {domain}")

            for i, novel_id in enumerate(novel_ids):
                if self.cancel_requested:
                    break
                self.log(f"\n==========================================")
                self.log(f"[*] Processing Novel ID: {novel_id} ({i+1}/{len(novel_ids)})")
                scraped_count = self.process_novel(novel_id, headers, cookies, domain)
                total_new_chapters += scraped_count

            if not self.cancel_requested:
                self.log(f"\n[*] All queued jobs completed!")
            else:
                self.log(f"\n[-] Queue aborted by user.")

        except Exception as e:
            import traceback
            self.log(f"\n[!] FATAL THREAD ERROR: {str(e)}")
            self.log(traceback.format_exc())
        finally:
            self.root.after(0, self._finish_download_safe, total_new_chapters)

    def _finish_download_safe(self, total_new):
        self.set_progress(0, 100, "Idle")
        self.download_btn.config(text="Start Download", state=tk.NORMAL)
        self.is_downloading = False
        msg = "Finished processing novels." if not self.cancel_requested else "Download canceled by user."
        messagebox.showinfo("Status", f"{msg}\n\nTotal new chapters scraped: {total_new}")
        self.cancel_requested = False

    def process_novel(self, novel_id, parsed_headers, parsed_cookies, domain):
        def get_header(key):
            for k, v in parsed_headers.items():
                if k.lower() == key.lower():
                    return v
            return ""

        ua_exact = get_header("user-agent") or parsed_headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        # Base headers
        doc_headers = {
            "User-Agent": ua_exact,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": get_header("accept-language") or "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1"
        }

        api_headers = {
            "User-Agent": ua_exact,
            "Accept": "*/*",
            "Accept-Language": get_header("accept-language") or "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-novel-client": "shadow-v3",
            "Content-Type": "application/json"
        }

        # Dynamically inject sec-ch-ua headers provided in the curl command
        for k, v in parsed_headers.items():
            if k.lower().startswith("sec-ch-ua"):
                doc_headers[k] = v
                api_headers[k] = v

        # By explicitly filtering out the volatile cookies from initialization,
        # we bypass libcurl's domain-vs-host cookie shadowing problem completely.
        volatile_keys = {"nv", "ad_ack", "ntk_blk_ok_sig", "__ntk_ev_id"}
        clean_cookies = {k: v for k, v in parsed_cookies.items() if k.lower() not in volatile_keys}

        def create_clean_session():
            s = requests.Session(impersonate="chrome120")
            for k, v in clean_cookies.items():
                s.cookies.set(k, v, domain=domain)
            s.headers.update({"User-Agent": ua_exact})
            return s

        # Fetch Index Page
        index_session = create_clean_session()
        index_url = f"https://{domain}/novel/{novel_id}"
        self.log(f"[*] Fetching index page...")

        res = index_session.get(index_url, headers=doc_headers)

        if "error 1006" in res.text.lower():
            self.cancel_requested = True
            self.log(f"[-] FATAL: Cloudflare Error 1006 - Your IP is banned.")
            if not self.gui_notified:
                self.gui_notified = True
                self.root.after(0, lambda: messagebox.showerror(
                    "IP Banned",
                    "Cloudflare Error 1006: Access Denied.\n\nThe website owner has banned your IP address. You will need to use a VPN or Proxy to continue."
                ))
            return 0

        cf_keywords = ["cf-browser-verification", "Just a moment", "Ray ID"]
        if res.status_code == 404:
            self.log(f"[-] ERROR: Novel not found (HTTP 404).")
            return 0
        elif res.status_code in (403, 503) or any(k in res.text for k in cf_keywords):
            self.log(f"[-] ERROR: Cloudflare Blocked the index request. (HTTP {res.status_code})")
            return 0
        elif res.status_code != 200:
            self.log(f"[-] ERROR: Unexpected HTTP error fetching index. (HTTP {res.status_code})")
            return 0

        title_match = re.search(r'<meta property="og:title"\s+content="([^"]+)"', res.text)
        if not title_match:
            title_match = re.search(r'<title>([^<]+)</title>', res.text)

        if title_match:
            raw_title = title_match.group(1).split(' - ')[0].split(' | ')[0].strip()
        else:
            raw_title = f"Novel_{novel_id}"

        # Strip illegal characters for Windows filenames
        safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title).strip()
        self.log(f"[+] Title: {raw_title}")

        ep_blocks = re.findall(r'<li data-ep="(\d+)"[^>]*>.*?href="([^"]+)".*?<span class="ne-title">([^<]+)</span>', res.text)
        if not ep_blocks:
            self.log("[-] No chapters found on the index page.")
            return 0

        ep_blocks.reverse()
        total_chaps = len(ep_blocks)
        self.log(f"[+] Found {total_chaps} chapters.")

        cache_file = CACHE_DIR / f"{novel_id}.jsonl.gz"
        legacy_cache_file = CACHE_DIR / f"{novel_id}.json"
        cached_data = {}
        cache_version = 1

        if cache_file.exists():
            try:
                with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
                    lines = f.read().splitlines()
                    if lines:
                        try:
                            header = json.loads(lines[0])
                            cache_version = header.get("version", 1)
                        except:
                            pass

                        if len(lines) > 1:
                            for line in lines[1:]:
                                if line.strip():
                                    ch_data = json.loads(line)
                                    if "ep_id" in ch_data:
                                        cached_data[ch_data["ep_id"]] = ch_data
            except Exception as e:
                self.log(f"[-] Error reading .jsonl.gz cache: {e}")
        elif legacy_cache_file.exists():
            try:
                with open(legacy_cache_file, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
                for k, v in old_data.items():
                    if v.get('text') == '<p>Missing</p>':
                        continue
                    if v.get('type') == 'plain':
                        del v['type']
                    v['ep_id'] = k
                    cached_data[k] = v
                legacy_cache_file.unlink(missing_ok=True)
                cache_version = 0
            except Exception as e:
                self.log(f"[-] Error converting legacy cache: {e}")

        def save_cache():
            try:
                with gzip.open(cache_file, 'wt', encoding='utf-8') as f:
                    f.write(json.dumps({"title": raw_title, "version": 3}, ensure_ascii=False) + "\n")
                    for ep_num, href, title in ep_blocks:
                        eid = href.split('/')[-1]
                        if eid in cached_data:
                            ch = cached_data[eid].copy()
                            ch['ep_id'] = eid
                            if ch.get('type') == 'plain':
                                del ch['type']
                            f.write(json.dumps(ch, ensure_ascii=False) + "\n")
            except Exception as e:
                self.log(f"[-] Cache write error: {e}")

        needs_rewrite = False
        if cache_version < 3:
            for eid, ch in list(cached_data.items()):
                text_content = ch.get("text", "").strip()
                if text_content.startswith('{'):
                    try:
                        parsed = json.loads(text_content)
                        if isinstance(parsed, dict):
                            if parsed.get("kind") == "html" and "html" in parsed:
                                ch["text"] = parsed["html"]
                                ch["type"] = "html"
                            elif parsed.get("kind") == "text" and "text" in parsed:
                                ch["text"] = parsed["text"]
                                ch.pop("type", None)
                            elif "text" in parsed:
                                ch["text"] = parsed["text"]
                                ch.pop("type", None)

                            cached_data[eid] = ch
                            needs_rewrite = True
                    except:
                        pass

            if cache_version < 3:
                needs_rewrite = True

        if needs_rewrite:
            self.log(f"[*] Upgrading cache format to v3...")
            save_cache()

        cache_lock = threading.Lock()
        progress_lock = threading.Lock()
        thread_local = threading.local()

        completed_chapters = [0]
        new_downloads = [0]
        missing_eps = []

        try:
            max_workers = max(1, min(5, int(self.workers_var.get())))
        except:
            max_workers = 1

        def get_thread_session():
            if not hasattr(thread_local, "session"):
                s = create_clean_session()
                # Imitate exactly what the browser does on first load
                try:
                    s.get(f"https://{domain}/api/me", headers=api_headers, timeout=10)
                    ev_id = get_cookie_value(s, "__ntk_ev_id")
                    if not ev_id:
                        ev_id = os.urandom(32).hex()
                        s.cookies.set("__ntk_ev_id", ev_id, domain=domain)
                    sync_headers = api_headers.copy()
                    sync_headers["Referer"] = index_url
                    if "Content-Type" in sync_headers:
                        del sync_headers["Content-Type"]
                    s.post(f"https://{domain}/api/ev/sync", json={"evId": ev_id}, headers=sync_headers, timeout=10)
                except Exception:
                    pass
                thread_local.session = s
            return thread_local.session

        def process_chapter(ep_tuple, is_retry=False):
            if self.cancel_requested:
                return False

            idx, ep_num, ep_href, ep_title = ep_tuple
            ep_id = ep_href.split('/')[-1]
            chap_path = f"/novel/{novel_id}/{ep_id}"
            chapter_url = f"https://{domain}{chap_path}"

            with cache_lock:
                if ep_id in cached_data:
                    if not is_retry:
                        with progress_lock:
                            completed_chapters[0] += 1
                            self.set_progress(completed_chapters[0], total_chaps, f"Skipping: {ep_title}")
                    return True

            if not is_retry:
                with progress_lock:
                    self.set_progress(completed_chapters[0], total_chaps, f"Downloading: {ep_title}")
            else:
                self.log(f"[*] Retrying missing chapter: {ep_title}")

            session = get_thread_session()
            success = False
            force_nv_reissue = False

            for attempt in range(MAX_RETRIES + 1):
                if self.cancel_requested:
                    break

                if attempt > 0:
                    time.sleep(1)

                try:
                    # 1. Fetch HTML
                    chap_get_headers = doc_headers.copy()
                    chap_get_headers["Referer"] = index_url
                    cb = int(time.time() * 1000)
                    chap_res = session.get(f"{chapter_url}?cb={cb}", headers=chap_get_headers, timeout=15)

                    if "error 1006" in chap_res.text.lower():
                        self.cancel_requested = True
                        self.log(f"[-] FATAL: Cloudflare Error 1006 (IP Banned) on {ep_title}.")
                        with progress_lock:
                            if not self.gui_notified:
                                self.gui_notified = True
                                self.root.after(0, lambda: messagebox.showerror(
                                    "IP Banned",
                                    "Cloudflare Error 1006: Access Denied.\n\nThe website owner has banned your IP address. You will need to use a VPN or Proxy to continue."
                                ))
                        break

                    if "Just a moment" in chap_res.text or "cf-browser-verification" in chap_res.text:
                        self.log(f"[-] Cloudflare block detected on HTML fetch for {ep_title}.")
                        continue

                    if chap_res.status_code == 404 or "본문이 아직 준비되지 않았습니다" in chap_res.text:
                        np_match = re.search(r'href="https://novelpia\.com/viewer/(\d+)"', chap_res.text)
                        if np_match:
                            np_id = np_match.group(1)
                            np_html = self.fetch_novelpia_fallback(session, np_id)
                            if np_html:
                                with cache_lock:
                                    cached_data[ep_id] = {"title": ep_title, "text": np_html, "type": "html"}
                                    save_cache()
                                self.log(f"[+] Downloaded (Fallback): {ep_title}")
                                success = True
                                if not is_retry:
                                    with progress_lock:
                                        new_downloads[0] += 1
                                break

                        if not is_retry:
                            with cache_lock:
                                missing_eps.append(ep_tuple)
                            self.log(f"[-] 'Not ready' (HTTP {chap_res.status_code}). Queued for retry: {ep_title}")
                        else:
                            self.log(f"[-] Still 'Not ready' on retry: {ep_title}")
                        success = True
                        break

                    if chap_res.status_code != 200:
                        self.log(f"[-] Server error on HTML fetch for {ep_title} (HTTP {chap_res.status_code}).")
                        continue

                    tokens = re.findall(r'(?:\\"|")token(?:\\"|")\s*:\s*(?:\\"|")([A-Za-z0-9_=-]+(?:\.[A-Za-z0-9_=-]+)+)(?:\\"|")', chap_res.text)
                    token = next((t for t in tokens if t.startswith("eyJuIjoi") or t.startswith("eyJlIjoi")), None)

                    if not token:
                        self.log(f"[-] Novel token missing in HTML for {ep_title}")
                        continue

                    req_headers = api_headers.copy()
                    req_headers["Referer"] = chapter_url

                    # 2. Extract Ad Challenge directly from HTML to bypass tracking impressions
                    chal_token = next((t for t in tokens if t.startswith("eyJ2Ijoy")), None)
                    slot_nonces = []
                    slot_match = re.search(r'(?:\\"|")slotNonces(?:\\"|")\s*:\s*\[(.*?)\]', chap_res.text)
                    if slot_match:
                        slot_nonces = re.findall(r'(?:\\"|")([^"\\]+)(?:\\"|")', slot_match.group(1))

                    chal_data = None
                    if chal_token and slot_nonces:
                        chal_data = {"token": chal_token, "slotNonces": slot_nonces}
                    else:
                        # Fallback to API if the HTML structure changes
                        chal_res = session.post(f"https://{domain}/api/ad/challenge", json={"path": chap_path}, headers=req_headers, timeout=10)
                        if chal_res.status_code == 200:
                            chal_data = chal_res.json().get("challenge", {})

                    if chal_data and "token" in chal_data:
                        # 3. Acknowledge the ad challenge immediately
                        ack_res = session.post(
                            f"https://{domain}/api/ad/ack",
                            json={
                                "challengeToken": chal_data.get("token"),
                                "total": 24,
                                "visible": 24,
                                "path": chap_path,
                                "slotNonces": chal_data.get("slotNonces", [])
                            },
                            headers=req_headers,
                            timeout=10
                        )
                        if ack_res.status_code == 200:
                            ack_json = ack_res.json()
                            if not ack_json.get("ok"):
                                err_msg = ack_json.get("error")
                                if err_msg == "blocked":
                                    self.cancel_requested = True
                                    self.log(f"[-] Block detected on Ad Ack. Halting queue.")
                                    with progress_lock:
                                        if not self.gui_notified:
                                            self.gui_notified = True
                                            self.root.after(0, lambda: messagebox.showerror(
                                                "Access Blocked",
                                                "Access blocked by the server.\n\nThis is usually a cookie/localstorage anomaly, not an IP block.\n\nPlease clear your browser cookies and localstorage for the site, or open it in a fresh Incognito window, and grab a new cURL command."
                                            ))
                                    break
                                self.log(f"[-] Ad Ack failed: {ack_res.text}")
                        else:
                            self.log(f"[-] Ad Ack HTTP {ack_res.status_code}: {ack_res.text}")

                    # 6. Check NV Issue
                    nv_cookie = get_cookie_value(session, "nv")
                    if not nv_cookie or force_nv_reissue:
                        issue_headers = req_headers.copy()
                        if "Content-Type" in issue_headers:
                            del issue_headers["Content-Type"]

                        session.post(f"https://{domain}/api/nv-issue", headers=issue_headers, timeout=10)

                        nv_cookie = get_cookie_value(session, "nv")
                        if nv_cookie:
                            force_nv_reissue = False
                            self.log(f"[+] Re-issued fresh NV token successfully.")
                        else:
                            self.log(f"[-] Missing 'nv' cookie after issue request.")
                            continue

                    # 7. Request Novel Content
                    nv_cookie_decoded = unquote(nv_cookie)
                    nonce_bytes = os.urandom(24)
                    nonce_str = b64url_encode(nonce_bytes)
                    message = f"{token}.{nonce_str}.{ua_exact}".encode('utf-8')
                    proof_bytes = hmac.new(nv_cookie_decoded.encode('utf-8'), message, hashlib.sha256).digest()
                    proof_str = b64url_encode(proof_bytes)

                    payload_data = {
                        "novelId": str(novel_id), "episodeId": str(ep_id), "token": token,
                        "nonce": nonce_str, "proof": proof_str
                    }

                    content_res = session.post(f"https://{domain}/api/novel-content", json=payload_data, headers=req_headers, timeout=15)
                    try:
                        resp_json = content_res.json()
                    except ValueError as e:
                        self.log(f"[-] ValueError parsing novel-content JSON: {e}")
                        continue

                    if not resp_json.get("ok"):
                        err_msg = resp_json.get("error")

                        if err_msg == "expired":
                            self.log(f"[*] NV token expired for {ep_title}. Forcing reissue...")
                            # Since we stripped the domain cookie at initialization,
                            # we can just flag it and hit /api/nv-issue on the next loop
                            # and curl_cffi will natively overwrite it.
                            force_nv_reissue = True
                            continue

                        elif err_msg == "ad_ack_required":
                            self.log(f"[*] Server explicitly requested ad verification again...")
                            continue

                        elif err_msg == "blocked":
                            self.cancel_requested = True
                            self.log(f"[-] Block detected on Novel Content. Halting queue.")
                            with progress_lock:
                                if not self.gui_notified:
                                    self.gui_notified = True
                                    self.root.after(0, lambda: messagebox.showerror(
                                        "Access Blocked",
                                        "Access blocked by the server.\n\nThis is usually a cookie/localstorage anomaly, not an IP block.\n\nPlease clear your browser cookies and localstorage for the site, or open it in a fresh Incognito window, and grab a new cURL command."
                                    ))
                            break

                        else:
                            self.log(f"[-] Failed response for {novel_id}/{ep_id}: {resp_json}")
                            continue

                    encrypted_payload = resp_json["payload"]
                    cookie_base_str = nv_cookie_decoded.split('.')[0]
                    cookie_bytes = b64url_decode(cookie_base_str)
                    payload_bytes = b64url_decode(encrypted_payload)

                    iv = payload_bytes[:12]
                    ciphertext_with_tag = payload_bytes[12:]
                    ciphertext = ciphertext_with_tag[:-16]
                    tag = ciphertext_with_tag[-16:]

                    key_suffix = f":{novel_id}:{ep_id}:v3".encode('utf-8')
                    aes_key = hashlib.sha256(cookie_bytes + key_suffix).digest()

                    try:
                        cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv)
                        decrypted_bytes = cipher.decrypt_and_verify(ciphertext, tag)
                        plaintext = decrypted_bytes.decode('utf-8')
                    except ValueError:
                        self.log(f"[-] Decryption failed for {ep_title} (Invalid Tag/Corrupt)")
                        continue

                    ch_type = None

                    if plaintext.startswith('{'):
                        try:
                            parsed_data = json.loads(plaintext)
                            if isinstance(parsed_data, dict):
                                kind = parsed_data.get("kind")
                                if kind == "html" and "html" in parsed_data:
                                    plaintext = parsed_data["html"]
                                    ch_type = "html"
                                elif kind == "text-shuffled" and "paragraphs" in parsed_data and "perm" in parsed_data:
                                    paragraphs = parsed_data["paragraphs"]
                                    perm = parsed_data["perm"]
                                    unshuffled = [""] * len(paragraphs)
                                    for p_idx, p_text in enumerate(paragraphs):
                                        if p_idx < len(perm):
                                            dest_idx = int(perm[p_idx])
                                            if 0 <= dest_idx < len(unshuffled):
                                                unshuffled[dest_idx] = p_text
                                    plaintext = "\n\n".join([p for p in unshuffled if p])
                                elif kind == "text" and "paragraphs" in parsed_data:
                                    plaintext = "\n\n".join(parsed_data["paragraphs"])
                                elif kind == "text" and "text" in parsed_data:
                                    plaintext = parsed_data["text"]
                                elif "text" in parsed_data:
                                    plaintext = parsed_data["text"]
                        except ValueError:
                            pass

                    with cache_lock:
                        cache_entry = {"title": ep_title, "text": plaintext}
                        if ch_type == "html":
                            cache_entry["type"] = "html"
                        cached_data[ep_id] = cache_entry
                        save_cache()

                    success = True
                    self.log(f"[+] Downloaded {novel_id}/{ep_id}: {ep_title}")
                    if not is_retry:
                        with progress_lock:
                            new_downloads[0] += 1
                    break

                except Exception as e:
                    self.log(f"[-] Attempt failed for {ep_title}: {e}")

            if not success and not self.cancel_requested:
                self.log(f"[!] FATAL: Failed to download '{ep_title}' after retries. Halting novel.")
                self.cancel_requested = True

            if success and not is_retry:
                with progress_lock:
                    completed_chapters[0] += 1
                    self.set_progress(completed_chapters[0], total_chaps)

            return success

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_chapter, (idx, ep_num, ep_href, ep_title), False) for idx, (ep_num, ep_href, ep_title) in enumerate(ep_blocks)]
            for future in futures:
                future.result()

        if missing_eps and not self.cancel_requested:
            self.log(f"\n[*] Retrying {len(missing_eps)} missing chapters...")
            for ep_tuple in missing_eps:
                if self.cancel_requested:
                    break
                process_chapter(ep_tuple, is_retry=True)

        ordered_chapters = []
        for ep_num, ep_href, ep_title in ep_blocks:
            eid = ep_href.split('/')[-1]
            if eid in cached_data:
                ordered_chapters.append(cached_data[eid])
            else:
                ordered_chapters.append({"title": ep_title, "text": "<p>Missing</p>", "type": "html", "missing": True})

        if ordered_chapters:
            if self.cancel_requested:
                self.log(f"[*] Building partial EPUB with {len(ordered_chapters)} chapters...")
            else:
                self.log(f"[*] Building EPUB with {len(ordered_chapters)} total chapters ({new_downloads[0]} new)...")

            out_path = build_epub(novel_id, safe_title, raw_title, ordered_chapters)
            self.log(f"[+] SUCCESS: Saved EPUB to:\n    {out_path}")
        else:
            self.log("[-] No valid chapters found to build EPUB.")

        return new_downloads[0]

    def check_for_updates(self):
        if not getattr(sys, 'frozen', False):
            self.log("[*] Updater: Running from script. Update checking disabled.")
            return
        if not GITHUB_REPO:
            self.log("[*] Updater: No repository defined in build. Update checking disabled.")
            return

        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            res = requests.get(url, impersonate="chrome120", timeout=10)

            if res.status_code == 200:
                data = res.json()
                latest_tag = data.get("tag_name", "")
                release_url = data.get("html_url", "")

                if latest_tag and latest_tag != APP_VERSION:
                    for asset in data.get("assets", []):
                        asset_name = asset.get("name", "")
                        if asset_name.endswith(".exe"):
                            self.update_info = {
                                "exe_url": asset.get("browser_download_url"),
                                "asset_name": asset_name,
                                "release_url": release_url
                            }
                            self.root.after(0, self.show_update_bar, latest_tag)
                            break
            else:
                self.log(f"[-] Updater: API returned HTTP {res.status_code}")
        except Exception as e:
            self.log(f"[-] Updater: Error checking for updates ({e})")

    def show_update_bar(self, new_version):
        self.update_label.config(text=f"New version {new_version} available! (Current: {APP_VERSION})")
        self.update_frame.pack(side=tk.BOTTOM, fill=tk.X, before=self.paned)

    def on_update_button_click(self):
        # Start update on "Download & Restart" button click
        self.update_btn.config(state=tk.DISABLED, text="Downloading...")
        threading.Thread(target=self._manual_download_and_relaunch, daemon=True).start()

    def _manual_download_and_relaunch(self):
        # Manual download requested by the user and restart
        exe_url = self.update_info.get("exe_url")
        asset_name = self.update_info.get("asset_name")
        release_url = self.update_info.get("release_url")

        if not exe_url:
            webbrowser.open(release_url)
            self.root.after(0, lambda: self.update_btn.config(text="Opened Browser"))
            return

        try:
            self.log(f"\n[*] Initiated update. Downloading: {asset_name}...")
            res = requests.get(exe_url, impersonate="chrome120", timeout=30)
            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}")

            current_exe = sys.executable
            target_dir = os.path.dirname(current_exe)
            target_path = os.path.join(target_dir, asset_name)

            # If the downloaded file name matches our currently running locked executable exactly
            if os.path.abspath(target_path) == os.path.abspath(current_exe):
                name, ext = os.path.splitext(asset_name)
                target_path = os.path.join(target_dir, f"{name} (1){ext}")

            # Attempt to write the file. If it fails (e.g. they are running "(1)" and downloading "(1)"),
            # catch the permission error and fallback to a timestamped name.
            try:
                with open(target_path, "wb") as f:
                    f.write(res.content)
            except PermissionError:
                name, ext = os.path.splitext(asset_name)
                target_path = os.path.join(target_dir, f"{name}_{int(time.time())}{ext}")
                with open(target_path, "wb") as f:
                    f.write(res.content)

            self.log(f"[+] Downloaded successfully to: {os.path.basename(target_path)}")
            self.log("[*] Launching updated version and closing current instance...")

            # Launch the new exe and immediately kill this process
            subprocess.Popen([target_path])
            os._exit(0)

        except Exception as e:
            self.log(f"[-] Download failed ({e}). Opening release page instead.")
            webbrowser.open(release_url)
            self.root.after(0, lambda: self.update_btn.config(state=tk.NORMAL, text="Update Failed"))


if __name__ == "__main__":
    root = tk.Tk()
    app = NovelDownloaderApp(root)
    root.mainloop()
