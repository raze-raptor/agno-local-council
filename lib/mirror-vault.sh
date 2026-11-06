#!/bin/bash
# Read-only markdown mirror of the Obsidian vault for the council (root, systemd timer). The council service cannot see /home.
set -euo pipefail
SRC="/path/to/your/notes/"; DST=/var/lib/agno-vault/
mkdir -p "$DST"
rsync -a --delete --prune-empty-dirs --exclude='.stversions/' --exclude='.obsidian/' --exclude='.git/' --include='*/' --include='*.md' --exclude='*' "$SRC" "$DST"
chown -R root:agno "$DST"; chmod -R u=rwX,g=rX,o= "$DST"
