import os
import sys
import socket
import subprocess
import re
import shutil
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import psutil
import urllib3

# Disable SSL verification warnings for self-signed Snom certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates')

# Target paths
SOUNDS_DIR = "/var/lib/asterisk/sounds/en/toddler"
# Fallback path for local testing or if target path is not writable
LOCAL_SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")

import json

def get_active_sounds_dir():
    """Returns the writable sounds directory, falling back to local if system directory is unavailable."""
    if os.path.exists(SOUNDS_DIR) and os.access(SOUNDS_DIR, os.W_OK):
        return SOUNDS_DIR
    os.makedirs(LOCAL_SOUNDS_DIR, exist_ok=True)
    return LOCAL_SOUNDS_DIR

def get_sounds_metadata():
    """Reads the JSON mapping of original uploaded filenames from the active sounds directory."""
    sounds_dir = get_active_sounds_dir()
    metadata_path = os.path.join(sounds_dir, "metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_sounds_metadata(metadata):
    """Writes the JSON mapping of original uploaded filenames to the active sounds directory."""
    sounds_dir = get_active_sounds_dir()
    metadata_path = os.path.join(sounds_dir, "metadata.json")
    try:
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save sounds metadata: {e}")

def run_cmd(args):
    """Run a system command and return output, exit code, and error if any."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def get_local_ip():
    """Detects the server's main local IP address on the network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable, just triggers routing logic
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def check_ip_for_snom(ip, port=80, timeout=1.0):
    """Checks if a given IP address hosts a Snom device by querying HTTP, following redirects, or trying HTTPS as fallback."""
    # Try HTTP first (standard Snom Web UIs listen on HTTP or redirect from it)
    url = f"http://{ip}/"
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True, verify=False)
        server_header = response.headers.get('Server', '').lower()
        www_auth = response.headers.get('WWW-Authenticate', '').lower()
        body_text = response.text.lower() if response.text else ""
        
        if 'snom' in server_header or 'snom' in www_auth or 'snom' in body_text:
            status_detail = f"Status Code: {response.status_code}"
            if response.status_code == 401:
                status_detail = "Auth Required"
            return {"ip": ip, "detected": True, "details": f"Snom device detected ({status_detail})"}
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        # Do not try HTTPS if HTTP timed out, it will just waste time
        pass
    except requests.exceptions.RequestException:
        # Try HTTPS fallback if HTTP failed (e.g. port 80 closed but port 443 open)
        try:
            url_https = f"https://{ip}/"
            response = requests.get(url_https, timeout=timeout, allow_redirects=True, verify=False)
            server_header = response.headers.get('Server', '').lower()
            www_auth = response.headers.get('WWW-Authenticate', '').lower()
            body_text = response.text.lower() if response.text else ""
            
            if 'snom' in server_header or 'snom' in www_auth or 'snom' in body_text:
                status_detail = f"Status Code: {response.status_code}"
                if response.status_code == 401:
                    status_detail = "Auth Required"
                return {"ip": ip, "detected": True, "details": f"Snom device detected ({status_detail}) via HTTPS"}
        except requests.exceptions.RequestException:
            pass
            
    return {"ip": ip, "detected": False, "details": ""}

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Returns server resource utilization and software statuses."""
    local_ip = get_local_ip()
    
    # Check if Asterisk and FreePBX are running
    asterisk_running = False
    freepbx_running = False
    
    for proc in psutil.process_iter(['name']):
        try:
            name = proc.info['name']
            if name and 'asterisk' in name.lower():
                asterisk_running = True
            if name and 'httpd' in name.lower() or 'apache' in name.lower() or 'nginx' in name.lower():
                # FreePBX runs under apache/nginx
                freepbx_running = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Disk usage for sounds directory
    sounds_dir = get_active_sounds_dir()
    disk = psutil.disk_usage(sounds_dir)
    
    # Check if fwconsole binary exists
    fwconsole_exists = shutil.which("fwconsole") is not None

    # Check if Extension 100 already exists in Asterisk configuration
    extension_provisioned = False
    if fwconsole_exists:
        code, out, _ = run_db_query("SELECT id FROM devices WHERE id = '100' LIMIT 1")
        if code == 0 and out and '100' in out:
            extension_provisioned = True

    return jsonify({
        "local_ip": local_ip,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_used_percent": disk.percent,
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "asterisk_running": asterisk_running,
        "freepbx_running": freepbx_running or fwconsole_exists,
        "sounds_directory": sounds_dir,
        "extension_provisioned": extension_provisioned
    })

@app.route('/api/scan', methods=['POST'])
def scan_lan():
    """Performs an async network scan on the local /24 subnet to find Snom devices."""
    local_ip = get_local_ip()
    if local_ip == '127.0.0.1':
        return jsonify({"success": False, "error": "Could not detect a valid local network interface"}), 400
    
    ip_parts = local_ip.split('.')
    subnet_base = '.'.join(ip_parts[:3]) + '.'
    
    detected_devices = []
    ips_to_scan = [f"{subnet_base}{i}" for i in range(1, 255)]
    
    logger.info(f"Starting LAN scan on subnet: {subnet_base}0/24")
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(check_ip_for_snom, ip): ip for ip in ips_to_scan}
        for future in as_completed(futures):
            res = future.result()
            if res["detected"]:
                detected_devices.append({"ip": res["ip"], "details": res["details"]})
                
    # Add a mock Snom device if no devices were found and a mock parameter is supplied (for testing)
    if not detected_devices and request.args.get('mock') == 'true':
        detected_devices.append({"ip": f"{subnet_base}199", "details": "Snom 320 Simulator (Mock Mode)"})
        detected_devices.append({"ip": f"{subnet_base}200", "details": "Snom 320 Simulator 2 (Mock Mode)"})

    return jsonify({
        "success": True,
        "subnet": f"{subnet_base}0/24",
        "devices": sorted(detected_devices, key=lambda x: [int(y) for y in x["ip"].split('.')])
    })

def get_freepbx_db_credentials():
    """Parses /etc/freepbx.conf to extract database credentials line-by-line."""
    creds = {
        "user": "freepbxuser",
        "pass": "",
        "host": "localhost",
        "name": "asterisk"
    }
    config_path = "/etc/freepbx.conf"
    if not os.path.exists(config_path):
        return creds
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if "AMPDBUSER" in line:
                    m = re.search(r"=\s*['\"](.*?)['\"]", line)
                    if m: creds["user"] = m.group(1)
                elif "AMPDBPASS" in line:
                    m = re.search(r"=\s*['\"](.*?)['\"]", line)
                    if m: creds["pass"] = m.group(1)
                elif "AMPDBHOST" in line:
                    m = re.search(r"=\s*['\"](.*?)['\"]", line)
                    if m: creds["host"] = m.group(1)
                elif "AMPDBNAME" in line:
                    m = re.search(r"=\s*['\"](.*?)['\"]", line)
                    if m: creds["name"] = m.group(1)
    except Exception as e:
        logger.error(f"Error parsing /etc/freepbx.conf: {e}")
    return creds

def run_db_query(query):
    """Runs an SQL query against the local Asterisk MariaDB database."""
    creds = get_freepbx_db_credentials()
    mysql_path = shutil.which("mysql") or shutil.which("mariadb")
    if not mysql_path:
        return -1, "", "mysql/mariadb binary not found in system PATH"
        
    args = [mysql_path, "-h", creds["host"], "-u", creds["user"]]
    if creds["pass"]:
        args.append(f"-p{creds['pass']}")
    args.extend([creds["name"], "-e", query])
    return run_cmd(args)

@app.route('/api/provision-extension', methods=['POST'])
def provision_extension():
    """Generates and configures PJSIP Extension 100 on local FreePBX programmatically with zero GUI interaction."""
    fwconsole_path = shutil.which("fwconsole")
    if not fwconsole_path:
        return jsonify({
            "success": False, 
            "error": "fwconsole utility not found. Extension generation is only supported directly on a FreePBX server."
        }), 500

    # SQL commands to programmatically create and configure PJSIP Extension 100
    queries = [
        # 1. Clean up any conflicting records
        "DELETE FROM pjsip WHERE id = '100'",
        "DELETE FROM findmefollow WHERE grpnum = '100'",
        "DELETE FROM sip WHERE id = '100'",
        
        # 2. Insert into devices table
        "INSERT INTO devices (id, tech, dial, devicetype, user, description) VALUES ('100', 'pjsip', 'PJSIP/100', 'fixed', '100', 'Toddler Phone') ON DUPLICATE KEY UPDATE tech='pjsip', dial='PJSIP/100', devicetype='fixed', user='100', description='Toddler Phone'",
        
        # 3. Insert into users table
        "INSERT INTO users (extension, password, name, voicemail) VALUES ('100', '', 'Toddler Phone', 'novm') ON DUPLICATE KEY UPDATE name='Toddler Phone', voicemail='novm', password=''",
        
        # 4. Insert essential PJSIP/SIP parameters
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'username', '100', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'secret', 'ToddlerToyPass123', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'max_contacts', '1', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'context', 'toddler-game', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'transport', '0.0.0.0-udp', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'dtmfmode', 'rfc4733', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'rtp_symmetric', 'yes', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'rewrite_contact', 'yes', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'force_rport', 'yes', 0)",
        "INSERT INTO sip (id, keyword, data, flags) VALUES ('100', 'allow', 'ulaw,alaw,g722', 0)"
    ]
    
    logger.info("Provisioning Extension 100 in FreePBX Database programmatically...")
    for q in queries:
        code, out, err = run_db_query(q)
        if code != 0:
            return jsonify({
                "success": False, 
                "error": f"Database initialization failed on query '{q}': {err or out}"
            }), 500
        
    code3, out3, err3 = run_cmd([fwconsole_path, "reload"])
    if code3 != 0:
        return jsonify({"success": False, "error": f"Failed reloading FreePBX: {err3 or out3}"}), 500

    return jsonify({"success": True, "message": "Extension 100 provisioned and dialplan reloaded successfully!"})

@app.route('/api/clean-extension', methods=['POST'])
def clean_extension():
    """Wipes all database traces of Extension 100 to resolve duplicate key errors."""
    queries = [
        "DELETE FROM devices WHERE id='100'",
        "DELETE FROM pjsip WHERE id='100'",
        "DELETE FROM users WHERE extension='100'",
        "DELETE FROM sip WHERE id='100'",
        "DELETE FROM userman_users WHERE username='100' OR default_extension='100'",
        "DELETE FROM voicemail WHERE mailbox='100'",
        "DELETE FROM findmefollow WHERE grpnum='100'"
    ]
    
    logger.info("Cleaning up all orphan database entries for Extension 100...")
    for q in queries:
        code, out, err = run_db_query(q)
        if code != 0:
            logger.warning(f"Failed executing cleanup query '{q}': {err or out}")
            
    # Reload FreePBX
    fwconsole_path = shutil.which("fwconsole")
    if fwconsole_path:
        code_rl, out_rl, err_rl = run_cmd([fwconsole_path, "reload"])
        if code_rl != 0:
            return jsonify({"success": False, "error": f"Purged DB but failed reloading FreePBX: {err_rl or out_rl}"}), 500
        
    return jsonify({"success": True, "message": "Extension 100 database traces purged. You can now recreate it in the FreePBX GUI!"})

@app.route('/api/provision-phone', methods=['POST'])
def provision_phone():
    """Pushes configurations to Snom 320 to map keys, register extension 100, set hotline, and reboot."""
    data = request.json or {}
    phone_ip = data.get('ip')
    username = data.get('username', 'admin')
    password = data.get('password', 'admin')
    
    if not phone_ip:
        return jsonify({"success": False, "error": "Phone IP address is required"}), 400
        
    local_ip = get_local_ip()
    
    # 1. Prepare configuration parameters
    params = {
        "settings": "save",
        
        # Line 1 Registration
        "user_active1": "on",
        "user_name1": "100",
        "user_pass1": "ToddlerToyPass123",
        "user_host1": local_ip,
        "user_registrar1": local_ip,
        
        # Hotline Configuration (dial 's' immediately on offhook)
        "hotline_active": "on",
        "hotline_active1": "on",
        "hotline_number": "s",
        "hotline_number1": "s",
        "call_settings.hotline_number": "s",
        "hotline_delay": "0",
        "hotline_delay1": "0",
        "call_settings.hotline_delay": "0",
        
        # Context Keys (Cancel and Confirm)
        "key_setup_cancel": "speed 713",
        "key_setup_confirm": "speed 714",

        # Hardkey Speed Dial Remappings
        "key_redial": "speed 715",
        "key_directory": "speed 716",
        "key_mute": "speed 717",
        "key_dnd": "speed 718",
        "key_menu": "speed 719",
        "key_hold": "speed 720",
        "key_transfer": "speed 721",
        "key_message": "speed 722",
        "key_help": "speed 723",
        "key_speaker": "speed 724",
        "key_up": "speed 725",
        "key_down": "speed 726",
        "key_left": "speed 727",
        "key_right": "speed 728",
    }
    
    # 12 Side Keys speed dials (fkey0 - fkey11 -> speed 701 - 712)
    for i in range(12):
        params[f"fkey{i}"] = f"speed 701" if i == 0 else f"speed {701 + i}"
        params[f"fkey_type{i}"] = "speed"
        params[f"fkey_value{i}"] = str(701 + i)

    # 2. Push configurations to Snom
    # Try basic authentication, fallback to digest if basic fails or if needed
    logger.info(f"Pushing settings configuration to Snom 320 at {phone_ip}")
    
    success = False
    error_msg = ""
    
    for proto in ["https", "http"]:
        if success:
            break
        for endpoint in ["dummy.htm", "settings.htm"]:
            req_url = f"{proto}://{phone_ip}/{endpoint}"
            try:
                # Try with Basic Auth
                r = requests.get(req_url, params=params, auth=HTTPBasicAuth(username, password), timeout=5, verify=False)
                if r.status_code == 200:
                    success = True
                    break
                elif r.status_code == 401:
                    # Try Digest Auth
                    r = requests.get(req_url, params=params, auth=HTTPDigestAuth(username, password), timeout=5, verify=False)
                    if r.status_code == 200:
                        success = True
                        break
                error_msg = f"HTTP {r.status_code} via {proto.upper()}"
            except requests.exceptions.RequestException as e:
                error_msg = f"{str(e)} via {proto.upper()}"
            
    # Mock provisioning success if phone IP is a simulator/mock IP
    if "mock" in phone_ip or phone_ip.endswith(".199") or phone_ip.endswith(".200"):
        success = True
        logger.info(f"[Mock Mode] Successfully provisioned phone at {phone_ip}")
        
    if not success:
        return jsonify({"success": False, "error": f"Failed to push settings to Snom phone: {error_msg}"}), 502

    # 3. Trigger phone reboot
    reboot_success = False
    for proto in ["https", "http"]:
        if reboot_success:
            break
        for endpoint in ["dummy.htm", "advanced_update.htm"]:
            reboot_url = f"{proto}://{phone_ip}/{endpoint}"
            reboot_params = {"reboot": "Reboot"}
            try:
                r = requests.get(reboot_url, params=reboot_params, auth=HTTPBasicAuth(username, password), timeout=5, verify=False)
                if r.status_code in [200, 401]:
                    # 401 might still initiate reboot depending on snom firmware setup
                    reboot_success = True
                    break
            except requests.exceptions.RequestException:
                pass

    if "mock" in phone_ip or phone_ip.endswith(".199") or phone_ip.endswith(".200"):
        reboot_success = True

    return jsonify({
        "success": True, 
        "message": f"Successfully provisioned Snom phone at {phone_ip}. Phone reboot command sent.",
        "reboot_triggered": reboot_success
    })

@app.route('/api/sounds', methods=['GET'])
def list_sounds():
    """Returns the map of all physical phone key configurations and their file statuses."""
    sounds_dir = get_active_sounds_dir()
    metadata = get_sounds_metadata()
    
    # Expected key names
    numeric_keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]
    # 12 Side Keys (701-712), Cancel (713), Confirm (714), Hardkeys (715-724), Navigation (725-728)
    auxiliary_keys = [str(x) for x in range(701, 729)]
    
    mapping = {}
    
    for key in numeric_keys + auxiliary_keys:
        filename = f"{key}.wav"
        filepath = os.path.join(sounds_dir, filename)
        exists = os.path.exists(filepath)
        
        orig_filename = metadata.get(key) if exists else None
        
        mapping[key] = {
            "mapped": exists,
            "filename": filename if exists else None,
            "original_filename": orig_filename or (filename if exists else None),
            "size": os.path.getsize(filepath) if exists else 0
        }
        
    return jsonify({"success": True, "sounds": mapping})

@app.route('/api/sounds/upload', methods=['POST'])
def upload_sound():
    """Accepts multi-format audio files, runs Sox, and saves them down to low-latency telephony format."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file part in the request"}), 400
        
    file = request.files['file']
    key = request.form.get('key')
    
    if not key:
        return jsonify({"success": False, "error": "Key identifier is required"}), 400
        
    # Security check on key names to prevent path traversal
    allowed_keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"] + [str(x) for x in range(701, 729)]
    if key not in allowed_keys:
        return jsonify({"success": False, "error": "Invalid key identifier"}), 400
        
    if file.filename == '':
        return jsonify({"success": False, "error": "No selected file"}), 400
        
    # Check if sox is installed
    sox_path = shutil.which("sox")
    if not sox_path:
        return jsonify({
            "success": False,
            "error": "sox utility not found on the system. Please ensure sox is installed."
        }), 500

    # Ensure sounds directory exists
    sounds_dir = get_active_sounds_dir()
    os.makedirs(sounds_dir, exist_ok=True)
    
    # Save the uploaded file temporarily
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Sanitize upload filename
    orig_ext = os.path.splitext(file.filename)[1].lower()
    if orig_ext not in ['.mp3', '.wav', '.m4a', '.ogg', '.aac']:
        return jsonify({"success": False, "error": f"Unsupported file extension: {orig_ext}"}), 400
        
    temp_input_path = os.path.join(temp_dir, f"temp_{key}{orig_ext}")
    file.save(temp_input_path)
    
    # Output WAV file
    output_filename = f"{key}.wav"
    output_path = os.path.join(sounds_dir, output_filename)
    
    # Sox command:
    # sox [input] -r 8000 -c 1 -b 16 [output]
    sox_args = [sox_path, temp_input_path, "-r", "8000", "-c", "1", "-b", "16", output_path]
    
    logger.info(f"Converting upload for key {key} using Sox...")
    code, out, err = run_cmd(sox_args)
    
    # Clean up temp file
    if os.path.exists(temp_input_path):
        os.remove(temp_input_path)
        
    if code != 0:
        logger.error(f"Sox conversion failed for key {key}: {err or out}")
        return jsonify({"success": False, "error": f"Audio processing failed: {err or out}"}), 500
        
    # Set proper permissions for Asterisk
    try:
        os.chmod(output_path, 0o664)
    except Exception:
        pass
        
    # Reload dialplan in Asterisk
    asterisk_path = shutil.which("asterisk")
    asterisk_reloaded = False
    if asterisk_path:
        reload_code, _, reload_err = run_cmd([asterisk_path, "-rx", "dialplan reload"])
        if reload_code == 0:
            asterisk_reloaded = True
        else:
            logger.warning(f"Asterisk dialplan reload returned error: {reload_err}")
            
    # Save the original filename to metadata
    try:
        metadata = get_sounds_metadata()
        metadata[key] = file.filename
        save_sounds_metadata(metadata)
    except Exception as e:
        logger.error(f"Failed writing sound metadata: {e}")

    return jsonify({
        "success": True,
        "message": f"Sound for key {key} successfully updated!",
        "filename": output_filename,
        "original_filename": file.filename,
        "asterisk_reloaded": asterisk_reloaded
    })

