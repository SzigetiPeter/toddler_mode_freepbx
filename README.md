# VoIP Toddler Mode Suite 🧸📞

A lightweight, self-contained automation suite and web dashboard designed to run directly inside a Debian-based FreePBX/Asterisk Proxmox LXC container. This application turns a **Snom 320 IP phone** (and other Snom models) into an interactive, locked-down soundboard toy for toddlers.

---

## 🎯 Features

- **Painless LAN Discovery**: High-speed, optimized asynchronous scanner that queries HTTP/HTTPS on the local `/24` subnet to find active Snom devices, automatically resolving self-signed certificates and protocol redirects.
- **Protected Extension Setup**: Validates existing FreePBX configurations and securely registers Asterisk PJSIP passwords and contact bindings.
- **Remote Snom API Overrides**: Automated provisioning that registers line 1, configures an immediate off-hook hotline straight to the Asterisk game-loop, maps the 12 physical side keys to speed-dials (`701-712`), and remaps context keys (Cancel/Confirm) to `713` and `714`.
- **Dynamic Audio Processor**: Accepts various audio file uploads (MP3, WAV, M4A, OGG) on the web dashboard and uses `sox` to convert them on-the-fly to Asterisk-compliant telephony format (8kHz, 16-bit, Mono PCM).
- **Security-First Sandbox Context**: Injects an isolated Asterisk dialplan context `[toddler-game]` to execute audio playbacks, ensuring the toddler cannot dial outbound trunk lines, access other extensions, or disturb the system.

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

## 🚀 Step-by-Step Installation & Setup

### Step 1: Install on your FreePBX Container
SSH into your Debian-based FreePBX Proxmox LXC container as `root` and run the installer command:
```bash
git clone http://github.com/SzigetiPeter/toddler_mode_freepbx tm && cd tm && sudo bash install.sh
```
The installation script will:
* Install system dependencies (`sox`, `libsox-fmt-all`, `python3-pip`, and `python3-venv`).
* Append `#include asterisk_toddler.conf` to `/etc/asterisk/extensions_custom.conf` and reload Asterisk's dialplan.
* Create a persistent systemd background daemon `toddler-mode.service` running on **Port 8080** as the `asterisk` user.

### Step 2: Create Extension 100 in the FreePBX GUI
FreePBX requires extension frames to be registered in the web interface to maintain database integrity:
1. Open your **FreePBX Web Administration portal** in your browser.
2. Go to **Applications** -> **Extensions**.
3. Click **Add Extension** -> **Add New PJSIP Extension**.
4. Set the **User Extension** to **`100`** (and choose a name, e.g., "Toddler Phone").
5. Click **Submit**, then click the red **Apply Config** button at the top-right of the page.

### Step 3: Register Extension on the Toddler Suite Dashboard
1. Open the Toddler Mode Suite dashboard at `http://<YOUR_FREEPBX_IP>:8080`.
2. The UI will dynamically detect if the extension is present. Click **Register Extension 100** under Step 1.
3. This links the extension, secures PJSIP settings, reloads Asterisk configurations, and unlocks the Snom provisioning panels.

### Step 4: Scan and Provision the Snom Phone
1. Under **2. Snom Device Discovery**, click **Scan LAN for Phones**. The scanner will search the local subnet and list your phone (e.g., at `192.168.1.12`).
2. Alternatively, if network filters interfere, use the **3. Manual Provisioning** panel to input the phone's IP manually.
3. Click **Provision**, enter the phone's current web administrator credentials (default is `admin` / `admin`), and click **Push Config & Reboot**.
4. The server will push configuration parameters to register the line, set up speed-dials, activate the off-hook hotline, and restart the physical handset.

### Step 5: Upload Sounds and Play!
Using the interactive grid on the dashboard, click any keycard to upload sound files (animal noises, music, voices, etc.). 
Once mapped:
* **Lift the receiver**: Snom dials the hotline instantly, opening the audio loop.
* **Dialpad (0-9, \*, #)**: Plays mapped keypad sounds.
* **Side keys (1-12)**: Plays speed-dial sounds `701-712`.
* **Cancel (X) & Confirm (✔)**: Plays context sounds `713` and `714`.

---

## 🛠️ Troubleshooting Guide

### 1. `fwconsole reload` fails with `Undefined array key "trunk_name"`
* **Cause**: This happens if database rows were inserted manually for Extension 100 before the extension was created in the FreePBX GUI, resulting in orphan settings that crash the configuration writer.
* **Solution**: Clean the orphan rows out of the database and reload FreePBX:
  ```bash
  mysql -ufreepbxuser -pvByLlqh0kYrM asterisk -e "DELETE FROM pjsip WHERE id='100';"
  fwconsole reload
  ```
  After this command runs, complete **Step 2** (create the extension in the FreePBX GUI) before registering it on the dashboard.

### 2. Snom phone discovery fails / Manual provisioning times out
If the dashboard cannot detect or provision the phone even though it is active, a firewall or filter is dropping the packets:
* **Check 1: FreePBX Firewall**: FreePBX's aggressive firewall blocks unregistered subnet traffic. Temporarily disable it to test:
  ```bash
  fwconsole firewall disable
  iptables -F
  ping -c 3 <YOUR_PHONE_IP>
  ```
  If ping works after running this, go to FreePBX's admin GUI under **Connectivity** -> **Firewall** -> **Zones** and add your local network range (e.g. `192.168.1.0/24`) to the **Internal (Trusted)** zone.
* **Check 2: Snom Web Server Lockup**: Older Snom 320 phones can have their embedded web server hang when scanned. Unplug the phone's power adapter, wait 5 seconds, plug it back in, and retry.
* **Check 3: Snom IP Filters**: Open the Snom phone web UI on your computer. Go to **Advanced** -> **QoS/Security** and check **IP Range** / **Restricted IP List**. If set, clear the field so that the phone accepts requests from the FreePBX container.

### 3. Verification Commands
* **View Dashboard Service Logs**:
  ```bash
  journalctl -u toddler-mode.service -f
  ```
* **Verify Asterisk Dialplan**:
  To verify if the custom sandbox dialplan is properly loaded in Asterisk:
  ```bash
  asterisk -rvvv
  > dialplan show toddler-game
  ```
