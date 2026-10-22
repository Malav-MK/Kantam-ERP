# Installing Kantam ERP on a Windows server

This guide is for the person setting up Kantam ERP on the factory's Windows PC or server. One script installs everything and starts it automatically at boot. You don't need Docker.

## 1. What you need

- **Windows 10/11 Pro, or Windows Server 2019 or later** (64-bit), on the plant network.
- At least **8 GB RAM, 4 CPU cores and 50 GB free disk space**.
- A **fixed IP address**, or a DNS name such as `erp.kantam.local`.
- **Internet access while installing.** The script downloads Python, Node.js, PostgreSQL and the app's libraries. The ERP itself does not need internet afterwards.
- **An administrator account** on the machine.

## 2. Install

1. Copy the `Kantam-ERP-Windows` folder (or unzip the package) onto the server, e.g. `C:\Setup\Kantam-ERP-Windows`.
2. Open `deploy\windows` and **double-click `Install-Kantam.cmd`**. Click **Yes** when Windows asks for administrator rights.

   To run it from PowerShell instead (opened with **Run as administrator**):

   ```powershell
   cd C:\Setup\Kantam-ERP-Windows\deploy\windows
   powershell -ExecutionPolicy Bypass -File .\Install-Kantam.ps1
   ```

3. Answer the questions:
   - **Approved networks.** The script shows the network it found, e.g. `10.10.20.0/24`. Only devices on these networks can reach the ERP. Press **Y** to accept, or **N** to type your own list. Leave out guest Wi-Fi and machine-control networks.
   - **Owner password.** At least 12 characters. The owner sets up an authenticator app at first sign-in.

4. At the end, the script prints:
   - the **address**, e.g. `https://KANTAM-SRV`;
   - the **owner username** (`owner`);
   - a **device enrolment code**, valid 24 hours, for this server's browser.

Installation takes about 10–20 minutes, depending on download speed.

### Useful options

| Option | Example | Meaning |
|---|---|---|
| `-AllowedCidrs` | `-AllowedCidrs 10.10.20.0/24,10.10.30.0/24` | Approved plant networks (skips the question) |
| `-HostName` | `-HostName erp.kantam.local` | Name people type in the browser (goes into the certificate) |
| `-Port` | `-Port 8443` | Use this if port 443 is already taken (e.g. by IIS) |
| `-CertPfx` | `-CertPfx C:\certs\erp.pfx` | Use your own company certificate instead of a self-signed one |
| `-Demo` | `-Demo` | Also load sample data and demo users (for training, not production) |
| `-PgHost` | `-PgHost 10.10.20.9` | Use an existing PostgreSQL server (you'll be asked for its superuser password) |

## 3. Let the other PCs and tablets in

1. **Trust the certificate (do this once per device).** The installer creates `C:\ProgramData\Kantam ERP\tls\kantam-erp.cer`. Install it on each plant PC or tablet as a *Trusted Root Certification Authority*, or push it with Group Policy. Without this, browsers show a security warning. You can skip this step if you supplied a company certificate with `-CertPfx`.
2. **Enrol each device.** On the server, open PowerShell as administrator and run:

   ```powershell
   & "C:\Program Files\Kantam ERP\bin\KantamAdmin.ps1" new-device "Stores tablet" -Kind tablet
   ```

   Open the ERP address on that device and enter the code it prints.
3. **Create user accounts.** Either use *Administration → Users* in the ERP, or run:

   ```powershell
   & "C:\Program Files\Kantam ERP\bin\KantamAdmin.ps1" create-user rajesh -Name "Rajesh Patel" -Role store_staff -Department STORE
   ```

   The password you set is temporary; the person must change it at first sign-in.

## 4. Everyday administration

Run all of these in PowerShell as administrator, from `C:\Program Files\Kantam ERP\bin`:

| Task | Command |
|---|---|
| Is it running? | `.\KantamAdmin.ps1 status` |
| Restart everything | `.\KantamAdmin.ps1 restart` |
| See recent logs | `.\KantamAdmin.ps1 logs gateway` (or `backend`, `worker`) |
| Encrypted backup | `.\KantamAdmin.ps1 backup -Path E:\KantamBackups` |
| Restore a backup | `.\KantamAdmin.ps1 restore -File E:\KantamBackups\kantam-20261022-090000.kbak` |
| **Start again with empty data** | `.\KantamAdmin.ps1 reset` (takes a safety backup first, then asks for a new owner password) |
| Upgrade to a new version | Copy the new package to the server and run `Install-Kantam.cmd` again. Data, users and settings are kept |
| Uninstall | `.\Uninstall-Kantam.ps1` (keeps data). `.\Uninstall-Kantam.ps1 -RemoveData` deletes everything |

**Backups:**
- Backups are encrypted with a passphrase you choose. Keep the passphrase in your password safe: a backup can't be restored without it.
- Copy backups off the server.
- Test a restore on a spare PC before go-live; a backup only counts once a restore has worked.
- To back up every night, create a Windows scheduled task that runs `backup`. The passphrase prompt means this needs an operator, or a password-vault integration agreed with IT.

## 5. What the installer sets up

| Item | Where / how |
|---|---|
| Program | `C:\Program Files\Kantam ERP` (`app`, `venv`, `bin`) |
| Data | `C:\ProgramData\Kantam ERP`: `config` (secrets), `keys` (token signing), `media` (photos/evidence), `logs`, `backups`, `tls` |
| Database | PostgreSQL 16 on this machine, listening on **localhost only**. Database and login role `kantam` |
| Services | Three tasks under *Task Scheduler → Kantam ERP*: **backend**, **worker** and **gateway**. They start at boot and restart automatically |
| Service account | `LOCAL SERVICE` (low privilege). It can read the program and config, and write only keys, media, logs and run state |
| Network | Only the gateway is reachable, on HTTPS port 443 (or `-Port`), and the firewall admits only the approved networks. The backend (127.0.0.1:8000) and the database are not reachable from the network |
| Secrets | Generated randomly at install, stored in `config\kantam.env` (administrators and the service account only). The PostgreSQL superuser password, if the installer created it, is in `admin\postgres-superuser.txt`. Move it to your password safe and delete the file |

## 6. Troubleshooting

| Symptom | What to check |
|---|---|
| Browser says "network not approved" | The device's IP isn't in the approved networks. Re-run the installer with `-AllowedCidrs …` |
| Browser shows a certificate warning | Install `kantam-erp.cer` on that device (section 3) |
| `status` shows a service not running | Run `.\KantamAdmin.ps1 logs backend` (or `gateway`/`worker`). The `*-runner.log` file shows restarts |
| Port 443 in use | Re-run with `-Port 8443` and use `https://server:8443` |
| winget not found (older Windows Server) | Install Python 3.12 (for all users), Node.js LTS and PostgreSQL 16 manually, then re-run the installer with `-SkipPrerequisites` |
| Forgot the owner password | Another administrator can reset it in the ERP (maker-checker applies). If there is none, use `reset` (this wipes all data) |

All installer runs are logged in `C:\ProgramData\Kantam ERP\logs\install-*.log`.
