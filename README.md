# VoIP Toddler Mode Suite 🧸📞

A lightweight, self-contained automation suite and web dashboard designed to run directly inside a Debian-based FreePBX/Asterisk Proxmox LXC container. This application turns a registered or unregistered **Snom 320 IP phone** (and other Snom models) into an interactive soundboard toy for toddlers.

---

## 🎯 Features

- **LAN Discovery Engine**: Accelerating async scan (TCP Port 80 query) of the local `/24` subnet to find active Snom devices.
- **1-Click Extension Generation**: Instantly provisions Extension `100` directly in the local FreePBX configuration database.
- **Remote Snom API Overrides**: Automated push config that registers line 1, configures an immediate off-hook hotline straight to the Asterisk server, maps the 12 physical side keys to sequential dialcodes (`701-712`), and context keys (Cancel/Confirm) to `713` and `714`.
- **Dynamic Audio Processor**: Accepts various audio file uploads (MP3, WAV, M4A, OGG) on the web dashboard and uses `sox` to convert them on-the-fly to Asterisk-compliant telephony format (8kHz, 16-bit, Mono PCM).
- **Security-First Sandbox Context**: Injects an isolated Asterisk dialplan context `[toddler-game]` to execute audio playbacks, ensuring the toddler cannot dial outbound trunk lines or disturb other extensions.

---

## 📁 Repository Structure

```plaintext
voip-toddler-mode/
├── install.sh                  # Master zero-interaction deployment script
├── server/
│   ├── app.py                  # Standalone Python/Flask backend (Port 8080)
│   ├── templates/
│   │   └── dashboard.html      # Glassmorphic responsive dashboard UI
│   └── requirements.txt        # Python backend library dependencies
└── config/
    └── asterisk_toddler.conf   # Asterisk custom dialplan context
```

---

## 🚀 Quickstart Installation

Run this directly inside your Debian FreePBX Proxmox LXC container via SSH:

### 1. Clone the Repository
```bash
git clone https://github.com/SzigetiPeter/toddler_mode_freepbx.git voip-toddler-mode
cd voip-toddler-mode
```

### 2. Run the Deployment Script
```bash
chmod +x install.sh
sudo ./install.sh
```

The script will:
- Install dependencies: `sox`, `libsox-fmt-all`, `python3-pip`, and `python3-venv`.
- Append `#include asterisk_toddler.conf` to `/etc/asterisk/extensions_custom.conf`.
- Set up and start a persistent background service `toddler-mode.service` running as the system `asterisk` user on **Port 8080**.

---

## ⚙️ Configuration & Provisioning Workflow

### Step 1: Create the Extension
On the Web Dashboard (`http://<CONTAINER_IP>:8080`), click **Generate extension 100**. This registers the profile in the FreePBX database.

### Step 2: Scan & Remap Phone
1. Click **Scan LAN for Phones**.
2. Locate your Snom 320 device in the list and click **Provision**.
3. Input the phone's administrator username/password (default is `admin`/`admin`) and click **Push Config**. 
4. The server will configure registration, key-bindings, off-hook hotline, and send a reboot signal to the phone.

### Step 3: Upload Sound Mappings
Using the dashboard grids, click any key and upload your media files. Once a file is processed, lifting the phone's receiver immediately connects to the game loop. 
- Pressing numbers `0-9`, `*`, or `#` plays their respective key sounds.
- Pressing side keys `1-12` plays sounds `701-712`.
- Pressing `Cancel (X)` or `Confirm (✔)` plays sounds `713` and `714`.

---

## 🛡️ Under the Hood: Custom Dialplan Loop

The suite injects `[toddler-game]` to execute call loops safely:

```asterisk
[toddler-game]
exten => s,1,Answer()
same => n,Playback(silence/1)
same => n(loop),WaitExten(15)

; Main number pad handler (DTMF audio inputs mid-call)
exten => _[0-9*#],1,Playback(toddler/${EXTEN})
same => n,Goto(s,loop)

; Side and context key handler (Speed dials 701-714)
exten => _7XX,1,Answer()
same => n,Playback(toddler/${EXTEN})
same => n,Hangup()

exten => t,1,Hangup()
exten => i,1,Goto(s,loop)
```

---

## 🛠️ Troubleshooting

### Check Dashboard Daemon Logs
```bash
journalctl -u toddler-mode.service -f
```

### Asterisk Console Verification
To verify the dialplan has loaded correctly:
```bash
asterisk -rvvv
> dialplan show toddler-game
```

### Local Testing / Simulator Mode
If running without a Snom 320 physical device, toggle **Include Simulator Phones** in the dashboard. This spawns mock devices allowing you to test the web interface flows directly.
