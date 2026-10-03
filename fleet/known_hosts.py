#!/usr/bin/python3
"""Print a known_hosts for GitHub and every host in an ansible inventory group.
Fleet keys are scanned from <host>.<domain> and recorded under the short name,
the FQDN and each resolved address; GitHub's come from its published API. A
host that does not answer keeps its entries from the previous file."""

import argparse
import json
import socket
import subprocess
import sys
import urllib.request

import yaml

KEY_TYPES = "ed25519,ecdsa,rsa"
GITHUB = "github.com"
GITHUB_META = "https://api.github.com/meta"


def members(inventory, group):
    """Return the sorted hosts of group, including those of its child groups."""
    hosts, children = {}, {}

    def walk(name, node):
        node = node or {}
        hosts.setdefault(name, set()).update(node.get("hosts") or {})
        for child, sub in (node.get("children") or {}).items():
            children.setdefault(name, set()).add(child)
            walk(child, sub)

    walk("all", (inventory or {}).get("all"))
    found, pending, seen = set(), [group], set()
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            found |= hosts.get(name, set())
            pending.extend(children.get(name, ()))
    return sorted(found)


def addresses(fqdn):
    try:
        infos = socket.getaddrinfo(fqdn, 22, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def keyscan(fqdns, timeout):
    """Return {fqdn: sorted "type key" strings} for the hosts that answered."""
    if not fqdns:
        return {}
    proc = subprocess.run(
        ["ssh-keyscan", "-T", str(timeout), "-t", KEY_TYPES, *fqdns],
        capture_output=True,
        text=True,
        check=False,
    )
    keys = {}
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and not line.startswith("#"):
            keys.setdefault(fields[0], set()).add(f"{fields[1]} {fields[2]}")
    return {host: sorted(found) for host, found in keys.items()}


def github_keys(timeout, url=GITHUB_META):
    """Return GitHub's published "type key" strings, or [] if they cannot be fetched."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return sorted(json.load(response)["ssh_keys"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def previous_entries(path):
    """Return {first alias: [lines]} from an earlier output of this tool."""
    by_host = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return by_host
    for line in lines:
        if line.strip() and not line.startswith("#"):
            by_host.setdefault(line.split(None, 1)[0].split(",", 1)[0], []).append(line)
    return by_host


def entries(names, domain, timeout, resolve=addresses):
    """Return {name: (aliases, keys)} for GitHub and each fleet host; keys may be empty."""
    fqdn = {name: f"{name}.{domain}" if domain else name for name in names}
    scanned = keyscan(list(fqdn.values()), timeout)
    found = {GITHUB: ([GITHUB], github_keys(timeout))}
    for name, full in fqdn.items():
        keys = scanned.get(full, [])
        aliases = list(dict.fromkeys([name, full, *(resolve(full) if keys else ())]))
        found[name] = (aliases, keys)
    return found


def render(found, previous):
    """Yield (name, lines, fresh) per host: fresh keys, else the previous lines."""
    for name, (aliases, keys) in found.items():
        if keys:
            yield name, [f"{','.join(aliases)} {key}" for key in keys], True
        else:
            yield name, previous.get(name, []), False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", help="ansible YAML inventory")
    parser.add_argument("--group", default="claude")
    parser.add_argument("--domain", default="")
    parser.add_argument("--previous", default="", help="earlier output to fall back on")
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args(argv)
    with open(args.inventory, encoding="utf-8") as handle:
        names = members(yaml.safe_load(handle), args.group)
    print(f">> host keys for {GITHUB} and {len(names)} {args.group} hosts", file=sys.stderr)
    found = entries(names, args.domain.strip("."), args.timeout)
    previous = previous_entries(args.previous) if args.previous else {}
    for done, (name, lines, fresh) in enumerate(render(found, previous), 1):
        state = "fresh" if fresh else ("kept previous" if lines else "unreachable, no keys")
        print(f"   [{done}/{len(found)}] {name}: {len(lines)} keys, {state}", file=sys.stderr)
        print("\n".join(lines), end="\n" if lines else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
