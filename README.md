# goto/

Short links for your browser. Type `goto/mail` and land in your inbox, `goto/cal` for your calendar, `goto/jira/OPS-12` for a ticket.

- **Runs on your own computer.** No account, no cloud, no tracking. Works on **Windows, macOS and Linux**.
- **Create, edit and delete links** from a web page at `http://goto/` or from the terminal.
- **Aliases:** `goto/mail`, `goto/m` and `goto/email` can all go to the same place.
- **No duplicates:** every name and alias is unique, and each destination can only be added once. A second name for the same place becomes an alias.
- **Smart links:** pass extra text through, e.g. `goto/g/cats` to search Google for cats.
- **One Python file, no dependencies.** Optionally run it on a Raspberry Pi so everyone on your home network gets the same links.

## Install

You need **Python 3.8 or newer**.

- **Windows:** get it from [python.org](https://www.python.org/downloads/), or run `winget install Python.Python.3.12`.
- **macOS:** usually already there. If not, run `xcode-select --install`.
- **Linux:** run `sudo apt install python3`.

Then download goto, either with `git clone https://github.com/Salterm27/goto.git` or from GitHub: **Code → Download ZIP**, then unzip it. The commands below assume you are in that folder: `goto` if you cloned it, `goto-main` if you downloaded the ZIP.

### Windows 10 / 11

Double-click **`install-windows.cmd`** in the goto folder. Windows asks once for permission to edit the hosts file; choose **Yes**.

goto then runs in the background, starts at every sign-in, and adds a `goto` command for PowerShell and Windows Terminal.

### macOS

```bash
cd goto        # or goto-main
./install-macos.sh
```

It asks for your password once. goto then runs in the background, starts at every login, and adds a `goto` command to your terminal.

### Linux / Raspberry Pi (for your whole network)

```bash
cd goto        # or goto-main
sudo ./install-linux.sh
```

This sets goto up as a system service that every device on your network can use. See [Share with your home network](#share-with-your-home-network).

### Then

Open **`http://goto/`** in your browser and add your first link. The first time, type it **with `http://`**; after that, the browser knows `goto/` is a site and `goto/mail` just works.

> **Firefox:** if typing `goto/mail` searches the web instead, open `about:config` and add a boolean setting `browser.fixup.domainwhitelist.goto` set to `true`.

## Using it

**Web page:** `http://goto/` lists all your links with a filter (press `/`) and Edit / Delete on each row. Visiting a link that doesn't exist yet, such as `goto/jira`, opens the page with the name filled in so you can create it.

**Terminal:**

```bash
goto add mail https://mail.google.com -a m -a email -d "Personal Gmail"
goto ls                              # list all (goto ls google filters)
goto show mail
goto edit mail --url https://outlook.office.com
goto edit mail --rename inbox        # rename (an alias can be promoted this way)
goto edit mail --aliases m inbox     # replace all aliases
goto alias add mail gm               # add alias(es)
goto alias rm gm                     # remove an alias
goto rm mail                         # delete the link and all its aliases
goto mail                            # open it in your browser
goto export > backup.json            # back up / move your links
goto import backup.json
goto doctor                          # check that everything is set up
```

Running `goto rm` on an alias doesn't delete anything. It tells you whether you meant to remove the alias or the whole link.

> **Windows:** use the `goto` command in PowerShell or Windows Terminal. In the old Command Prompt (`cmd.exe`), `goto` is a built-in command and can't be replaced.

### Smart links

| You create | You type | You land on |
|---|---|---|
| `gh` → `https://github.com` | `goto/gh/anthropics` | `https://github.com/anthropics` (extra path is appended) |
| `g` → `https://www.google.com/search?q=%s` | `goto/g/cats` | Google search for "cats" (`%s` is replaced) |
| `jira` → `https://acme.atlassian.net/browse/%s` | `goto/jira/OPS-12` | that ticket |

**Names:** letters, numbers, `-`, `_` and `.`; case doesn't matter, so `goto/Mail` and `goto/mail` are the same link.
**URLs:** `mail.google.com` is fine; `https://` is added for you, or `http://` for local addresses like `localhost:3000`.

## Updating and uninstalling

To update, get the latest files (`git pull`, or download the ZIP again) and run the installer again. Your links are kept.

| | Update | Uninstall (keeps your links) | Also delete your links |
|---|---|---|---|
| Windows | `install-windows.cmd` | `uninstall-windows.cmd` | `uninstall-windows.cmd -Purge` |
| macOS | `./install-macos.sh` | `./uninstall-macos.sh` | `./uninstall-macos.sh --purge` |
| Linux | `sudo ./install-linux.sh` | `sudo systemctl disable --now goto` | also `sudo rm -rf /var/lib/goto` |

### Where things live

| | Windows | macOS | Linux service |
|---|---|---|---|
| Your links | `%LOCALAPPDATA%\goto\goto.db` | `~/.goto/goto.db` | `/var/lib/goto/goto.db` |
| Log | `%LOCALAPPDATA%\goto\server.log` | `~/.goto/server.log` | `journalctl -u goto` |
| Autostart | `HKCU\...\CurrentVersion\Run` → `goto` | `~/Library/LaunchAgents/com.goto.server.plist` | `goto.service` (systemd) |

## Share with your home network

On a laptop, goto only answers your own computer. To give everyone in the house the same links, run it on an always-on machine such as a Raspberry Pi:

1. **Install it there:** copy the goto folder over and run `sudo ./install-linux.sh`.
2. **Bring your links along** (optional): run `goto export > links.json` on your computer, copy the file over, then run `goto import links.json` on the Pi.
3. **Give the Pi a fixed address.** Set a DHCP reservation in your router so its IP never changes.
4. **Point the name `goto` at the Pi** for every device, using one of these:
   - **Router local DNS** (most routers, also OpenWrt, pfSense, UniFi): add a host entry `goto` → the Pi's IP.
   - **Pi-hole:** *Local DNS → DNS Records* → `goto` → the Pi's IP.
   - **Per computer:** add `<pi-ip> goto` to that computer's hosts file (not possible on phones).
5. **On computers that had goto installed locally,** uninstall it so they use the shared one. The `goto` command keeps working if you keep `goto.py`: use `python3 goto.py ...`, or set it up as an alias.

If people will type a longer name such as `goto.lan/`, install with `sudo GOTO_ALLOW_HOSTS="goto.lan" ./install-linux.sh` so editing works from that name too.

**Note:** on the shared version, anyone on your network can add or change links. There's no login, so only run it on a network you trust.

## How it works

- The name `goto` points at the goto server: through your computer's hosts file for a local install, or your network's DNS for a shared one.
- The server answers `goto/<name>` with a temporary (302) redirect. Browsers don't cache a temporary redirect, so edits take effect immediately.
- Links live in a small SQLite database. Names and aliases sit in their own table with a unique key, which is what guarantees no name is ever used twice; destination URLs have a unique key too.
- A local install only answers requests from your own computer. The editing API (`/_/api/...`) only accepts JSON requests addressed to a known hostname, so websites you visit can't change your links behind your back.

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

Start with **`goto doctor`**. It checks that the name `goto` points at your computer and that the server is answering.

- **The browser searches the web instead of opening the link:** type `http://goto/mail` once (see [Then](#then)).
- **"Port 80 is already in use":** another program is using the web port. That's often a local web server, Docker, Skype (older versions) or IIS on Windows. Stop it, or install goto on another port: `install-windows.cmd -Port 8080` on Windows, `GOTO_PORT=8080 ./install-macos.sh` on macOS. Links then look like `goto:8080/mail`.
- **Redirects feel slow:** reinstall to make sure both `127.0.0.1 goto` and `::1 goto` are in your hosts file. Without the second one, some networks look up `goto` online first.
- **Windows Security warns about the hosts file:** the installer adds two lines, `127.0.0.1 goto` and `::1 goto`, which point `goto` at your own computer. You can check them in `C:\Windows\System32\drivers\etc\hosts`.
- **"The connection isn't secure" warning:** goto uses plain `http` to your own computer, which is expected for a local service.
- **Logs:**
  - Windows: `Get-Content $env:LOCALAPPDATA\goto\server.log -Tail 20`
  - macOS: `tail ~/.goto/server.log`
  - Linux: `journalctl -u goto -n 50`

## License

[MIT](LICENSE)
