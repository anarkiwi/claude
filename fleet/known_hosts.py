#!/usr/bin/python3
"""Print a known_hosts for GitHub and every ssh host in an ansible inventory group.
Each host's address and port resolve as ansible resolves them, from the inventory,
group_vars and host_vars; its keys are recorded under the inventory name, the FQDN
and every address. A host that does not answer keeps its previous entries."""

import argparse
import collections
import json
import os
import shlex
import socket
import subprocess
import sys
import urllib.request

import yaml

KEY_TYPES = "ed25519,ecdsa,rsa"
GITHUB = "github.com"
GITHUB_META = "https://api.github.com/meta"
CONNECTION_VARS = (
    "ansible_connection",
    "ansible_host",
    "ansible_host_fallbacks",
    "ansible_port",
    "ansible_ssh_common_args",
    "ansible_ssh_extra_args",
)
SSH_CONNECTIONS = frozenset({"ssh", "paramiko", "paramiko_ssh", "smart", "network_cli", "libssh"})
VAR_SUFFIXES = ("", ".yml", ".yaml", ".json")
VAULT_HEADER = "$ANSIBLE_VAULT"

Host = collections.namedtuple("Host", "name address fallbacks port options", defaults=((),))


class Loader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """SafeLoader reading ansible's tags: !vault values are opaque, !unsafe ones literal."""


Loader.add_constructor("!vault", lambda loader, node: None)
Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))


def tree(inventory):
    """Return ({group: direct hosts}, {group: child groups}, {host: inline vars})."""
    hosts, children, inline = {}, {}, {}

    def walk(name, node):
        node = node or {}
        for host, hostvars in (node.get("hosts") or {}).items():
            hosts.setdefault(name, set()).add(host)
            inline.setdefault(host, {}).update(hostvars or {})
        for child, sub in (node.get("children") or {}).items():
            children.setdefault(name, set()).add(child)
            walk(child, sub)

    walk("all", (inventory or {}).get("all"))
    return hosts, children, inline


def depths(children):
    """Return {group: distance from all}, the order ansible applies group_vars in."""
    depth, frontier = {"all": 0}, ["all"]
    while frontier:
        nxt = []
        for group in frontier:
            for child in children.get(group, ()):
                if child not in depth:
                    depth[child] = depth[group] + 1
                    nxt.append(child)
        frontier = nxt
    return depth


def descendants(group, hosts, children):
    """Return every host in group or any group beneath it."""
    found, pending, seen = set(), [group], set()
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            found |= hosts.get(name, set())
            pending.extend(children.get(name, ()))
    return found


def var_files(base, name):
    """Return the vars files ansible loads for name under base, in load order."""
    paths = [os.path.join(base, name + suffix) for suffix in VAR_SUFFIXES]
    directory = os.path.join(base, name)
    if os.path.isdir(directory):
        paths += sorted(os.path.join(directory, entry) for entry in os.listdir(directory))
    return [
        path for path in paths if os.path.isfile(path) and os.path.splitext(path)[1] in VAR_SUFFIXES
    ]


def connection_vars(data):
    """Return the literal connection vars in data; templated values are left for ansible."""
    if not isinstance(data, dict):
        return {}
    return {
        key: data[key]
        for key in CONNECTION_VARS
        if data.get(key) is not None and "{{" not in json.dumps(data[key])
    }


def load_vars(paths):
    merged = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if not text.lstrip().startswith(VAULT_HEADER):
            merged.update(connection_vars(yaml.load(text, Loader)))
    return merged


def resolve_inventory(path):
    """Return ({group: member hosts}, {host: connection vars}) merged as ansible merges them."""
    with open(path, encoding="utf-8") as handle:
        hosts, children, inline = tree(yaml.load(handle, Loader))
    root = os.path.dirname(os.path.abspath(path))
    depth = depths(children)
    groups = sorted(depth, key=lambda name: (depth[name], name))
    members = {name: descendants(name, hosts, children) for name in groups}
    group_vars = [
        (members[name], load_vars(var_files(os.path.join(root, "group_vars"), name)))
        for name in groups
    ]
    merged = {}
    for host in members.get("all", ()):
        found = {}
        for group_members, values in group_vars:
            if host in group_members:
                found.update(values)
        found.update(connection_vars(inline.get(host)))
        found.update(load_vars(var_files(os.path.join(root, "host_vars"), host)))
        merged[host] = found
    return members, merged


def ssh_hosts(path, group):
    """Return a Host per member of group whose resolved connection is ssh."""
    members, merged = resolve_inventory(path)
    found = []
    for name in sorted(members.get(group, ())):
        values = merged[name]
        if str(values.get("ansible_connection") or "ssh").rsplit(".", 1)[-1] in SSH_CONNECTIONS:
            fallbacks = list(values.get("ansible_host_fallbacks") or [])
            port = int(values.get("ansible_port") or 22)
            args = " ".join(
                str(values.get(key) or "")
                for key in ("ansible_ssh_common_args", "ansible_ssh_extra_args")
            )
            options = ssh_options(args)
            found.append(Host(name, values.get("ansible_host"), fallbacks, port, options))
    return found


