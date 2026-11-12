# Deploying on a VM (replaces Zeabur)

Decided 2026-11-03: Whisper + pyannote are too heavy for Workers or Vercel, so this runs on one small VM.
**Oracle Cloud Always Free** first (Ampere A1, Singapore), **Hetzner** (about 8 GB, roughly EUR 15/month) if Oracle won't
verify the account or has no capacity. The same files work on both.

Shape: Docker Compose with the app (the repo's `Dockerfile`, unchanged; it builds on ARM) and a Cloudflare Tunnel.
The VM has **no open inbound ports**; traffic reaches the app only through the tunnel.

## 1. Create the VM
- Oracle: Compute → Instances → Create. Image **Ubuntu 24.04** (aarch64), shape `VM.Standard.A1.Flex`,
  4 OCPU / 24 GB (free maximum; 2 / 12 is enough). Add your SSH key. If it says "out of host capacity",
  retry later or pick fewer OCPUs.
- Hetzner: CX/CAX with at least 8 GB RAM, Ubuntu 24.04, your SSH key.
- Leave the firewall closed except SSH (Oracle's default security list already only allows 22).

## 2. Set it up
```bash
ssh ubuntu@<vm-ip>
curl -fsSL https://raw.githubusercontent.com/TheBooleanJulian/minutes-in-seconds/master/deploy/vm/setup.sh | bash
exit   # log back in so the docker group applies
```

## 3. Tunnel + secrets
1. Cloudflare Zero Trust → Networks → Tunnels → **Create a tunnel** (Cloudflared), name `minutes-in-seconds`.
2. Copy the token from the install command into `~/minutes-in-seconds/deploy/vm/.env` as `TUNNEL_TOKEN`.
3. Public hostname: `minutes-in-seconds.thebooleanjulian.dev` → service `http://app:8080`.
   The tunnel adds its own DNS record, which takes precedence over the zone wildcard.
4. Copy `HF_TOKEN` and `ANTHROPIC_API_KEY` from the Zeabur service into the same `.env` (both optional).

## 4. Start
```bash
cd ~/minutes-in-seconds/deploy/vm
docker compose up -d --build        # first build 10-20 min on ARM; the first job also downloads model weights
docker compose logs -f app
```
Check `https://minutes-in-seconds.thebooleanjulian.dev/`, run one short recording end to end, then terminate the Zeabur
service.

## Updating
```bash
cd ~/minutes-in-seconds && git pull && cd deploy/vm && docker compose up -d --build
```

## Notes
- Jobs live in memory (as on Zeabur): a restart drops jobs in progress. Model weights persist in the `cache` volume.
- The app has no login, as before. If that should change, put a Cloudflare Access policy on the hostname.
- Oracle can reclaim idle Always Free instances. Everything here is in git, so a lost VM is a re-run of steps 1-4.
