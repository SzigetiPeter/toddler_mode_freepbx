#!/bin/bash
# VoIP Toddler Mode Suite - Master Installation Script
# Designed for Debian-based FreePBX / Asterisk environments

set -e

# ANSI styling colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==============================================${NC}"
echo -e "${BLUE}        VoIP Toddler Mode Suite Installer     ${NC}"
echo -e "${BLUE}==============================================${NC}"

# 1. Root check
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root.${NC}" 1>&2
   exit 1
fi

CLONE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/var/lib/toddler-mode"
echo -e "Clone Directory: ${GREEN}${CLONE_DIR}${NC}"
echo -e "Installation target: ${GREEN}${INSTALL_DIR}${NC}"

# Copy codebase to target install directory
mkdir -p "${INSTALL_DIR}"
cp -r "${CLONE_DIR}/server" "${INSTALL_DIR}/"
cp -r "${CLONE_DIR}/config" "${INSTALL_DIR}/"

# 2. Install system-level dependencies
echo -e "\n${BLUE}[1/6] Installing system-level dependencies...${NC}"
apt-get update
apt-get install -y sox libsox-fmt-all python3-pip python3-venv

# 3. Create Asterisk sound directory and set permissions
echo -e "\n${BLUE}[2/6] Setting up sound directories...${NC}"
SOUNDS_DIR="/var/lib/asterisk/sounds/en/toddler"
mkdir -p "${SOUNDS_DIR}"

# 4. Inject custom Asterisk dialplan loops
echo -e "\n${BLUE}[3/6] Configuring Asterisk Dialplan...${NC}"
DIALPLAN_SRC="${INSTALL_DIR}/config/asterisk_toddler.conf"
DIALPLAN_DEST="/etc/asterisk/asterisk_toddler.conf"
EXT_CUSTOM="/etc/asterisk/extensions_custom.conf"

if [ -f "${DIALPLAN_SRC}" ]; then
    cp "${DIALPLAN_SRC}" "${DIALPLAN_DEST}"
    chown asterisk:asterisk "${DIALPLAN_DEST}"
    chmod 664 "${DIALPLAN_DEST}"
    echo -e "Copied custom dialplan configuration to ${GREEN}${DIALPLAN_DEST}${NC}"
else
    echo -e "${RED}Warning: Source dialplan config file ${DIALPLAN_SRC} not found! Skipping copy.${NC}"
fi

# Backup and append #include statement to extensions_custom.conf
if [ -f "${EXT_CUSTOM}" ]; then
    cp "${EXT_CUSTOM}" "${EXT_CUSTOM}.bak"
    echo -e "Backed up existing dialplan to ${GREEN}${EXT_CUSTOM}.bak${NC}"
    
    if ! grep -q "asterisk_toddler.conf" "${EXT_CUSTOM}"; then
        echo -e "\n#include asterisk_toddler.conf" >> "${EXT_CUSTOM}"
        echo -e "Appended #include directive to ${GREEN}${EXT_CUSTOM}${NC}"
    else
        echo -e "${YELLOW}Notice: asterisk_toddler.conf inclusion already exists in ${EXT_CUSTOM}${NC}"
    fi
else
    echo -e "${YELLOW}Notice: ${EXT_CUSTOM} not found. Creating it...${NC}"
    echo "#include asterisk_toddler.conf" > "${EXT_CUSTOM}"
    chown asterisk:asterisk "${EXT_CUSTOM}"
    chmod 664 "${EXT_CUSTOM}"
fi

# 5. Set up Python virtual environment & dependencies
echo -e "\n${BLUE}[4/6] Setting up Python virtual environment...${NC}"
VENV_DIR="${INSTALL_DIR}/venv"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/server/requirements.txt"

# 6. Apply recursive ownership of directory files
echo -e "\n${BLUE}[5/6] Adjusting file ownership permissions...${NC}"
chown -R asterisk:asterisk "${SOUNDS_DIR}"
chown -R asterisk:asterisk "${INSTALL_DIR}"
# Ensure the virtual env can be run by asterisk user
chmod -R 755 "${VENV_DIR}"

# 7. Install and enable Systemd service
echo -e "\n${BLUE}[6/6] Creating systemd service configuration...${NC}"
SERVICE_FILE="/etc/systemd/system/toddler-mode.service"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=VoIP Toddler Mode Suite Web Daemon
After=network.target asterisk.service
Wants=asterisk.service

[Service]
Type=simple
User=asterisk
Group=asterisk
WorkingDirectory=${INSTALL_DIR}/server
ExecStart=${INSTALL_DIR}/venv/bin/python app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo -e "Service config written to ${GREEN}${SERVICE_FILE}${NC}"

# Reload systemd and start service
systemctl daemon-reload
systemctl enable toddler-mode.service
systemctl restart toddler-mode.service

echo -e "\n${GREEN}==============================================${NC}"
echo -e "${GREEN}      Installation Completed Successfully!    ${NC}"
echo -e "${GREEN}==============================================${NC}"
echo -e "Flask Dashboard is now running on Port ${BLUE}8080${NC}."
echo -e "Check service logs: ${YELLOW}journalctl -u toddler-mode.service -f${NC}"
echo -e "Asterisk dialplan updated. Reloading Asterisk configuration..."

# Reload Asterisk dialplan if asterisk command is available
if command -v asterisk &> /dev/null; then
    asterisk -rx "dialplan reload"
    echo -e "${GREEN}Asterisk dialplan reloaded.${NC}"
else
    echo -e "${YELLOW}Notice: 'asterisk' command not found. Dialplan will reload once Asterisk restarts.${NC}"
fi
