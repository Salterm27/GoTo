# goto/

Short links for your browser. Type `goto/mail` and land in your inbox.

- Runs locally on your Mac, starts automatically at login.
- Create, edit and delete links from a web page (`http://goto/`) or the terminal (`goto add ...`).
- Every link can have **aliases** (`goto/mail`, `goto/m`, `goto/email` → same place).
- **No duplicates:** every name and alias is unique across all links, and each destination URL can only exist once. A second name for the same place has to be an alias.
- One Python file, no dependencies. The same file runs on a Raspberry Pi for the whole home network.

## Install on your Mac

```bash
cd goto
chmod +x install-macos.sh
./install-macos.sh
```

It asks for your password once (to add `127.0.0.1 goto` to `/etc/hosts` and install the `goto` command). Then open **http://goto/** and add your first link.

> First time in a browser, type `http://goto/` (with `http://`). After that the browser knows `goto/` is a site and `goto/mail` just works.
> Firefox: if it still searches, open `about:config` and add a boolean `browser.fixup.domainwhitelist.goto` = `true`.

## Using it

**Web page:** `http://goto/` lists everything, with a filter (press `/`), and Edit / Delete on each row. Visiting a link that doesn't exist (e.g. `goto/jira`) opens the page with the name filled in, ready to create it.

**Terminal:**

```bash
goto add mail https://mail.google.com -a m -a email -d "Personal Gmail"
goto ls                      # list all       |  goto ls google   (filter)
goto show mail
goto edit mail --url https://outlook.office.com
goto edit mail --rename inbox        # rename (an alias can be promoted this way)
goto edit mail --aliases m inbox     # replace all aliases
goto alias add mail gm               # add alias(es)
goto alias rm gm                     # remove an alias
goto rm mail                         # delete the link and all its aliases
goto mail                            # open it in your browser
goto doctor                          # check setup
```

`goto rm` on an alias refuses and tells you whether you meant to drop the alias or the whole link.

**Smart links**

| You create | You type | You land on |
|---|---|---|
| `gh` → `https://github.com` | `goto/gh/anthropics` | `https://github.com/anthropics` (extra path is appended) |
| `g` → `https://www.google.com/search?q=%s` | `goto/g/cats` | Google search for "cats" (`%s` is replaced) |
| `jira` → `https://acme.atlassian.net/browse/%s` | `goto/jira/OPS-12` | that ticket |

**Names:** letters, numbers, `-`, `_`, `.`; case doesn't matter (`goto/Mail` = `goto/mail`).
**URLs:** `mail.google.com` is fine; `https://` is added for you (`http://` for local addresses like `localhost:3000`).

## Where things live (Mac)

| What | Where |
|---|---|
| Your links | `~/.goto/goto.db` (SQLite; back up with `goto export > goto-backup.json`) |
| Program | `~/.goto/goto.py` |
| Log | `~/.goto/server.log` |
| Service | `~/Library/LaunchAgents/com.goto.server.plist` |

Upgrade: replace `goto.py` and run `./install-macos.sh` again. Uninstall: `./uninstall-macos.sh` (keeps your links; add `--purge` to delete them).

## Whole home network (Raspberry Pi)

1. **Move your links** from the Mac:
   ```bash
   goto export > links.json
   scp goto.py install-linux.sh links.json pi@raspberrypi.local:~
   ```
2. **Install on the Pi:**
   ```bash
   ssh pi@raspberrypi.local
   sudo ./install-linux.sh
   goto import links.json
   ```
   It runs as a systemd service on port 80 for every device on your network (`sudo systemctl status goto`, logs with `journalctl -u goto`).
3. **Make `goto` point at the Pi** for every device. Give the Pi a fixed IP (DHCP reservation on your router), then pick one:
   - **Router local DNS** (most routers, also OpenWrt / pfSense / UniFi): add a host entry `goto` → Pi's IP.
   - **Pi-hole** (if you run it): *Local DNS → DNS Records* → `goto` → Pi's IP.
   - **Per device**: add `<pi-ip> goto` to that device's hosts file (only practical for computers, not phones).
4. **Switch your Mac over:** run `./uninstall-macos.sh` (your Mac then uses the network's `goto`), and keep the `goto` command by adding to `~/.zshrc`:
   `alias goto='python3 ~/.goto/goto.py'` (the CLI already talks to `http://goto`).

If your router hands out names with a suffix and people type e.g. `goto.lan/`, install with
`sudo GOTO_ALLOW_HOSTS="goto.lan" ./install-linux.sh` so editing works from that name too.

**Note:** on the network version anyone on your Wi-Fi can add or edit links (that's the point of a shared one). There's no login, so don't run it on a network you don't trust.

## How it works

- `goto` resolves to the server through `/etc/hosts` (Mac) or your network's DNS (Pi).
- The server answers `goto/<name>` with a temporary (302) redirect, never a permanent one, so browsers don't cache old destinations after you edit a link.
- Data is a small SQLite database. Names live in their own table with a unique key, which is what guarantees no name or alias can ever be used twice; destination URLs have a unique key too.
- The editing API (`/_/api/...`) only accepts JSON requests addressed to a known hostname, which stops other websites you visit from changing your links behind your back.

### API

| Method | Path | Body |
|---|---|---|
| GET | `/_/api/links?q=filter` | |
| POST | `/_/api/links` | `{"name", "url", "aliases": [], "description"}` |
| GET | `/_/api/links/<name or alias>` | |
| PATCH | `/_/api/links/<name or alias>` | any of `name`, `url`, `description`, `aliases`, `add_aliases`, `remove_aliases` |
| DELETE | `/_/api/links/<name>` | |
| GET | `/_/api/export` | |
| POST | `/_/api/import` | `{"links": [...]}` |

## Troubleshooting

- **`goto doctor`** checks that `goto` resolves and the server answers.
- **Browser searches Google instead of opening the link:** type `http://goto/mail` once (see top).
- **"Port 80 in use"** (something else, like a local web server, already uses it): reinstall with `GOTO_PORT=8080 ./install-macos.sh`. Links then look like `goto:8080/mail`, which is less neat, so freeing port 80 is better.
- **Browser warns the connection isn't secure:** it's plain `http` to your own machine; that's expected for a local service.
- **Service status:** `launchctl print gui/$(id -u)/com.goto.server | head -20` and `tail ~/.goto/server.log`.
