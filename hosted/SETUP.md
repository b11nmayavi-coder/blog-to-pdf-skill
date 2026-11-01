# Hosted mode: setup playbook

Hosted mode turns the skill into a small web app on the user's own machine. They paste a URL, or share one from their phone, and get the PDF without opening a Claude Code session. Use this playbook when the user asks to host it, run it as a web app or service, use it from their phone or share sheet, or set it up for unattended use.

Here, `SKILL_DIR` is this skill's directory, `HOSTED` is `SKILL_DIR/hosted`, and `DATA` is `~/.local/share/blog-pdf`. `DATA` holds the venv, `.env`, `jobs.json` and `out/`, so the skill directory stays a clean git checkout.

Work through the steps in order. Check each one before moving on. The user must do some steps in a browser; for those, tell them exactly what to click and wait for them.

## 1. Prerequisites

Check what is already installed: `python3 --version` (3.10 or later), `curl`, and Chrome or Chromium. For Chrome, `helpers.chrome_bin()` must resolve:

```bash
python3 -c "import sys; sys.path.insert(0,'SKILL_DIR'); import helpers; print(helpers.chrome_bin())"
```

Install anything missing, or tell the user how to install it. On macOS use `brew`; on Linux use the system package manager. `sudo` needs the user's own terminal, so ask them to run it with `! <command>`.

## 2. Python environment

```bash
mkdir -p ~/.local/share/blog-pdf
python3 -m venv ~/.local/share/blog-pdf/venv
~/.local/share/blog-pdf/venv/bin/pip install -r HOSTED/requirements.txt
cp -n HOSTED/.env.example ~/.local/share/blog-pdf/.env && chmod 600 ~/.local/share/blog-pdf/.env
```

## 3. API keys

The companion page needs an Anthropic API key from console.anthropic.com → API keys. A Claude subscription does not include API credits; those are bought separately. An Ollama Cloud key (`OLLAMA_API_KEY`) is an optional backup.

**Never ask the user to paste a key into the chat.** Have them append it from their own terminal:

```
! read -rsp "Anthropic key: " k && echo "ANTHROPIC_API_KEY=$k" >> ~/.local/share/blog-pdf/.env && unset k && echo saved
```

Then check that it works without printing it:

```bash
set -a; . ~/.local/share/blog-pdf/.env; set +a
~/.local/share/blog-pdf/venv/bin/python -c "import anthropic; print(anthropic.Anthropic().models.list(limit=1).data[0].id)"
```

If the `.env` has duplicate key lines (an earlier empty `ANTHROPIC_API_KEY=` plus the appended one), the last one wins in both systemd and the shell. The empty line is harmless, but tidy it up.

## 4. Where PDFs go

- **Google Drive (default):** needs an rclone remote named `gdrive`. Check with `rclone listremotes`. If it is missing, the user runs `! rclone config create gdrive drive`, which opens a browser to sign in to Google. PDFs land in `Blog PDFs/<date>/`.
- **Local only:** set `DRIVE_DIR=` (empty) in `.env`. PDFs are then only available from the web UI's Download link and from `DATA/out/`.

Never use `rclone link`: it makes the file public. The service builds the Drive view URL from `rclone lsjson`.

## 5. Smoke test

```bash
cd HOSTED && set -a && . ~/.local/share/blog-pdf/.env && set +a
~/.local/share/blog-pdf/venv/bin/python pipeline.py https://example-article-url --no-upload
```

Use a real article the user cares about. Then open the resulting `DATA/out/*.pdf` and look at it: run `helpers.page_grid` and read the PNG. Check the pagination, that the images are there, and that the companion page fits on one page.

## 6. Run it in the background

**Linux (systemd user service):**
```bash
mkdir -p ~/.config/systemd/user && cp HOSTED/deploy/blog-pdf.service ~/.config/systemd/user/
# fix WorkingDirectory if the skill isn't at ~/.claude/skills/blog-to-pdf
systemctl --user daemon-reload && systemctl --user enable --now blog-pdf
loginctl show-user "$USER" -p Linger   # if "no": ask the user to run  ! sudo loginctl enable-linger $USER
curl -s localhost:8099/healthz
```

