# Carpool Planner

[![CI](https://github.com/simo-cmos/carpool-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/simo-cmos/carpool-planner/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Eight friends, one concert, four cars — who drives, who rides with whom, and what's the
shortest way to collect everyone? Carpool Planner works that out, collects the guests'
own pickup points through a shareable link, and shows what the shared ride saved.

Self-hosted: it runs on your machine and stores everything in a local SQLite file. No
account, no cloud service, no telemetry. The app calls itself *Drivers Manager* — same
project, older name.

## What it does

- 🗺️ Plan a trip on a map: destination, participants, who has a car and how many seats
- 🚗 Choose drivers and assign passengers
- 🧠 Optimize routes — local search by default, with simulated annealing, ant colony and
  exhaustive search under Settings → Advanced optimizer
- 🔗 Invite guests by link: they add their own pickup point and time window, no account needed
- 🍔 Find food stops along the route, with the detour each one costs
- 🗳️ Let guests vote on the stop; organizers see the tally
- 🌱 See the impact: cars, km, EUR and CO₂ saved against everyone driving alone
- 🔐 Optional organizer password — admin pages require login, guest links keep working
- 📱 Installable as a phone or desktop app, with light and dark themes
- 🌐 Organizer interface in English, Italian, French and Spanish (status and error
  messages are still English-only)

## Quick Start

Requires Python 3.11 or newer.

**macOS / Linux**

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

**Windows** — `start-app.cmd` creates the virtualenv, installs dependencies, and runs the
app in the background so it survives closing the terminal:

```bat
start-app.cmd              :: status-app.cmd and stop-app.cmd to check and stop it
start-app.cmd -Port 8010   :: also -BindHost 0.0.0.0, -DataDir data
```

Either way, open <http://127.0.0.1:8000>.

## Demo data

To see it populated without entering anyone's details, start the app, open **Settings →
Workspace → Import**, and choose `demo/demo-workspace.json`. That's eight fictional
participants around Bologna and a destination — enough to run driver selection and
optimization end to end.

Importing replaces the current workspace, so export first if you have real data in it.

## Sharing a trip

The app is local, so guests need a public URL to reach the invite form. `start-tunnel.cmd`
opens a Cloudflare Quick Tunnel and writes the URL straight into the app's settings:

```bat
start-tunnel.cmd -Mode guest    :: the invite link for participants
start-tunnel.cmd -Mode admin    :: organizer access away from the machine
```

`status-tunnel.cmd` and `stop-tunnel.cmd` take the same `-Mode`. Requires
[cloudflared](https://developers.cloudflare.com/cloudflare-tunnel/) on your PATH. A quick
tunnel URL lasts as long as the process — restart it and Cloudflare usually hands you a
new one.

The Share page turns that into per-guest links with QR codes and WhatsApp sharing. If you
expose the admin tunnel, set an organizer password first under Settings → Security.

When the app is started with `start-app.cmd`, you also get desktop notifications when a
guest opens an invite or sends a response. Tunnels and notifications are Windows-only for
now; the app itself runs anywhere Python does.

## Releases

Releases are named after the people who worked this problem out before there were
computers fast enough to care. **v0.1.0 "Dantzig"** is for George Dantzig and John Ramser,
whose 1959 paper *The Truck Dispatching Problem* introduced what we now call vehicle
routing — give a fleet a set of stops, find the shortest set of routes. Near enough what
this does, with friends instead of trucks.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup,
tests and house rules, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities
privately.

One rule worth repeating here: **never commit real personal data.** Home addresses and
coordinates are personal data, `data/` and `logs/` are gitignored, and the tests use
synthetic locations on purpose.

## License

[MIT](LICENSE) © Matteo Brunetti and Simone Gherardi
