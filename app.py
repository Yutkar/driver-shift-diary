"""Driver shift diary: a small JSON-backed API and static web client."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA_FILE = Path(os.environ.get("TRIPS_FILE", ROOT / "data" / "trips.json"))
LOCK = threading.RLock()


def load_trips() -> list[dict]:
    with LOCK:
        if not DATA_FILE.exists():
            return []
        with DATA_FILE.open(encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            raise ValueError("Trips storage must contain a JSON array")
        return items


def save_trips(items: list[dict]) -> None:
    with LOCK:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
        temp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(DATA_FILE)


def validate_trip(trip: object) -> dict:
    if not isinstance(trip, dict):
        raise ValueError("Request body must be a JSON object")
    required = ("id", "start", "end", "amount", "payment", "commission")
    missing = [key for key in required if key not in trip]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    if not isinstance(trip["id"], str) or not trip["id"].strip():
        raise ValueError("id must be a non-empty string")
    try:
        start = datetime.fromisoformat(trip["start"])
        end = datetime.fromisoformat(trip["end"])
    except (TypeError, ValueError):
        raise ValueError("start and end must be ISO 8601 timestamps") from None
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must include a timezone offset")
    if end <= start:
        raise ValueError("end must be later than start")
    if isinstance(trip["amount"], bool) or not isinstance(trip["amount"], int) or trip["amount"] <= 0:
        raise ValueError("amount must be a whole number greater than 0")
    if isinstance(trip["commission"], bool) or not isinstance(trip["commission"], int) or trip["commission"] < 0:
        raise ValueError("commission must be a whole non-negative number")
    if trip["payment"] not in ("cash", "card"):
        raise ValueError("payment must be 'cash' or 'card'")
    return {key: trip[key] for key in required}


def summarize(trips: list[dict]) -> dict:
    cash = sum(t["amount"] for t in trips if t["payment"] == "cash")
    card = sum(t["amount"] for t in trips if t["payment"] == "card")
    revenue = cash + card
    commission = sum(t["commission"] for t in trips)
    return {
        "trip_count": len(trips), "revenue": revenue, "commission": commission,
        "net": revenue - commission, "cash": cash, "card": card,
    }


def trips_for_day(day: str, trips: list[dict]) -> list[dict]:
    parsed_day = date.fromisoformat(day)
    if parsed_day.isoformat() != day:
        raise ValueError("date must use YYYY-MM-DD format")
    return sorted(
        (t for t in trips if t["start"][:10] == day),
        key=lambda t: datetime.fromisoformat(t["start"]),
    )


def daily_report(day: str, trips: list[dict]) -> dict:
    daily_trips = trips_for_day(day, trips)
    return {"date": day, "trips": daily_trips, "summary": summarize(daily_trips)}


def add_trip(trip: dict) -> tuple[dict, bool]:
    """Persist once by ID; identical retries are successful no-ops."""
    with LOCK:
        trips = load_trips()
        existing = next((item for item in trips if item["id"] == trip["id"]), None)
        if existing:
            if existing == trip:
                return existing, False
            raise FileExistsError("A trip with this id already exists with different data")
        trips.append(trip)
        save_trips(trips)
        return trip, True


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            try:
                body = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self._json(404, {"error": "Client not found"})
            return
        if parsed.path == "/app.js":
            try:
                body = (ROOT / "app.js").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self._json(404, {"error": "Client script not found"})
            return
        if parsed.path != "/api/trips":
            self._json(404, {"error": "Not found"})
            return
        day = parse_qs(parsed.query).get("date", [None])[0]
        if day is None:
            self._json(400, {"error": "Query parameter date is required (YYYY-MM-DD)"})
            return
        try:
            report = daily_report(day, load_trips())
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, report)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/trips":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64_000:
                raise ValueError("Request body is empty or too large")
            trip = validate_trip(json.loads(self.rfile.read(length)))
            record, created = add_trip(trip)
            self._json(201 if created else 200, {"trip": record, "created": created})
        except FileExistsError as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc) or "Invalid JSON"})

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Driver diary running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
