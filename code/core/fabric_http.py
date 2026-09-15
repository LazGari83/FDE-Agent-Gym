"""
fabric_http.py — the HTTP plumbing every Fabric REST client in this folder shares.

The requests-plane plumbing that OUTLIVED the SDK migration: the two error-body-preserving
raisers (one family — ``raise_for_status_fabric`` for the Fabric plane,
``raise_for_status_arm`` for ARM — called by these names, unaliased), the JSON
Content-Type header motif, base64 definition-part encode/decode, transport-level retry,
and the paginator that follows either of Fabric's two continuation conventions.

Its constituency after the migration: the validation check families' own type-specific
reads, ``livy_client`` and ``key_vault`` (non-SDK planes), the rebased clients' few
``# SDK gap`` direct calls, and ``encode_part``/``decode_parts`` everywhere definitions
are assembled or read. The 202 LRO poller (``handle_lro``) and ``item_url`` are gone —
LRO polling now lives in azure-core and the seam's ``sdk.lro_result``.

Deliberately NOT consolidated here: ``github_client._raise_for_status_github`` (GitHub's
error body has a different shape).
"""
import base64
import json
import time

import requests

from auth import get_headers


def raise_for_status_fabric(resp: requests.Response) -> requests.Response:
    """raise_for_status that keeps the Fabric error body — the part that says what was wrong."""
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except ValueError:  # non-JSON body — fall back to the stock behaviour
            body = None
        if not isinstance(body, dict):
            resp.raise_for_status()
            return resp
        detail = "; ".join(d.get("message", "") for d in body.get("moreDetails", [])) \
            or body.get("message", resp.text[:400])
        raise requests.HTTPError(
            f"{resp.status_code} {body.get('errorCode', '')}: {detail}", response=resp)
    return resp


def raise_for_status_arm(resp: requests.Response) -> requests.Response:
    """raise_for_status that keeps the ARM error body — the part that says what was wrong.

    ARM (and the Key Vault data plane, which shares the shape) nests its code and message
    under an ``error`` object. Shared by every ARM-plane caller — capacity, key_vault —
    so an ``AuthorizationFailed`` names itself instead of surfacing as a bare 403.
    """
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        if isinstance(err, dict) and err:
            raise requests.HTTPError(
                f"{resp.status_code} {err.get('code', '')}: "
                f"{err.get('message', resp.text[:300])}", response=resp)
        resp.raise_for_status()
    return resp


def json_headers() -> dict:
    """Fabric auth headers plus the JSON Content-Type a POST/PATCH body needs."""
    return {**get_headers(), "Content-Type": "application/json"}


def retry_after_seconds(headers, default: float) -> float:
    """Seconds to wait per a Retry-After header, or `default` when absent/unparseable.

    Handles both RFC 7231 forms: delta-seconds and an HTTP-date. The date form is
    differenced against the response's own Date header (the server's clock), falling back
    to local time when Date is absent.
    """
    lower = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = lower.get("retry-after")
    if raw is None:
        return default
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(str(raw))
        issued = parsedate_to_datetime(str(lower["date"])) if lower.get("date") \
            else datetime.now(timezone.utc)
        return max(0.0, (when - issued).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return default


def decode_parts(raw: dict) -> dict:
    """``{part path: decoded content}`` from a getDefinition response body — JSON parts
    decoded to dicts, anything undecodable kept as replacement-decoded text."""
    parts = raw.get("definition", {}).get("parts", [])
    decoded = {}
    for part in parts:
        blob = base64.b64decode(part.get("payload", ""))
        try:
            decoded[part["path"]] = json.loads(blob)
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded[part["path"]] = blob.decode("utf-8", errors="replace")
    return decoded


def encode_part(path: str, content, *, indent: int | None = None) -> dict:
    """One InlineBase64 definition part. Dicts are JSON-serialized (``indent`` is honoured
    so byte-level definition comparisons stay stable for callers that pretty-print);
    everything else is stringified as-is."""
    if isinstance(content, dict):
        content = json.dumps(content, indent=indent)
    payload = base64.b64encode(str(content).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def resolve_in(items, display_name: str):
    """First item whose displayName matches, or None. Display names are not unique in
    Fabric — first match wins; callers needing duplicate detection must count matches."""
    return next((i for i in items if i.get("displayName") == display_name), None)


def paged(url: str, *, params: dict | None = None, item_key: str = "value",
          max_pages: int = 10000) -> list:
    """Every item across a paginated Fabric listing, whichever cursor convention it uses.

    Fabric endpoints paginate two ways: a ``continuationUri`` (followed verbatim — it
    carries the api-version and skiptoken) or a ``continuationToken`` (re-queried against
    the original URL, preserving the original query params). Reading only the first page
    silently truncates, so every listing should go through here. The page cap guards a
    cursor that never advances — it raises rather than returning a silent partial listing.
    ``item_key`` names the array key when an endpoint departs from ``value`` (the
    Lakehouse tables listing answers under ``data``).
    """
    base, first_params, items = url, dict(params or {}), []
    params = dict(first_params)
    for _ in range(max_pages):
        resp = transport_retry("GET", url, headers=get_headers(), params=params)
        raise_for_status_fabric(resp)
        body = resp.json()
        items.extend(body.get(item_key, []))
        nxt = body.get("continuationUri")
        token = body.get("continuationToken")
        if nxt:
            url, params = nxt, None
        elif token:
            url, params = base, {**first_params, "continuationToken": token}
        else:
            return items
    raise RuntimeError(f"pagination did not terminate after {max_pages} pages: {base}")


# Statuses safe to retry: a 429 means the request was refused before any work happened
# (any verb); gateway errors are retried only for reads, where a replay cannot double an
# effect. Everything else with a status line is a statement about the request and belongs
# to the caller.
_RETRY_ANY_VERB = {429}
_RETRY_READS = {502, 503, 504}
_MAX_HONOURED_WAIT = 300.0  # a Retry-After beyond this is returned to the caller instead


def transport_retry(method: str, url: str, *, attempts: int = 4, **kwargs) -> requests.Response:
    """One HTTP call, retried on TRANSPORT failure and on throttling — never on other statuses.

    The path to the Fabric endpoint drops connections intermittently
    (`ConnectionResetError(104)` with no response ever received); unretried, a single
    reset surfaces as a failed build on work that is fine. A `RequestException` here means
    no response was received, so the server either never accepted the request or its
    answer was lost. A 429 (any verb) or a 502/503/504 (reads only) is retried honouring
    Retry-After; any other status is a real answer about the request and is returned to
    the caller. A default ``timeout=60`` is applied unless the caller passes one, so a
    hung connection can never block forever.

    `LivyClient` delegates its statement polling here; this is the shared retry policy for
    every one-shot REST call in the toolkit.
    """
    kwargs.setdefault("timeout", 60)
    verb_is_read = method.upper() in ("GET", "HEAD")
    last = None
    for attempt in range(attempts):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last = exc
            if attempt == attempts - 1:
                break
            time.sleep(2.0 * (2 ** attempt))
            continue
        status = resp.status_code
        retryable = status in _RETRY_ANY_VERB or (verb_is_read and status in _RETRY_READS)
        if not retryable or attempt == attempts - 1:
            return resp
        delay = retry_after_seconds(resp.headers, 2.0 * (2 ** attempt))
        if delay > _MAX_HONOURED_WAIT:
            return resp
        time.sleep(delay)
    raise last
