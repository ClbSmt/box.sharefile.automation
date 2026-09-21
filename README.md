# Box + ShareFile API Validation Harness

Throwaway Python CLI used to validate the OAuth flows and core API calls needed
to connect to **Box** and **ShareFile** independently, before rebuilding the
same calls as HTTP actions in Power Automate. There is no automated test
suite here — `sync_test.py` is an interactive menu you drive by hand.

## What this repo does NOT do

This does not sync or transfer files between Box and ShareFile. Each API is
exercised independently (get a token, list a folder, upload/download a file).
There is no code here that moves a file from one system to the other. If you
need that logic, you have to build it — this repo only proves out the auth
and the individual API calls it will depend on.

The original intent was to port these validated calls into Power Automate as
HTTP actions, not to run this script in production.

## Box: auth pattern

Box's `client_credentials` grant on its own returns a token for the app's
sandboxed **service account**, which has no access to real folders. To get a
token scoped to an actual user's folder access, request the token with user
impersonation:

```
POST https://api.box.com/oauth2/token
grant_type=client_credentials
client_id=<BOX_CLIENT_ID>
client_secret=<BOX_CLIENT_SECRET>
box_subject_type=user
box_subject_id=<BOX_USER_ID>
```

This requires **"Generate User Access Tokens"** to be enabled for the app in
the Box Developer Console. Without it, the impersonation request fails.

Once you have a token: `GET /2.0/users/me` (whoami), `GET /2.0/folders/{id}/items`
(list), `POST https://upload.box.com/api/2.0/files/content` (upload, direct
upload only works up to 50MB — larger files need Box's chunked upload API,
which is not implemented here).

## ShareFile: auth pattern

ShareFile's password grant cannot satisfy an MFA challenge, so any account
with MFA enabled must authenticate interactively via the **Authorization
Code** flow:

1. Send the user to:
   ```
   https://<SF_SUBDOMAIN>.sharefile.com/oauth/authorize?response_type=code&client_id=<SF_CLIENT_ID>&redirect_uri=<encoded redirect uri>
   ```
2. After login (and MFA if prompted), ShareFile redirects to
   `https://secure.sharefile.com/oauth/oauthcomplete.aspx?code=...` with the
   auth code in the query string.
3. Exchange the code for a token:
   ```
   POST https://<SF_SUBDOMAIN>.sharefile.com/oauth/token
   grant_type=authorization_code
   client_id=<SF_CLIENT_ID>
   client_secret=<SF_CLIENT_SECRET>
   code=<code>
   requirev3=true
   ```

The token response includes `subdomain` and `appcp` — ShareFile accounts can
live on different API endpoints, so use the returned `subdomain` for
subsequent calls rather than assuming it matches the one you authenticated
against.

**Refresh tokens**: call with `grant_type=refresh_token` against the same
`/oauth/token` endpoint. Whether ShareFile rotates the refresh token on each
call (issues a new one you must persist) or keeps it static was an open
question this harness was built to answer — see `test_sf_refresh_rotation()`
in `sync_test.py` and run step 8 to check current behavior for your tenant
before assuming either way.

Once you have a token: `GET /sf/v3/Items({id})/Children` (list, note items
have `CreationDate`/`Creator...` fields), `GET /sf/v3/Items({id})/Download`
(download — this returns a redirect to the actual file content, or a JSON
body describing the item, depending on the item type; a client needs to
follow the redirect itself).

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # then fill in values below
python sync_test.py
```

Required env vars (see `.env.example`):

| Variable | Purpose |
|---|---|
| `BOX_CLIENT_ID`, `BOX_CLIENT_SECRET` | Box app credentials |
| `BOX_USER_ID` | Box user to impersonate for folder access |
| `BOX_TEST_FOLDER_ID` | Box folder used by the list/upload steps |
| `SF_SUBDOMAIN` | ShareFile account subdomain, e.g. `mycompany` for `mycompany.sharefile.com` |
| `SF_CLIENT_ID`, `SF_CLIENT_SECRET` | ShareFile app credentials |
| `SF_TEST_FOLDER_ID` | ShareFile item/folder used by the list/download steps |

`.env` is gitignored and must never be committed. Credentials in it are live
API credentials for production Box and ShareFile accounts — treat them the
same as any other production secret.

## Menu steps (`sync_test.py`)

1. `get_box_token` — client-credentials + impersonation
2. `whoami_box` — confirm which Box user the token acts as
3. `list_box_folder`
4. `upload_box_file` — uploads `sample.txt` (auto-created if missing)
5. `get_sf_token` — interactive Authorization Code flow
6. `list_sf_folder`
7. `download_sf_file`
8. `test_sf_refresh_rotation` — determines refresh-token rotation behavior

## Status / caveats

- This is a manual validation harness, not production code. Review and
  adapt it (error handling, retries, logging, secret storage) before basing
  any production integration on it directly.
- No automated tests. Correctness was checked by hand against live Box and
  ShareFile tenants.