**macOS (launchd):**
```bash
sed "s|HOME_DIR|$HOME|g" HOSTED/deploy/com.blogpdf.server.plist > ~/Library/LaunchAgents/com.blogpdf.server.plist
launchctl load -w ~/Library/LaunchAgents/com.blogpdf.server.plist
curl -s localhost:8099/healthz
```

The app is now running at http://127.0.0.1:8099. It binds to localhost only. If the user only wants it on this machine, stop here.

## 7. Private public URL (optional): Cloudflare Tunnel + Access

The user needs a domain on Cloudflare (free plan). Without one, suggest Tailscale instead: `tailscale serve 8099` reaches the app from their own devices with no public exposure.

1. Install `cloudflared`, then have the user run `! cloudflared tunnel login` and pick the zone in the browser.
2. Create the tunnel, point a hostname at it, and write the config:
   ```bash
   cloudflared tunnel create blogpdf
   cloudflared tunnel route dns blogpdf <host>
   ```
   Create `~/.cloudflared/blogpdf.yml` from `deploy/blogpdf.yml.example`, using the tunnel UUID and credentials path printed by `create`.
3. Run the tunnel as a service: `deploy/cloudflared-blogpdf.service` on Linux (fix the cloudflared path), or `cloudflared service install` / a LaunchAgent on macOS.
4. **Before** sharing the URL, the user creates an Access application in the Cloudflare dashboard. Walk them through it:
   - Zero Trust → Access → Applications → Add → Self-hosted, hostname = `<host>`.
   - Add a policy with action **Allow** and Include → Emails = their email.
   - If Settings → Authentication has no login method yet, add **One-time PIN**.
5. Check that it fails closed. `curl -s -o /dev/null -w '%{http_code} %{redirect_url}' https://<host>/` must return a 302 to `*.cloudflareaccess.com`, not 200. The server also returns 403 to any Cloudflare-proxied request that has no Access JWT. So a missing Access app means "locked", never "open".

## 8. iPhone share sheet (optional; needs step 7)

1. In Zero Trust → Access → Service credentials → Service tokens, create a token. The user saves the Client ID and Secret somewhere safe; they are only shown once.
2. Under Access policies, add a policy with **Action: Service Auth** (not Allow) and Include → Service Token = that token.
3. Attach the policy to the application and **save the application**. The policy page must show the app under "Used by applications". If it doesn't, Access returns 403 and the Shortcut fails with "couldn't convert from Rich Text to Dictionary".
4. Test it with an invalid URL so no job gets queued. Success is HTTP 400 `{"error":"need an http(s) url"}`, which comes from our server:
   ```bash
   curl -s -w '\nHTTP %{http_code}\n' -X POST https://<host>/jobs \
     -H 'CF-Access-Client-Id: <id>' -H 'CF-Access-Client-Secret: <secret>' \
     -H 'Content-Type: application/json' -d '{"url":"not-a-url"}'
   ```
5. Build the Shortcut:
   - Turn on "Show in Share Sheet", accepting URLs and Safari web pages.
   - Get URLs from Shortcut Input.
   - **Get Contents of URL**: Method POST, URL `https://<host>/jobs`, the two CF-Access headers, Request Body JSON `url` = the URL.
   - Show Notification "Queued ✓".

   You can also hand the user a ready-to-paste description of these actions for the Shortcuts app.

## Operating it

- To update the code, `git pull` in `SKILL_DIR`, then restart the service: `systemctl --user restart blog-pdf`, or `launchctl kickstart -k gui/$UID/com.blogpdf.server` on macOS.
- For logs, use `journalctl --user -u blog-pdf -f` (Linux) or `DATA/server.log` (macOS). `GET /jobs/<id>` shows a single job's log.
- If a PDF comes out wrong (an odd layout, or a paywall), run the normal Claude-in-the-loop skill on that URL.
