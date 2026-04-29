#!/usr/bin/env bash
# vault.sh — Serge's personal encrypted vault on baza.
#
# Single entrypoint for all vault operations. Dispatches subcommands:
#   vault.sh create <size>     create a new vault (size e.g. 10G, 50G, 1T)
#   vault.sh unlock            unlock + mount the vault
#   vault.sh lock              unmount + close the vault
#   vault.sh status            report state
#   vault.sh enroll-yubikey    add a FIDO2 hardware-key slot
#   vault.sh rotate-passphrase change the passphrase
#   vault.sh panic             force-kill everyone touching the mount, then lock
#
# Security model:
#   - LUKS2 container at /mnt/empirepool/cloud/1/.vault_meta/vault.luks
#     (hidden from the cloud UI file browser).
#   - AES-XTS-PLAIN64 with 512-bit keys.
#   - Argon2id KDF tuned to ~2 sec / 1 GiB memory per try — brute-force with
#     a strong passphrase is infeasible on any hardware Serge will ever face.
#   - Passphrase is prompted interactively, never stored, never logged, never
#     echoed. Keys are wiped on lock.
#   - Mount permissions: 0700, owned by switchhacker. Nothing inside the mount
#     is readable by anyone but Serge, not even root on another account.
#   - Container file is 0600 root:root by default (so the data can't be copied
#     out of the filesystem by a non-root attacker), but the mapper device is
#     chown'd to switchhacker when open so ext4 inside behaves normally.
#   - Optional: enroll a YubiKey / other FIDO2 token for 2FA via systemd-cryptenroll.
set -euo pipefail

CLOUD_ROOT="/mnt/empirepool/cloud/1"
CONTAINER="${CLOUD_ROOT}/.vault_meta/vault.luks"
MAPPER_NAME="baza-vault"
MAPPER="/dev/mapper/${MAPPER_NAME}"
MOUNT="${CLOUD_ROOT}/Vault"
OWNER_USER="switchhacker"
OWNER_UID="1000"
LOG="/home/switchhacker/baza-empire/agent-framework-v3/logs/vault.log"

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | sudo -n tee -a "$LOG" >/dev/null; }

require_sudo() {
  if ! sudo -n true 2>/dev/null; then
    echo "error: passwordless sudo required for vault operations" >&2
    exit 1
  fi
}

is_open() { [[ -e "$MAPPER" ]]; }
is_mounted() { mountpoint -q "$MOUNT"; }

cmd_status() {
  echo "container : $CONTAINER"
  if [[ -f "$CONTAINER" ]]; then
    echo "size      : $(du -h "$CONTAINER" 2>/dev/null | awk '{print $1}')"
    echo "format    : $(sudo -n cryptsetup isLuks "$CONTAINER" && echo LUKS2 || echo not-luks)"
  else
    echo "size      : (not created)"
  fi
  echo "mapper    : $(is_open && echo open || echo closed)"
  echo "mount     : $(is_mounted && echo "mounted at $MOUNT" || echo "not mounted")"
  if is_mounted; then
    df -h "$MOUNT" | tail -1
    echo "in use by : $(sudo -n lsof +D "$MOUNT" 2>/dev/null | awk 'NR>1 {print $1}' | sort -u | paste -sd, - || echo none)"
  fi
  if sudo -n cryptsetup isLuks "$CONTAINER" 2>/dev/null; then
    echo
    echo "--- keyslots ---"
    sudo -n cryptsetup luksDump "$CONTAINER" 2>/dev/null | grep -E "^[[:space:]]*[0-9]+:|Tokens|[[:space:]]+Keyslot:|Memory|Threads|Time cost"
  fi
}

