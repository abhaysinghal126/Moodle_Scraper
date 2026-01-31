import os
import requests
import re
import json
import argparse
import urllib.parse
import sys
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit
from tqdm import tqdm

# Force UTF-8 encoding for Windows terminals
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

BANNER = r"""
  __  __                 _ _         _____                                
 |  \/  |               | | |       / ____|                               
 | \  / | ___   ___   __| | | ___  | (___   ___ _ __ __ _ _ __   ___ _ __ 
 | |\/| |/ _ \ / _ \ / _` | |/ _ \  \___ \ / __| '__/ _` | '_ \ / _ \ '__|
 | |  | | (_) | (_) | (_| | |  __/  ____) | (__| | | (_| | |_) |  __/ |   
 |_|  |_|\___/ \___/ \__,_|_|\___| |_____/ \___|_|  \__,_| .__/ \___|_|   
                                                         | |              
                                                         |_|              
"""


class MoodleAPIClient:
    def __init__(self, cookie, course_url):
        self.session = requests.Session()
        self.base_netloc = urlsplit(course_url).netloc
        self.session.cookies.set("MoodleSession", cookie, domain=self.base_netloc)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json"
        })
        self.service_url = f"https://{self.base_netloc}/lib/ajax/service.php"

    def get_course_state(self, course_id, sesskey):
        params = {"sesskey": sesskey, "info": "core_courseformat_get_state"}
        payload = [{"index": 0, "methodname": "core_courseformat_get_state", "args": {"courseid": course_id}}]
        response = self.session.post(self.service_url, params=params, json=payload)
        return response.json()[0]['data']


class ResourceDownloader:
    def __init__(self, client, subject_path):
        self.client = client
        self.subject_path = subject_path
        self.index_file = os.path.join("Second Semester", "downloaded_index.json")
        self.history = self._load_history()

    def _load_history(self):
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_history(self):
        os.makedirs("Second Semester", exist_ok=True)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=4, ensure_ascii=False)

    def _clean_name(self, text):
        text = text.lower().strip()
        text = re.sub(r'\s+', '_', text)
        return re.sub(r'[\\/*?:"<>|]', "", text)

    def get_file(self, url, folder_name, display_name):
        if url in self.history:
            tqdm.write(f"      [EXIST] {display_name}")
            return self.history[url]
        try:
            target_url = url + ("&" if "?" in url else "?") + "redirect=1"
            resp = self.client.session.get(target_url, allow_redirects=True, stream=True)
            ctype = resp.headers.get('Content-Type', '').lower()
            if "text/html" in ctype: return None

            clean_display = re.sub(r'(File|URL|Folder)$', '', display_name).strip()
            base_name = self._clean_name(clean_display)

            ext_map = {
                "application/pdf": ".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/msword": ".doc",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.ms-excel": ".xls",
                "text/plain": ".txt",
                "application/zip": ".zip"
            }

            extension = ext_map.get(ctype, "")
            if any(base_name.endswith(e) for e in ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls', '.zip']):
                filename = base_name
            else:
                filename = base_name + extension

            rel_path = os.path.join(folder_name, filename)
            full_path = os.path.join(self.subject_path, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192): f.write(chunk)

            self.history[url] = rel_path
            self._save_history()
            tqdm.write(f"      [NEW] {filename}")
            return rel_path
        except Exception as e:
            tqdm.write(f"      [ERR] {display_name}: {e}")
            return None


class CourseManager:
    def __init__(self, course_url, cookie, subject_name):
        self.semester_dir = "Second Semester"
        self.clean_subject = re.sub(r'\s+', '_', subject_name.lower().strip())
        self.subject_dir = os.path.join(self.semester_dir, self.clean_subject)
        self.client = MoodleAPIClient(cookie, course_url)
        self.course_url = course_url
        self.downloader = ResourceDownloader(self.client, self.subject_dir)
        self.notes_dir = os.path.join(self.subject_dir, "class_notes")

    def run(self):
        print(BANNER)
        resp = self.client.session.get(self.course_url)
        if "Kirjaudu" in resp.text:
            print("SESSION EXPIRED: Update your cookie.")
            return

        sesskey = re.search(r'"sesskey":"([^"]+)"', resp.text).group(1)
        course_id = re.search(r'"courseId":(\d+)', resp.text).group(1)
        course_title = BeautifulSoup(resp.text, 'lxml').title.string.replace(' | TUNI Moodle', '')

        state = json.loads(self.client.get_course_state(course_id, sesskey))
        cm_map = {str(item['id']): item for item in state.get('cm', [])}

        os.makedirs(self.notes_dir, exist_ok=True)
        readme_content = [f"# {course_title}", f"\n[Moodle Link]({self.course_url})", f"[[class_notes/|Class Notes]]\n",
                          "---"]

        sections = state.get('section', [])
        for sec in tqdm(sections, desc="Scraping"):
            raw_name = sec.get('title') or sec.get('name') or "General"
            folder_name = self.downloader._clean_name(raw_name)
            folder_path = os.path.join(self.subject_dir, folder_name)

            is_new = not os.path.exists(folder_path)
            os.makedirs(folder_path, exist_ok=True)

            tqdm.write(f"\nSECTION: {raw_name}")
            section_updated = False

            with open(os.path.join(folder_path, "context.txt"), "w", encoding="utf-8") as cf:
                cf.write(f"SECTION: {raw_name}\n")
                if sec.get('summary'):
                    cf.write(f"\n[SUMMARY]\n{BeautifulSoup(sec['summary'], 'lxml').get_text(strip=True)}\n")

            readme_content.append(f"\n## {raw_name}")
            for cm_id in sec.get('cmlist', []):
                item = cm_map.get(str(cm_id))
                if not item: continue

                clean_title = BeautifulSoup(item['name'], 'lxml').get_text(strip=True)
                if item['module'] == 'resource':
                    if item['url'] not in self.downloader.history: section_updated = True
                    local_rel = self.downloader.get_file(item['url'], folder_name, item['name'])
                    if local_rel:
                        readme_content.append(f"* [[{local_rel.replace(os.sep, '/')}|{clean_title}]]")
                else:
                    readme_content.append(f"* [{clean_title}]({item.get('url', '#')})")

            if not section_updated and not is_new: tqdm.write("      Nothing new.")

        with open(os.path.join(self.subject_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(readme_content))
        print(f"\nDONE: {self.clean_subject}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url");
    parser.add_argument("cookie");
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    CourseManager(args.url, args.cookie, args.output).run()