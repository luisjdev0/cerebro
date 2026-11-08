#!/bin/sh
# Installs the `cerebro` CLI binary for Linux (amd64) from GitHub Releases.
#
# Served by this deployment's gateway at /install/install.sh (static file, see
# gateway/Caddyfile) -- meant to be run as:
#
#   curl -fsSL https://<your-gateway>/install/install.sh | sh
#
# Only downloads and installs the binary -- it does NOT know this deployment's
# public URL or any credentials. After installing, run `cerebro login` yourself
# (interactive; or `cerebro login --user <name> --url <gateway URL>`, or with
# `--token <token>`) (see
# luisjdev-pendientes/ecosistema-cerebro for why: a static script has no
# reliable way to know the URL it was fetched through, and `cerebro login`
# already does exactly this, tested and working).
set -eu

CEREBRO_REPO="${CEREBRO_REPO:-luisjdev0/cerebro}"
INSTALL_DIR="${CEREBRO_INSTALL_DIR:-$HOME/.local/bin}"
ASSET_NAME="cerebro-linux-amd64"
URL="https://github.com/${CEREBRO_REPO}/releases/latest/download/${ASSET_NAME}"

echo "Downloading ${URL}..."
mkdir -p "$INSTALL_DIR"
curl -fsSL "$URL" -o "$INSTALL_DIR/cerebro"
chmod +x "$INSTALL_DIR/cerebro"

echo "cerebro installed to $INSTALL_DIR/cerebro"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    echo ""
    echo "$INSTALL_DIR is not in your PATH. Add it, for example:"
    echo "  echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.bashrc"
    ;;
esac

echo ""
echo "Next step: run 'cerebro login' (username and password, or a token) and enter your gateway URL."
