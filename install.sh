#!/usr/bin/env bash
# Ego/Dabe installer  -  Ubuntu 22.04 / 24.04 (root)
set -euo pipefail

REPO="${DABE_REPO:-mahdisj676-ai/egoiaidabe}"          # GitHub  user/repo
BRANCH="${DABE_BRANCH:-main}"
DIR="/opt/dabe"

GOLD=$'\e[38;5;178m'; DIM=$'\e[38;5;94m'; RED=$'\e[31m'; OFF=$'\e[0m'
say()  { echo -e "${GOLD}✦${OFF} $*"; }
fail() { echo -e "${RED}✘ $*${OFF}"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run as root:  sudo bash install.sh"
command -v apt-get >/dev/null || fail "This installer supports Ubuntu/Debian only."

echo -e "${GOLD}"
cat <<'ART'
  ███████╗ ██████╗  ██████╗
  ██╔════╝██╔════╝ ██╔═══██╗
  █████╗  ██║  ███╗██║   ██║
  ██╔══╝  ██║   ██║██║   ██║
  ███████╗╚██████╔╝╚██████╔╝
  ╚══════╝ ╚═════╝  ╚═════╝
  ██████╗  █████╗ ██████╗ ███████╗
  ██╔══██╗██╔══██╗██╔══██╗██╔════╝
  ██║  ██║███████║██████╔╝█████╗
  ██║  ██║██╔══██║██╔══██╗██╔══╝
  ██████╔╝██║  ██║██████╔╝███████╗
  ╚═════╝ ╚═╝  ╚═╝╚═════╝ ╚══════╝
ART
echo -e "${OFF}${DIM}  Personal AI Butler installer${OFF}\n"

say "Installing system packages (python, git, ffmpeg)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ffmpeg unzip ca-certificates >/dev/null

say "Downloading Ego/Dabe from github.com/${REPO}…"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  rm -rf "$DIR.tmp"
  git clone -q --depth 1 -b "$BRANCH" "https://github.com/${REPO}.git" "$DIR.tmp" \
    || fail "Could not download https://github.com/${REPO} (is the repo public and the name correct?)"
  if [ -d "$DIR" ]; then cp -a "$DIR/data" "$DIR.tmp/data" 2>/dev/null || true; rm -rf "$DIR"; fi
  mv "$DIR.tmp" "$DIR"
fi

say "Creating Python environment (1-3 minutes)…"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
# optional helper for YouTube (never stops the install if it fails)
if ! command -v deno >/dev/null 2>&1; then
  say "Installing helper for YouTube (optional)…"
  curl -fsSL https://deno.land/install.sh 2>/dev/null | DENO_INSTALL=/usr/local sh >/dev/null 2>&1 || echo "  (optional YouTube helper not installed - fine)"
fi
mkdir -p "$DIR/data"; chmod 700 "$DIR/data"

# quick self-check so problems show NOW, not later
say "Checking installation…"
if ! "$DIR/venv/bin/python" - <<'PY'
import importlib, sys
bad = []
for m in ["telegram","telethon","httpx","fastapi","uvicorn","edge_tts","ddgs","bs4","rich","arabic_reshaper","bidi","psutil","yt_dlp"]:
    try: importlib.import_module(m)
    except Exception as e: bad.append(f"{m}: {e}")
if bad:
    print("\n".join(bad)); sys.exit(1)
PY
then
  echo -e "${RED}Some packages failed to install (see above). Open the menu and press D (Doctor) to fix them.${OFF}"
fi

say "Installing service…"
cat > /etc/systemd/system/dabe.service <<UNIT
[Unit]
Description=Ego/Dabe AI Agent
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
Environment=DABE_HOME=$DIR
ExecStart=$DIR/venv/bin/python $DIR/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable dabe >/dev/null 2>&1

cat > /usr/local/bin/dabe <<WRAP
#!/usr/bin/env bash
cd $DIR
export DABE_HOME=$DIR
exec $DIR/venv/bin/python $DIR/cli.py "\$@"
WRAP
chmod +x /usr/local/bin/dabe

say "Installed! From now on, type  ${GOLD}dabe${OFF}  anytime to open the panel."
sleep 1
if [ -r /dev/tty ]; then exec /usr/local/bin/dabe </dev/tty; else echo "Run:  dabe"; fi
