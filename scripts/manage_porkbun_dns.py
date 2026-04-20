#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_API_BASE = "https://api.porkbun.com/api/json/v3"
PORKBUN_API_KEY_ENV = "PORKBUN_API_KEY"
PORKBUN_SECRET_API_KEY_ENV = "PORKBUN_SECRET_API_KEY"

SUPPORTED_RECORD_TYPES = {
    "A",
    "AAAA",
    "ALIAS",
    "CAA",
    "CNAME",
    "HTTPS",
    "MX",
    "NS",
    "SRV",
    "SSHFP",
    "SVCB",
    "TLSA",
    "TXT",
}


class PorkbunApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class PorkbunCredentials:
    api_key: str
    secret_api_key: str


def to_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def read_credentials(args: argparse.Namespace) -> PorkbunCredentials:
    api_key = str(args.api_key or os.environ.get(PORKBUN_API_KEY_ENV) or "").strip()
    secret_api_key = str(args.secret_api_key or os.environ.get(PORKBUN_SECRET_API_KEY_ENV) or "").strip()
    if not api_key or not secret_api_key:
        raise SystemExit(
            f"Set {PORKBUN_API_KEY_ENV} and {PORKBUN_SECRET_API_KEY_ENV}, "
            "or pass --api-key and --secret-api-key."
        )
    return PorkbunCredentials(api_key=api_key, secret_api_key=secret_api_key)


def normalized_api_base(value: str) -> str:
    return value.rstrip("/")


def endpoint_url(api_base: str, *segments: str) -> str:
    quoted = "/".join(urllib.parse.quote(str(segment), safe="") for segment in segments if str(segment) != "")
    return f"{normalized_api_base(api_base)}/{quoted}"