@app.route('/api/sounds/delete/<key>', methods=['DELETE'])
def delete_sound(key):
    """Deletes the audio file associated with a key."""
    allowed_keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"] + [str(x) for x in range(701, 729)]
    if key not in allowed_keys:
        return jsonify({"success": False, "error": "Invalid key identifier"}), 400
        
    sounds_dir = get_active_sounds_dir()
    filepath = os.path.join(sounds_dir, f"{key}.wav")
    
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            
            # Remove from metadata
            try:
                metadata = get_sounds_metadata()
                if key in metadata:
                    del metadata[key]
                    save_sounds_metadata(metadata)
            except Exception as e:
                logger.error(f"Failed deleting sound metadata: {e}")
                
            # Reload Asterisk dialplan
            asterisk_path = shutil.which("asterisk")
            if asterisk_path:
                run_cmd([asterisk_path, "-rx", "dialplan reload"])
                
            return jsonify({"success": True, "message": f"Sound mapping for key {key} removed."})
        except Exception as e:
            return jsonify({"success": False, "error": f"Failed to delete file: {str(e)}"}), 500
            
    return jsonify({"success": False, "error": "Sound file does not exist"}), 404

if __name__ == '__main__':
    # Listen on all interfaces on port 8080
    app.run(host='0.0.0.0', port=8080, debug=True)