def ssh_options(args):
    """Return the (keyword, value) pairs of the -o options in an ssh argument string."""
    tokens, options = shlex.split(args), []
    for index, token in enumerate(tokens):
        option = tokens[index + 1] if token == "-o" and index + 1 < len(tokens) else ""
        option = token[2:] if token.startswith("-o") and len(token) > 2 else option
        keyword, _, value = option.replace("=", " ", 1).partition(" ")
        if keyword and value.strip():
            options.append((keyword, value.strip()))
    return tuple(options)


def addresses(name):
    try:
        infos = socket.getaddrinfo(name, 22, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def bracket(name, port):
    return name if port == 22 else f"[{name}]:{port}"


def unbracket(token):
    return token[1:].rsplit("]:", 1)[0] if token.startswith("[") else token


def keyscan(pairs, timeout):
    """Return {(target, port): sorted "type key" strings} for the pairs that answered."""
    by_port = collections.defaultdict(list)
    for target, port in dict.fromkeys(pairs):
        by_port[port].append(target)
    keys = {}
    for port, names in sorted(by_port.items()):
        proc = subprocess.run(
            ["ssh-keyscan", "-T", str(timeout), "-p", str(port), "-t", KEY_TYPES, *names],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in proc.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3 and not line.startswith("#"):
                key = (unbracket(fields[0]), port)
                keys.setdefault(key, set()).add(f"{fields[1]} {fields[2]}")
    return {target: sorted(found) for target, found in keys.items()}


def github_keys(timeout, url=GITHUB_META):
    """Return GitHub's published "type key" strings, or [] if they cannot be fetched."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return sorted(json.load(response)["ssh_keys"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def previous_entries(path):
    """Return {host name: [lines]} from an earlier output of this tool."""
    by_host = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return by_host
    for line in lines:
        if line.strip() and not line.startswith("#"):
            first = line.split(None, 1)[0].split(",", 1)[0]
            by_host.setdefault(unbracket(first), []).append(line)
    return by_host


def targets(host, domain):
    """Return the addresses ssh-keyscan should reach host at."""
    fqdn = f"{host.name}.{domain}" if domain else host.name
    return list(dict.fromkeys([host.address or fqdn, *host.fallbacks]))


def entries(hosts, domain, timeout, resolve=addresses):
    """Return {name: (aliases, keys)} for GitHub and each host; keys may be empty."""
    scanned = keyscan([(t, h.port) for h in hosts for t in targets(h, domain)], timeout)
    found = {GITHUB: ([GITHUB], github_keys(timeout))}
    for host in hosts:
        reach = targets(host, domain)
        keys = sorted({key for t in reach for key in scanned.get((t, host.port), [])})
        ips = {ip for t in reach for ip in (resolve(t) if keys else ())}
        names = [host.name, *reach, *sorted(ips)]
        fqdn = f"{host.name}.{domain}" if domain else ""
        if keys and fqdn and ips & set(resolve(fqdn)):
            names.insert(1, fqdn)
        found[host.name] = ([bracket(n, host.port) for n in dict.fromkeys(names)], keys)
    return found


def render(found, previous):
    """Yield (name, lines, fresh) per host: fresh keys, else the previous lines."""
    for name, (aliases, keys) in found.items():
        if keys:
            yield name, [f"{','.join(aliases)} {key}" for key in keys], True
        else:
            yield name, previous.get(name, []), False


def ssh_config(hosts):
    """Return ssh_config Host blocks connecting each inventory name as ansible connects."""
    blocks = []
    for host in hosts:
        address = host.address if host.address != host.name else None
        lines = [f"    HostName {address}"] if address else []
        lines += [f"    Port {host.port}"] if host.port != 22 else []
        lines += [f"    {keyword} {value}" for keyword, value in host.options]
        if lines:
            patterns = [host.name, address] if address else [host.name]
            blocks.append("\n".join([f"Host {' '.join(patterns)}", *lines]))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", help="ansible YAML inventory, beside its *_vars dirs")
    parser.add_argument("--group", default="all")
    parser.add_argument("--domain", default="")
    parser.add_argument("--previous", default="", help="earlier output to fall back on")
    parser.add_argument("--ssh-config", default="", help="also write Host blocks here")
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args(argv)
    hosts = ssh_hosts(args.inventory, args.group)
    print(f">> host keys for {GITHUB} and {len(hosts)} {args.group} hosts", file=sys.stderr)
    found = entries(hosts, args.domain.strip("."), args.timeout)
    previous = previous_entries(args.previous) if args.previous else {}
    for done, (name, lines, fresh) in enumerate(render(found, previous), 1):
        state = "fresh" if fresh else ("kept previous" if lines else "unreachable, no keys")
        print(f"   [{done}/{len(found)}] {name}: {len(lines)} keys, {state}", file=sys.stderr)
        print("\n".join(lines), end="\n" if lines else "")
    if args.ssh_config:
        with open(args.ssh_config, "w", encoding="utf-8") as handle:
            handle.write(ssh_config(hosts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
