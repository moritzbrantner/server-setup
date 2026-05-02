#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from registry_contract import DEFAULT_REGISTRY_PATH, load_registry


class DnsError(ValueError):
    """Raised when DNS provider configuration or API data is invalid."""


@dataclass(frozen=True)
class DomainTarget:
    site_name: str
    domain: str
    provider: str
    zone: str


JsonRecord = dict[str, Any]
NAMECHEAP_REPLACE_WARNING = "Namecheap updates replace the full host list for the zone."


def json_response(payload: JsonRecord) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def as_object(value: Any) -> JsonRecord:
    return value if isinstance(value, dict) else {}


def pick_string(record: JsonRecord, key: str) -> str:
    value = record.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def resolve_registry_path(explicit: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("REGISTRY_PATH") or os.environ.get("STATUS_CONFIG_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_REGISTRY_PATH


def merged_site(entry: JsonRecord) -> JsonRecord:
    deploy_config = as_object(entry.get("deploy_config"))
    return {
        **deploy_config,
        **entry,
        "dns": {
            **as_object(deploy_config.get("dns")),
            **as_object(entry.get("dns")),
        },
    }


def load_domain_target(site_name: str, registry_path: Path) -> DomainTarget:
    for raw_entry in load_registry(registry_path):
        site = merged_site(as_object(raw_entry))
        if pick_string(site, "name") != site_name:
            continue

        domain = pick_string(site, "domain")
        dns = as_object(site.get("dns"))
        provider = pick_string(dns, "provider").lower()
        zone = pick_string(dns, "zone") or domain
        if not domain:
            raise DnsError(f"Site '{site_name}' does not define a domain.")
        if provider not in {"namecheap", "porkbun"}:
            raise DnsError(f"Site '{site_name}' does not configure dns.provider.")
        if not zone or "." not in zone:
            raise DnsError(f"Site '{site_name}' does not configure a valid dns.zone.")
        return DomainTarget(site_name=site_name, domain=domain, provider=provider, zone=zone.lower())

    raise DnsError(f"No registry entry named '{site_name}' was found.")


def read_required_env(*names: str) -> list[str]:
    values: list[str] = []
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            missing.append(name)
        values.append(value)
    if missing:
        raise DnsError(f"Missing required environment variables: {', '.join(missing)}")
    return values


def normalize_record_name(name: str) -> str:
    cleaned = name.strip().rstrip(".")
    return cleaned or "@"


def normalize_ttl(value: str | int | None) -> int:
    if value in (None, ""):
        return 600
    try:
        ttl = int(value)
    except (TypeError, ValueError) as exc:
        raise DnsError("TTL must be numeric.") from exc
    if ttl < 0:
        raise DnsError("TTL must be zero or greater.")
    return ttl


def normalize_prio(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        prio = int(value)
    except (TypeError, ValueError) as exc:
        raise DnsError("Priority must be numeric.") from exc
    if prio < 0:
        raise DnsError("Priority must be zero or greater.")
    return prio


def build_record(record_id: str, record_type: str, name: str, content: str, ttl: str | int | None, prio: str | int | None) -> JsonRecord:
    record_type = record_type.strip().upper()
    if not record_type:
        raise DnsError("Record type is required.")
    if not content.strip():
        raise DnsError("Record content is required.")

    record: JsonRecord = {
        "id": record_id.strip(),
        "type": record_type,
        "name": normalize_record_name(name),
        "content": content.strip(),
        "ttl": normalize_ttl(ttl),
        "prio": normalize_prio(prio),
    }
    return record


def relative_name(full_name: str, zone: str) -> str:
    cleaned = full_name.strip().rstrip(".")
    zone = zone.rstrip(".")
    if not cleaned or cleaned == zone:
        return "@"
    suffix = f".{zone}"
    if cleaned.endswith(suffix):
        return cleaned[: -len(suffix)] or "@"
    return cleaned


def fqdn_name(name: str, zone: str) -> str:
    relative = normalize_record_name(name)
    return zone if relative == "@" else f"{relative}.{zone}"


def summarize_document(target: DomainTarget, records: list[JsonRecord]) -> JsonRecord:
    return {
        "siteName": target.site_name,
        "domain": target.domain,
        "provider": target.provider,
        "zone": target.zone,
        "records": records,
    }


class PorkbunClient:
    def __init__(self) -> None:
        self.api_key, self.secret_api_key = read_required_env("PORKBUN_API_KEY", "PORKBUN_SECRET_API_KEY")
        self.base_url = os.environ.get("PORKBUN_API_BASE_URL", "https://api.porkbun.com/api/json/v3").rstrip("/")

    def request(self, path: str, *, method: str = "POST", body: JsonRecord | None = None) -> JsonRecord:
        url = f"{self.base_url}{path}"
        headers = {"accept": "application/json"}
        payload = {"apikey": self.api_key, "secretapikey": self.secret_api_key, **(body or {})}
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                parsed = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DnsError(f"Porkbun API request failed with HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise DnsError(f"Porkbun API request failed: {exc}") from exc

        if not isinstance(parsed, dict):
            raise DnsError("Porkbun API returned an unexpected response.")
        if parsed.get("status") == "ERROR":
            message = parsed.get("message") or parsed.get("code") or "Porkbun API returned an error."
            raise DnsError(str(message))
        return parsed

    def list_records(self, target: DomainTarget) -> list[JsonRecord]:
        payload = self.request(f"/dns/retrieve/{urllib.parse.quote(target.zone)}")
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raw_records = []
        records: list[JsonRecord] = []
        for raw in raw_records:
            record = as_object(raw)
            records.append(
                {
                    "id": str(record.get("id") or ""),
                    "type": str(record.get("type") or "").upper(),
                    "name": relative_name(str(record.get("name") or ""), target.zone),
                    "content": str(record.get("content") or ""),
                    "ttl": normalize_ttl(record.get("ttl") or 0),
                    "prio": normalize_prio(record.get("prio")),
                }
            )
        return records

    def create_record(self, target: DomainTarget, record: JsonRecord) -> list[JsonRecord]:
        body: JsonRecord = {
            "type": record["type"],
            "name": "" if record["name"] == "@" else record["name"],
            "content": record["content"],
            "ttl": record["ttl"],
        }
        if record["prio"] is not None:
            body["prio"] = record["prio"]
        self.request(f"/dns/create/{urllib.parse.quote(target.zone)}", body=body)
        return self.list_records(target)

    def update_record(self, target: DomainTarget, record: JsonRecord) -> list[JsonRecord]:
        record_id = urllib.parse.quote(str(record["id"]))
        body: JsonRecord = {
            "type": record["type"],
            "name": "" if record["name"] == "@" else record["name"],
            "content": record["content"],
            "ttl": record["ttl"],
        }
        if record["prio"] is not None:
            body["prio"] = record["prio"]
        self.request(f"/dns/edit/{urllib.parse.quote(target.zone)}/{record_id}", body=body)
        return self.list_records(target)

    def delete_record(self, target: DomainTarget, record_id: str) -> list[JsonRecord]:
        self.request(f"/dns/delete/{urllib.parse.quote(target.zone)}/{urllib.parse.quote(record_id)}")
        return self.list_records(target)


def split_namecheap_zone(zone: str) -> tuple[str, str]:
    sld, separator, tld = zone.partition(".")
    if not separator or not sld or not tld:
        raise DnsError("Namecheap dns.zone must include both SLD and TLD, for example example.com.")
    return sld, tld


def namecheap_record_id(index: int, attrs: JsonRecord) -> str:
    host_id = str(attrs.get("HostId") or attrs.get("HostID") or "").strip()
    return host_id or f"index-{index}"


class NamecheapClient:
    def __init__(self) -> None:
        self.api_user, self.api_key, self.client_ip = read_required_env(
            "NAMECHEAP_API_USER",
            "NAMECHEAP_API_KEY",
            "NAMECHEAP_CLIENT_IP",
        )
        self.username = os.environ.get("NAMECHEAP_USERNAME", self.api_user).strip() or self.api_user
        default_base = (
            "https://api.sandbox.namecheap.com/xml.response"
            if os.environ.get("NAMECHEAP_SANDBOX", "").lower() in {"1", "true", "yes"}
            else "https://api.namecheap.com/xml.response"
        )
        self.base_url = os.environ.get("NAMECHEAP_API_BASE_URL", default_base).strip() or default_base

    def request(self, command: str, zone: str, extra: JsonRecord | None = None, *, post: bool = False) -> ET.Element:
        sld, tld = split_namecheap_zone(zone)
        params: JsonRecord = {
            "ApiUser": self.api_user,
            "ApiKey": self.api_key,
            "UserName": self.username,
            "ClientIp": self.client_ip,
            "Command": command,
            "SLD": sld,
            "TLD": tld,
            **(extra or {}),
        }
        encoded = urllib.parse.urlencode(params)
        url = self.base_url
        data = None
        if post:
            data = encoded.encode("utf-8")
        else:
            url = f"{self.base_url}?{encoded}"

        request = urllib.request.Request(
            url,
            data=data,
            headers={"content-type": "application/x-www-form-urlencoded"},
            method="POST" if post else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DnsError(f"Namecheap API request failed with HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise DnsError(f"Namecheap API request failed: {exc}") from exc

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise DnsError("Namecheap API returned invalid XML.") from exc

        errors = [
            "".join(element.itertext()).strip()
            for element in root.iter()
            if element.tag.endswith("Error") and "".join(element.itertext()).strip()
        ]
        if root.attrib.get("Status") == "ERROR" or errors:
            raise DnsError("; ".join(errors) or "Namecheap API returned an error.")
        return root

    def list_records(self, target: DomainTarget) -> list[JsonRecord]:
        root = self.request("namecheap.domains.dns.getHosts", target.zone)
        records: list[JsonRecord] = []
        index = 1
        for element in root.iter():
            if not element.tag.lower().endswith("host"):
                continue
            attrs: JsonRecord = dict(element.attrib)
            records.append(self.normalized_host(index, attrs))
            index += 1
        return records

    def normalized_host(self, index: int, attrs: JsonRecord) -> JsonRecord:
        return {
            "id": namecheap_record_id(index, attrs),
            "type": str(attrs.get("Type") or "").upper(),
            "name": normalize_record_name(str(attrs.get("Name") or "")),
            "content": str(attrs.get("Address") or ""),
            "ttl": normalize_ttl(attrs.get("TTL") or 1800),
            "prio": normalize_prio(attrs.get("MXPref")),
            "flag": str(attrs.get("Flag") or "").strip() or None,
            "tag": str(attrs.get("Tag") or "").strip() or None,
        }

    def set_records(self, target: DomainTarget, records: list[JsonRecord]) -> list[JsonRecord]:
        params: JsonRecord = {}
        for index, record in enumerate(records, start=1):
            params[f"HostName{index}"] = record["name"]
            params[f"RecordType{index}"] = record["type"]
            params[f"Address{index}"] = record["content"]
            params[f"TTL{index}"] = str(record.get("ttl") or 1800)
            if record.get("prio") is not None:
                params[f"MXPref{index}"] = str(record["prio"])
            if record.get("flag"):
                params[f"Flag{index}"] = str(record["flag"])
            if record.get("tag"):
                params[f"Tag{index}"] = str(record["tag"])

        root = self.request("namecheap.domains.dns.setHosts", target.zone, params, post=True)
        for element in root.iter():
            if element.tag.endswith("DomainDNSSetHostsResult"):
                if element.attrib.get("IsSuccess", "").lower() == "true":
                    return self.list_records(target)
        raise DnsError("Namecheap did not confirm that DNS records were updated.")

    def create_record(self, target: DomainTarget, record: JsonRecord) -> list[JsonRecord]:
        records = self.list_records(target)
        records.append(record)
        return self.set_records(target, records)

    def update_record(self, target: DomainTarget, record: JsonRecord) -> list[JsonRecord]:
        records = self.list_records(target)
        updated = False
        next_records: list[JsonRecord] = []
        for existing in records:
            if existing["id"] == record["id"]:
                next_records.append({**existing, **record})
                updated = True
            else:
                next_records.append(existing)
        if not updated:
            raise DnsError(f"DNS record '{record['id']}' was not found.")
        return self.set_records(target, next_records)

    def delete_record(self, target: DomainTarget, record_id: str) -> list[JsonRecord]:
        records = self.list_records(target)
        next_records = [record for record in records if record["id"] != record_id]
        if len(next_records) == len(records):
            raise DnsError(f"DNS record '{record_id}' was not found.")
        return self.set_records(target, next_records)


def provider_client(provider: str) -> PorkbunClient | NamecheapClient:
    if provider == "porkbun":
        return PorkbunClient()
    if provider == "namecheap":
        return NamecheapClient()
    raise DnsError(f"Unsupported DNS provider: {provider}")


def find_record(records: list[JsonRecord], record_id: str) -> JsonRecord:
    for record in records:
        if str(record.get("id") or "") == record_id:
            return record
    raise DnsError(f"DNS record '{record_id}' was not found.")


def proposed_record_change(action: str, before: list[JsonRecord], record: JsonRecord | None = None, record_id: str = "") -> tuple[list[JsonRecord], JsonRecord]:
    if action == "create":
        if record is None:
            raise DnsError("Record payload is required for create.")
        after = [*before, record]
        return after, {"created": [record], "updated": [], "deleted": []}

    if action == "update":
        if record is None:
            raise DnsError("Record payload is required for update.")
        existing = find_record(before, str(record["id"]))
        updated = {**existing, **record}
        after = [updated if str(item.get("id") or "") == str(record["id"]) else item for item in before]
        return after, {"created": [], "updated": [{"before": existing, "after": updated}], "deleted": []}

    if action == "delete":
        existing = find_record(before, record_id)
        after = [item for item in before if str(item.get("id") or "") != record_id]
        return after, {"created": [], "updated": [], "deleted": [existing]}

    raise DnsError("Unsupported DNS action.")


def mutation_payload(
    action: str,
    target: DomainTarget,
    records: list[JsonRecord],
    *,
    message: str,
    dry_run: bool,
    before: list[JsonRecord] | None = None,
    after: list[JsonRecord] | None = None,
    diff: JsonRecord | None = None,
    warning: str = "",
) -> JsonRecord:
    payload: JsonRecord = {
        "action": action,
        "message": message,
        "dryRun": dry_run,
        **summarize_document(target, records),
    }
    if before is not None and after is not None:
        payload["before"] = before
        payload["after"] = after
    if diff:
        payload.update(diff)
    if warning:
        payload["warning"] = warning
    return payload


def add_record_args(parser: argparse.ArgumentParser, *, include_id: bool = False) -> None:
    if include_id:
        parser.add_argument("--id", required=True, help="Provider record ID to update.")
    parser.add_argument("--type", required=True, help="DNS record type, for example A, CNAME, MX, or TXT.")
    parser.add_argument("--name", required=True, help="Record host relative to dns.zone. Use @ for the zone apex.")
    parser.add_argument("--content", required=True, help="Record value.")
    parser.add_argument("--ttl", default="600", help="Record TTL in seconds.")
    parser.add_argument("--prio", default="", help="Optional MX/SRV priority.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Namecheap or Porkbun DNS records for a registry site.")
    parser.add_argument("--registry", default="", help="Path to deploy/registry.json. Defaults to REGISTRY_PATH.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    list_parser = subparsers.add_parser("list", help="List DNS records for a managed site.")
    list_parser.add_argument("--site", required=True)

    create_parser = subparsers.add_parser("create", help="Create a DNS record.")
    create_parser.add_argument("--site", required=True)
    create_parser.add_argument("--dry-run", action="store_true", help="Preview the DNS change without writing it.")
    add_record_args(create_parser)

    update_parser = subparsers.add_parser("update", help="Update a DNS record by provider ID.")
    update_parser.add_argument("--site", required=True)
    update_parser.add_argument("--dry-run", action="store_true", help="Preview the DNS change without writing it.")
    add_record_args(update_parser, include_id=True)

    delete_parser = subparsers.add_parser("delete", help="Delete a DNS record by provider ID.")
    delete_parser.add_argument("--site", required=True)
    delete_parser.add_argument("--id", required=True)
    delete_parser.add_argument("--dry-run", action="store_true", help="Preview the DNS change without writing it.")
    return parser.parse_args()


def run(args: argparse.Namespace) -> JsonRecord:
    target = load_domain_target(args.site, resolve_registry_path(args.registry))
    client = provider_client(target.provider)

    if args.action == "list":
        records = client.list_records(target)
        return {"action": "list", **summarize_document(target, records)}

    if args.action == "create":
        record = build_record("", args.type, args.name, args.content, args.ttl, args.prio)
        message = f"Created {record['type']} record {record['name']} in {target.zone}."
        if args.dry_run or target.provider == "namecheap":
            before = client.list_records(target)
            after, diff = proposed_record_change("create", before, record)
            records = after if args.dry_run else client.set_records(target, after)
            return mutation_payload(
                "create",
                target,
                records,
                message=f"Would create {record['type']} record {record['name']} in {target.zone}." if args.dry_run else message,
                dry_run=args.dry_run,
                before=before,
                after=records,
                diff=diff,
                warning=NAMECHEAP_REPLACE_WARNING if target.provider == "namecheap" else "",
            )
        records = client.create_record(target, record)
        return mutation_payload("create", target, records, message=message, dry_run=False)

    if args.action == "update":
        record = build_record(args.id, args.type, args.name, args.content, args.ttl, args.prio)
        message = f"Updated {record['type']} record {record['name']} in {target.zone}."
        if args.dry_run or target.provider == "namecheap":
            before = client.list_records(target)
            after, diff = proposed_record_change("update", before, record)
            records = after if args.dry_run else client.set_records(target, after)
            return mutation_payload(
                "update",
                target,
                records,
                message=f"Would update {record['type']} record {record['name']} in {target.zone}." if args.dry_run else message,
                dry_run=args.dry_run,
                before=before,
                after=records,
                diff=diff,
                warning=NAMECHEAP_REPLACE_WARNING if target.provider == "namecheap" else "",
            )
        records = client.update_record(target, record)
        return mutation_payload("update", target, records, message=message, dry_run=False)

    if args.action == "delete":
        message = f"Deleted DNS record {args.id} from {target.zone}."
        if args.dry_run or target.provider == "namecheap":
            before = client.list_records(target)
            after, diff = proposed_record_change("delete", before, record_id=args.id)
            records = after if args.dry_run else client.set_records(target, after)
            return mutation_payload(
                "delete",
                target,
                records,
                message=f"Would delete DNS record {args.id} from {target.zone}." if args.dry_run else message,
                dry_run=args.dry_run,
                before=before,
                after=records,
                diff=diff,
                warning=NAMECHEAP_REPLACE_WARNING if target.provider == "namecheap" else "",
            )
        records = client.delete_record(target, args.id)
        return mutation_payload("delete", target, records, message=message, dry_run=False)

    raise DnsError("Unsupported DNS action.")


def main() -> None:
    args = parse_args()
    try:
        payload = run(args)
        if args.json:
            json_response(payload)
        else:
            if payload.get("warning"):
                print(payload["warning"])
            print(payload.get("message") or f"{len(payload.get('records', []))} DNS records.")
    except DnsError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
