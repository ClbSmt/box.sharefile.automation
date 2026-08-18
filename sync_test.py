"""Throwaway harness to validate Box + ShareFile API calls before rebuilding them in Power Automate."""
import json
import os
import sys
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

ENV_VARS = [
    "BOX_CLIENT_ID", "BOX_CLIENT_SECRET", "BOX_USER_ID", "SF_SUBDOMAIN",
    "SF_CLIENT_ID", "SF_CLIENT_SECRET", "BOX_TEST_FOLDER_ID", "SF_TEST_FOLDER_ID",
]
SF_REDIRECT_URI = "https://secure.sharefile.com/oauth/oauthcomplete.aspx"
for _name in ENV_VARS:
    globals()[_name] = os.environ.get(_name)
SECRET_KEYS, TOKEN_KEYS = {"client_secret", "password"}, {"access_token", "authorization"}

def redact(obj):
    if isinstance(obj, dict):
        return {k: ("***REDACTED***" if k.lower() in SECRET_KEYS
                     else v[:10] + "...REDACTED" if k.lower() in TOKEN_KEYS and isinstance(v, str)
                     else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj

def auth(token):
    return {"Authorization": f"Bearer {token}"}

def call(method, url, label, data=None, headers=None, files=None):
    print(f"\n--- {label} ---\n{method} {url}")
    if data:
        print(f"request body: {json.dumps(redact(data))}")
    resp = requests.request(method, url, data=data, headers=headers, files=files)
    print(f"status: {resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(redact(body), indent=2))
    except ValueError:
        body = None
        print(f"(non-JSON body, {len(resp.content)} bytes)")
    if not resp.ok:
        sys.exit(f"[{label}] FAILED with status {resp.status_code} - stopping.")
    return body

def get_box_token():
    # Impersonates the Box user BOX_USER_ID (box_subject_type=user) instead of the
    # app's sandboxed service account, so the token carries that user's own folder
    # access. Requires "Generate User Access Tokens" enabled for this app in the
    # Box Developer Console.
    data = {
        "grant_type": "client_credentials", "client_id": BOX_CLIENT_ID,
        "client_secret": BOX_CLIENT_SECRET, "box_subject_type": "user",
        "box_subject_id": BOX_USER_ID,
    }
    j = call("POST", "https://api.box.com/oauth2/token", "get_box_token", data=data)
    token = j.get("access_token", "")
    print(f"access_token: {token[:10]}...  expires_in: {j.get('expires_in')}")
    return token

def whoami_box(token):
    j = call("GET", "https://api.box.com/2.0/users/me", "whoami_box", headers=auth(token))
    print(f"login: {j.get('login')}")
    return j.get("login")

def list_box_folder(token, folder_id):
    url = f"https://api.box.com/2.0/folders/{folder_id}/items"
    entries = call("GET", url, "list_box_folder", headers=auth(token)).get("entries", [])
    print(f"item count: {len(entries)}")
    for e in entries:
        print(f"  - {e.get('name')}")
    return entries

def upload_box_file(token, folder_id, local_path="sample.txt"):
    # Box direct upload only works up to 50MB; larger files need the chunked
    # upload API, which this script does not implement.
    if not os.path.exists(local_path):
        with open(local_path, "w") as f:
            f.write("Sample file for Box upload validation.\n")
    attributes = json.dumps({"name": os.path.basename(local_path), "parent": {"id": folder_id}})
    with open(local_path, "rb") as f:
        files = {"attributes": (None, attributes, "application/json"), "file": (os.path.basename(local_path), f)}
        j = call("POST", "https://upload.box.com/api/2.0/files/content", "upload_box_file",
                 headers=auth(token), files=files)
    file_id = j["entries"][0]["id"]
    print(f"uploaded file id: {file_id}")
    return file_id

def get_sf_token():
    # Authorization Code flow, not password grant: ShareFile's password grant can't
    # satisfy an MFA challenge, so accounts with MFA must sign in interactively.
    authorize_url = (
        f"https://{SF_SUBDOMAIN}.sharefile.com/oauth/authorize?response_type=code"
        f"&client_id={SF_CLIENT_ID}&redirect_uri={quote(SF_REDIRECT_URI, safe='')}"
    )
    print(f"\n--- get_sf_token (Authorization Code flow) ---")
    print(f"1. Open this URL in a browser and log in (complete MFA if prompted):\n{authorize_url}")
    print("2. ShareFile will redirect to its oauthcomplete.aspx page showing a code.")
    pasted = input("3. Paste that page's URL (or just the 'code' value) here: ").strip()
    code = pasted.split("code=")[1].split("&")[0] if "code=" in pasted else pasted
    data = {
        "grant_type": "authorization_code", "client_id": SF_CLIENT_ID,
        "client_secret": SF_CLIENT_SECRET, "code": code, "requirev3": "true",
    }
    j = call("POST", f"https://{SF_SUBDOMAIN}.sharefile.com/oauth/token", "get_sf_token", data=data)
    token = j.get("access_token", "")
    print(f"access_token: {token[:10]}...  subdomain: {j.get('subdomain')}  appcp: {j.get('appcp')}")
    return token

def list_sf_folder(token, folder_id):
    url = f"https://{SF_SUBDOMAIN}.sf-api.com/sf/v3/Items({folder_id})/Children"
    items = call("GET", url, "list_sf_folder", headers=auth(token)).get("value", [])
    print(f"item count: {len(items)}")
    for item in items:
        print(f"  - {item.get('Name')}")
        for k in item:
            if "creat" in k.lower():
                print(f"      {k} = {item[k]}")
    return items

def download_sf_file(token, item_id):
    url = f"https://{SF_SUBDOMAIN}.sf-api.com/sf/v3/Items({item_id})/Download"
    resp = requests.get(url, headers=auth(token), allow_redirects=False)
    print(f"\n--- download_sf_file ---\nGET {url}\nstatus: {resp.status_code}")
    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code in (301, 302, 303, 307, 308):
        print(f"RESULT: redirect - a second GET is required.\nLocation: {resp.headers.get('Location')}")
    elif resp.ok and "json" in ctype.lower():
        print(f"RESULT: JSON body returned (not binary):\n{json.dumps(redact(resp.json()), indent=2)}")
    elif resp.ok:
        print(f"RESULT: binary file content returned directly. Content-Type={ctype}, bytes={len(resp.content)}")
    else:
        sys.exit(f"[download_sf_file] FAILED with status {resp.status_code} - stopping.")

def ask(prompt, default=None):
    v = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ")
    return v or default

STEPS = {
    "1": lambda s: s.update(box_token=get_box_token()),
    "2": lambda s: whoami_box(s.get("box_token") or ask("Box token")),
    "3": lambda s: list_box_folder(s.get("box_token") or ask("Box token"), ask("Box folder id", BOX_TEST_FOLDER_ID)),
    "4": lambda s: upload_box_file(s.get("box_token") or ask("Box token"), ask("Box folder id", BOX_TEST_FOLDER_ID)),
    "5": lambda s: s.update(sf_token=get_sf_token()),
    "6": lambda s: list_sf_folder(s.get("sf_token") or ask("SF token"), ask("SF folder id", SF_TEST_FOLDER_ID)),
    "7": lambda s: download_sf_file(s.get("sf_token") or ask("SF token"), ask("SF item id to download")),
}

def main():
    state = {}
    menu = ("\n1) get_box_token\n2) whoami_box\n3) list_box_folder\n4) upload_box_file\n"
            "5) get_sf_token\n6) list_sf_folder\n7) download_sf_file\nq) quit\n")
    while True:
        choice = input(menu + "Select step: ").strip().lower()
        if choice == "q":
            break
        (STEPS.get(choice) or (lambda s: print("Invalid choice.")))(state)

if __name__ == "__main__":
    main()