def parse_response(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as error:
        raise PorkbunApiError(f"Porkbun returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise PorkbunApiError("Porkbun returned an unexpected response shape.")
    return payload


def describe_api_failure(payload: dict[str, Any], fallback: str) -> str:
    message = str(payload.get("message") or "").strip()
    code = str(payload.get("code") or "").strip()
    if message and code:
        return f"{message} ({code})"
    return message or code or fallback


def porkbun_post(
    api_base: str,
    path_segments: list[str],
    credentials: PorkbunCredentials,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "apikey": credentials.api_key,
        "secretapikey": credentials.secret_api_key,
        **(payload or {}),
    }
    request = urllib.request.Request(
        endpoint_url(api_base, *path_segments),
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = parse_response(response.read())
    except urllib.error.HTTPError as error:
        parsed = parse_response(error.read())
        raise PorkbunApiError(describe_api_failure(parsed, f"Porkbun request failed with HTTP {error.code}.")) from error
    except urllib.error.URLError as error:
        raise PorkbunApiError(f"Porkbun request failed: {error.reason}") from error

    if parsed.get("status") == "ERROR":
        raise PorkbunApiError(describe_api_failure(parsed, "Porkbun returned an error."))
    return parsed


def normalize_record_type(value: str) -> str:
    record_type = value.strip().upper()
    if record_type not in SUPPORTED_RECORD_TYPES:
        raise SystemExit(f"Unsupported DNS record type: {value}")
    return record_type


def normalize_record_name(domain: str, name: str) -> str:
    trimmed = name.strip().rstrip(".")
    clean_domain = domain.strip().rstrip(".")
    if not trimmed or trimmed == "@":
        return clean_domain
    if trimmed == "*":
        return f"*.{clean_domain}"
    if trimmed.endswith(f".{clean_domain}") or trimmed == clean_domain:
        return trimmed
    return f"{trimmed}.{clean_domain}"


def record_subdomain(domain: str, name: str) -> str:
    trimmed = name.strip().rstrip(".")
    clean_domain = domain.strip().rstrip(".")
    if not trimmed or trimmed == "@" or trimmed == clean_domain:
        return ""
    if trimmed.endswith(f".{clean_domain}"):
        return trimmed[: -(len(clean_domain) + 1)]
    return trimmed


def read_int(value: str | None, label: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise SystemExit(f"{label} must be an integer.") from error
    if parsed < 0:
        raise SystemExit(f"{label} cannot be negative.")
    return parsed


def record_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": normalize_record_type(args.type),
        "content": args.content,
    }
    name = record_subdomain(args.domain, args.name)
    if name:
        payload["name"] = name
    else:
        payload["name"] = ""

    ttl = read_int(args.ttl, "TTL")
    if ttl is not None:
        payload["ttl"] = ttl

    prio = read_int(args.prio, "Priority")
    if prio is not None:
        payload["prio"] = prio

    if args.notes is not None:
        payload["notes"] = args.notes

    return payload


def list_domains(api_base: str, credentials: PorkbunCredentials, include_labels: bool = False) -> list[dict[str, Any]]:
    domains: list[dict[str, Any]] = []
    start = 0
    while True:
        payload = porkbun_post(
            api_base,
            ["domain", "listAll"],
            credentials,
            {"start": start, "includeLabels": "yes" if include_labels else "no"},
        )
        page = payload.get("domains")
        if not isinstance(page, list):
            raise PorkbunApiError("Porkbun domain list response did not include a domains array.")
        domains.extend(entry for entry in page if isinstance(entry, dict))
        if len(page) < 1000:
            return domains
        start += 1000


def record_matches(record: dict[str, Any], domain: str, record_type: str | None, name: str | None) -> bool:
    if record_type and str(record.get("type") or "").upper() != record_type:
        return False
    if name is None:
        return True
    return str(record.get("name") or "").rstrip(".") == normalize_record_name(domain, name)


def list_records(
    api_base: str,
    credentials: PorkbunCredentials,
    domain: str,
    record_type: str | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    payload = porkbun_post(api_base, ["dns", "retrieve", domain], credentials)
    records = payload.get("records")
    if not isinstance(records, list):
        raise PorkbunApiError("Porkbun DNS response did not include a records array.")
    normalized_type = normalize_record_type(record_type) if record_type else None
    return [
        record
        for record in records
        if isinstance(record, dict) and record_matches(record, domain, normalized_type, name)
    ]


def create_record(api_base: str, credentials: PorkbunCredentials, args: argparse.Namespace) -> dict[str, Any]:
    return porkbun_post(api_base, ["dns", "create", args.domain], credentials, record_payload(args))


def edit_record(api_base: str, credentials: PorkbunCredentials, args: argparse.Namespace) -> dict[str, Any]:
    return porkbun_post(api_base, ["dns", "edit", args.domain, args.record_id], credentials, record_payload(args))


def delete_record(api_base: str, credentials: PorkbunCredentials, args: argparse.Namespace) -> dict[str, Any]:
    return porkbun_post(api_base, ["dns", "delete", args.domain, args.record_id], credentials)


def render_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No DNS records matched."
    lines = ["ID\tTYPE\tNAME\tCONTENT\tTTL\tPRIO\tNOTES"]
    for record in records:
        lines.append(
            "\t".join(
                [
                    str(record.get("id") or ""),
                    str(record.get("type") or ""),
                    str(record.get("name") or ""),
                    str(record.get("content") or ""),
                    str(record.get("ttl") or ""),
                    str(record.get("prio") or ""),
                    str(record.get("notes") or ""),
                ]
            )
        )
    return "\n".join(lines)


def render_domains(domains: list[dict[str, Any]]) -> str:
    if not domains:
        return "No Porkbun domains were returned."
    lines = ["DOMAIN\tSTATUS\tEXPIRES\tAUTO_RENEW"]
    for domain in domains:
        lines.append(
            "\t".join(
                [
                    str(domain.get("domain") or ""),
                    str(domain.get("status") or ""),
                    str(domain.get("expireDate") or ""),
                    str(domain.get("autoRenew") or ""),
                ]
            )
        )
    return "\n".join(lines)


def print_result(payload: dict[str, Any], as_json: bool, fallback_message: str) -> None:
    if as_json:
        print(to_json(payload))
        return
    print(fallback_message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Porkbun DNS records.")
    parser.add_argument("--api-key", default="", help=f"Porkbun API key. Defaults to ${PORKBUN_API_KEY_ENV}.")
    parser.add_argument(
        "--secret-api-key",
        default="",
        help=f"Porkbun secret API key. Defaults to ${PORKBUN_SECRET_API_KEY_ENV}.",
    )
    parser.add_argument(
        "--api-base",
        default=os.environ.get("PORKBUN_API_BASE_URL", DEFAULT_API_BASE),
        help=f"Porkbun API base URL (default: {DEFAULT_API_BASE}).",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_json_alias(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    ping_parser = subparsers.add_parser("ping", help="Validate credentials and print the caller IP.")
    add_json_alias(ping_parser)
    domains_parser = subparsers.add_parser("domains", help="List domains in the Porkbun account.")
    add_json_alias(domains_parser)

    list_parser = subparsers.add_parser("list", help="List DNS records for a domain.")
    list_parser.add_argument("domain", help="Domain name.")
    list_parser.add_argument("--type", default="", help="Optional DNS record type filter.")
    list_parser.add_argument("--name", default=None, help="Optional root, subdomain, wildcard, or FQDN filter.")
    add_json_alias(list_parser)

    def add_record_fields(record_parser: argparse.ArgumentParser) -> None:
        record_parser.add_argument("domain", help="Domain name.")
        record_parser.add_argument("--type", required=True, help="DNS record type.")
        record_parser.add_argument("--name", default="", help="Root, subdomain, wildcard, or FQDN.")
        record_parser.add_argument("--content", required=True, help="Record content/value.")
        record_parser.add_argument("--ttl", default=None, help="TTL in seconds. Porkbun defaults to 600 when omitted.")
        record_parser.add_argument("--prio", default=None, help="Priority for MX/SRV-style records.")
        record_parser.add_argument("--notes", default=None, help="Optional Porkbun record notes.")

    create_parser = subparsers.add_parser("create", help="Create a DNS record.")
    add_record_fields(create_parser)
    add_json_alias(create_parser)

    edit_parser = subparsers.add_parser("edit", help="Edit a DNS record by Porkbun record ID.")
    edit_parser.add_argument("domain", help="Domain name.")
    edit_parser.add_argument("record_id", help="Porkbun DNS record ID.")
    edit_parser.add_argument("--type", required=True, help="DNS record type.")
    edit_parser.add_argument("--name", default="", help="Root, subdomain, wildcard, or FQDN.")
    edit_parser.add_argument("--content", required=True, help="Record content/value.")
    edit_parser.add_argument("--ttl", default=None, help="TTL in seconds.")
    edit_parser.add_argument("--prio", default=None, help="Priority for MX/SRV-style records.")
    edit_parser.add_argument("--notes", default=None, help="Optional Porkbun record notes.")
    add_json_alias(edit_parser)

    delete_parser = subparsers.add_parser("delete", help="Delete a DNS record by Porkbun record ID.")
    delete_parser.add_argument("domain", help="Domain name.")
    delete_parser.add_argument("record_id", help="Porkbun DNS record ID.")
    add_json_alias(delete_parser)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    credentials = read_credentials(args)
    api_base = normalized_api_base(args.api_base)

    try:
        if args.command == "ping":
            payload = porkbun_post(api_base, ["ping"], credentials)
            print_result({"action": "ping", **payload}, args.json, f"Porkbun credentials are valid. IP: {payload.get('yourIp') or 'n/a'}")
            return

        if args.command == "domains":
            domains = list_domains(api_base, credentials)
            if args.json:
                print(to_json({"action": "domains", "domains": domains}))
            else:
                print(render_domains(domains))
            return

        if args.command == "list":
            records = list_records(api_base, credentials, args.domain, record_type=args.type or None, name=args.name)
            if args.json:
                print(to_json({"action": "list", "domain": args.domain, "records": records}))
            else:
                print(render_records(records))
            return

        if args.command == "create":
            response = create_record(api_base, credentials, args)
            print_result(
                {"action": "create", "domain": args.domain, **response},
                args.json,
                f"Created {normalize_record_type(args.type)} record for {normalize_record_name(args.domain, args.name)}.",
            )
            return

        if args.command == "edit":
            response = edit_record(api_base, credentials, args)
            print_result(
                {"action": "edit", "domain": args.domain, "id": args.record_id, **response},
                args.json,
                f"Updated DNS record {args.record_id} for {args.domain}.",
            )
            return

        if args.command == "delete":
            response = delete_record(api_base, credentials, args)
            print_result(
                {"action": "delete", "domain": args.domain, "id": args.record_id, **response},
                args.json,
                f"Deleted DNS record {args.record_id} from {args.domain}.",
            )
            return

        raise SystemExit(f"Unsupported command: {args.command}")
    except PorkbunApiError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
