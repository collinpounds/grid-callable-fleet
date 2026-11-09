# Run it in your own environment

There are three levels. Each one works without the next.

| Level | What you see | What you need |
| --- | --- | --- |
| 1. Dashboard and results | Every policy version, its score and its proof result. Our recorded live runs. | Python 3.12 |
| 2. Simulator and agent loop | Score a policy yourself, or let Claude write new versions | `largo` (LOGOS 0.11), and for the loop the `claude` CLI |
| 3. Live run | Your own two machines answering a grid event | Two Linux machines with Intel RAPL, and SSH access to them |

This repository contains no hostnames, addresses, keys or tokens. The live run reads machine details from
`live/machines.json`, a file you create. It is in `.gitignore`, so it never gets committed.

## 1. Dashboard and results

```bash
git clone https://github.com/collinpounds/grid-callable-fleet
cd grid-callable-fleet
python3 -I dashboard/serve.py        # then open http://localhost:8765
```

The server listens on `127.0.0.1` only, so nothing on your network can reach it. It refuses requests from other
websites. It makes no outside connections. The dashboard shows the leaderboard, the policies and the most recent
recorded live runs in `live/runs/`. Without level 3 set up, the two live buttons only print an error that no
machine config exists.

## 2. Simulator and agent loop

Install LOGOS 0.11 and its `largo` tool (https://logicaffeine.com). Then:

```bash
policy/build.sh policy/versions/v021.lg             # compile one version to policy/bin/v021
python3 -I sim/simulate.py --policy policy/bin/v021  # score it on the replayed grid day: 718.9
python3 -I sim/simulate.py --policy none             # the do-nothing baseline
```

**Proofs.** `policy/prove.sh policy/versions/v021.lg` proves the four safety rules in `policy/rules.lg`. The rules
are inequalities over the policy's own functions. That needs a `largo` build with order relations in English
theorems, which is not in a release yet. Point `LARGO_PROVE` at such a build. Until then, the proof output for
every version is in `policy/versions/vNNN.prove.txt`.

**Agent loop.** `python3 -I loop.py --iterations 5` asks Claude for new versions through the `claude` CLI, logged in
with your own account. Claude runs with every tool turned off, from an empty folder, and only returns text. The
loop then compiles that text and runs it on your machine. **Policies are code written by a model, so run the loop
on a machine you are comfortable running such code on.** The proofs cover what a policy outputs, not anything
else it might do.

## 3. Live run on your own machines

### What each machine needs

- Linux on an Intel CPU with RAPL (`/sys/class/powercap/intel-rapl:0/energy_uj` exists). We used two Intel N100
  mini PCs.
- `python3`.
- SSH access from your laptop with a key, under a host alias in your own `~/.ssh/config`.
- Permission to read the RAPL counter, which is root-only, and nothing more. Add one sudoers line with
  `sudo visudo -f /etc/sudoers.d/rapl-read`, replacing `alice` with the login user:

  ```text
  alice ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/powercap/intel-rapl\:0/energy_uj
  ```

  Do not give a blanket `NOPASSWD: /usr/bin/cat`, because that lets the account read every file as root.

### Configure

```bash
cp live/machines.example.json live/machines.json     # gitignored
```

```json
{
  "shed": { "ssh": "site-a", "name": "Bay Area machine" },
  "recv": { "ssh": "site-b", "name": "machine outside the Bay Area" },
  "private_link": null
}
```

- `shed` is the machine inside the event zone. It runs two busy workers before the event.
- `recv` is the machine outside the zone. It is idle until work moves to it.
- `ssh` is a host alias from your `~/.ssh/config`. Keys and addresses stay in your SSH config, not here.

Check that both work: `ssh site-a 'sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj'` must print a number
without asking for a password.

### Fire an event

```bash
python3 -I live/live.py --policy policy/bin/v021 --scope statewide   # nowhere to move: the fleet cuts power
python3 -I live/live.py --policy policy/bin/v021 --scope zone        # Bay Area only: work moves to recv
```

Or press the two buttons on the dashboard. A run takes about 90 seconds: baseline, a 60-second event, then
everything is put back. Each run is saved to `live/runs/live-HHMMSS.csv`.

**What runs on your machines.** `live.py` copies `live/drnode.py` to `/tmp/drnode.py` on each machine and starts it
over SSH. It runs only CPU-busy Python workers, and pauses or stops them on command. Every worker is killed when
the SSH session closes, and each one exits on its own after an hour at most. Nothing is installed and nothing
persists.

**Your numbers will differ.** `STEP_MW` and `LIMIT_MW` at the top of `live/live.py` are the package power we
measured on our N100 machines with 0, 1 and 2 workers. Measure yours by running the baseline and reading the log,
and adjust them.

### Optional: a private link between the two machines

By default each machine is driven over its own SSH session. If your two machines share a private network, such as
a WireGuard or Tailscale mesh or a LAN, the shed machine's commands and readings can cross that network from the
recv machine instead:

```json
"private_link": { "shed_ip": "10.0.0.2", "port": 7707, "describe_command": null }
```

- The shed machine binds only to `shed_ip`, and only for 30 seconds. It accepts one connection, and that
  connection must send a random one-time token, which `live.py` hands over through SSH.
- `describe_command` is optional. It runs on the recv machine, and its second output line is shown in the log,
  for example your mesh tool's view of the path.
- Use a private address only. Never put the link on a public interface.