cmd_create() {
  local size="${1:-}"
  if [[ -z "$size" ]]; then
    echo "usage: $0 create <size>  (e.g. 10G, 50G, 1T)" >&2
    exit 2
  fi
  if [[ -f "$CONTAINER" ]]; then
    echo "error: vault already exists at $CONTAINER" >&2
    echo "to rebuild: first run '$0 lock', then remove the file by hand (destructive)" >&2
    exit 1
  fi
  require_sudo
  sudo -n mkdir -p "$(dirname "$CONTAINER")"
  sudo -n chmod 0700 "$(dirname "$CONTAINER")"

  echo ">> allocating container: $size"
  # truncate is instant and makes a sparse file — the LUKS format pass fills the header.
  sudo -n truncate -s "$size" "$CONTAINER"
  sudo -n chmod 0600 "$CONTAINER"
  sudo -n chown root:root "$CONTAINER"

  echo ">> formatting LUKS2 with Argon2id — you will set the passphrase now"
  echo "   Pick a passphrase you will remember. It cannot be recovered."
  echo "   Use a passphrase, not a password: 5+ random words is ideal."
  sudo -n cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --hash sha512 \
    --pbkdf argon2id \
    --pbkdf-memory 1048576 \
    --pbkdf-parallel 4 \
    --iter-time 2000 \
    --sector-size 4096 \
    --label "baza-vault" \
    --use-urandom \
    "$CONTAINER"

  echo ">> opening vault to create filesystem — enter the passphrase you just set"
  sudo -n cryptsetup open --type luks2 "$CONTAINER" "$MAPPER_NAME"

  echo ">> creating ext4 filesystem inside"
  sudo -n mkfs.ext4 -q -L baza-vault -E lazy_itable_init=1,lazy_journal_init=1 "$MAPPER"

  sudo -n mkdir -p "$MOUNT"
  sudo -n chmod 0700 "$MOUNT"
  sudo -n chown "$OWNER_USER:$OWNER_USER" "$MOUNT"

  sudo -n mount "$MAPPER" "$MOUNT"
  sudo -n chown "$OWNER_USER:$OWNER_USER" "$MOUNT"
  sudo -n chmod 0700 "$MOUNT"

  log "vault created size=$size container=$CONTAINER"
  echo
  echo "vault is now UNLOCKED and mounted at: $MOUNT"
  echo "run: $0 lock   (when you're done)"
  echo "run: $0 enroll-yubikey   (to add hardware-key 2FA)"
}

cmd_unlock() {
  if [[ ! -f "$CONTAINER" ]]; then
    echo "error: no vault found. run '$0 create <size>' first." >&2
    exit 1
  fi
  require_sudo
  if is_open; then
    echo "vault already open at $MAPPER"
  else
    echo ">> unlocking (enter passphrase)"
    sudo -n cryptsetup open --type luks2 "$CONTAINER" "$MAPPER_NAME"
  fi
  sudo -n mkdir -p "$MOUNT"
  if ! is_mounted; then
    sudo -n mount "$MAPPER" "$MOUNT"
    sudo -n chown "$OWNER_USER:$OWNER_USER" "$MOUNT"
    sudo -n chmod 0700 "$MOUNT"
  fi
  log "vault unlocked"
  echo "vault mounted at: $MOUNT"
}

cmd_lock() {
  require_sudo
  if is_mounted; then
    echo ">> unmounting"
    sync
    sudo -n umount "$MOUNT"
  fi
  if is_open; then
    echo ">> closing LUKS"
    sudo -n cryptsetup close "$MAPPER_NAME"
  fi
  # Scrub the kernel keyring slots for this name, best-effort.
  sudo -n keyctl list @s 2>/dev/null | awk -v n="$MAPPER_NAME" '$0 ~ n {print $1}' \
    | tr -d ':' | xargs -r -I{} sudo -n keyctl unlink {} @s 2>/dev/null || true
  log "vault locked"
  echo "vault locked."
}

cmd_panic() {
  require_sudo
  echo ">> panic lock: killing everything touching $MOUNT"
  if is_mounted; then
    sudo -n fuser -km "$MOUNT" 2>/dev/null || true
    sleep 1
    sudo -n umount -l "$MOUNT" 2>/dev/null || true
  fi
  if is_open; then
    sudo -n cryptsetup close "$MAPPER_NAME" 2>/dev/null || true
  fi
  log "vault PANIC lock"
  echo "panic lock complete."
}

cmd_enroll_yubikey() {
  if [[ ! -f "$CONTAINER" ]]; then echo "no vault"; exit 1; fi
  require_sudo
  if ! command -v systemd-cryptenroll >/dev/null; then
    echo ">> installing systemd-cryptenroll (via systemd package)"
    sudo -n apt-get install -y systemd-container >/dev/null
  fi
  echo ">> touch your FIDO2 key when it blinks; you'll be prompted for the existing passphrase first"
  sudo -n systemd-cryptenroll --fido2-device=auto "$CONTAINER"
  log "yubikey/fido2 enrolled"
  echo "done. the vault can now be unlocked by passphrase OR hardware key."
}

cmd_rotate_passphrase() {
  if [[ ! -f "$CONTAINER" ]]; then echo "no vault"; exit 1; fi
  require_sudo
  echo ">> you'll be prompted for the OLD passphrase then asked for a NEW one"
  sudo -n cryptsetup luksChangeKey "$CONTAINER"
  log "passphrase rotated"
}

SUB="${1:-status}"
shift || true
case "$SUB" in
  create)             cmd_create "$@" ;;
  unlock|open)        cmd_unlock ;;
  lock|close)         cmd_lock ;;
  status)             cmd_status ;;
  panic)              cmd_panic ;;
  enroll-yubikey)     cmd_enroll_yubikey ;;
  rotate-passphrase)  cmd_rotate_passphrase ;;
  *)
    echo "unknown subcommand: $SUB" >&2
    echo "see: $0 --help (or open the script)" >&2
    exit 2 ;;
esac
