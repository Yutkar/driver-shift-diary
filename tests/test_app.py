import json
from io import BytesIO
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app


EXAMPLE = [
    {"id": "t1", "start": "2026-10-01T08:10:00+05:00", "end": "2026-10-01T08:32:00+05:00", "amount": 2400, "payment": "card", "commission": 360},
    {"id": "t2", "start": "2026-10-01T09:05:00+05:00", "end": "2026-10-01T09:20:00+05:00", "amount": 1500, "payment": "cash", "commission": 225},
]


class MemorySocket:
    """In-memory socket substitute lets handler tests run without opening a port."""

    def __init__(self, request: bytes):
        self.incoming = BytesIO(request)
        self.outgoing = BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.incoming if "r" in mode else self.outgoing

    def getpeername(self):
        return ("127.0.0.1", 12345)

    def sendall(self, data):
        self.outgoing.write(data)


class SummaryTests(unittest.TestCase):
    def test_example_daily_summary(self):
        self.assertEqual(app.summarize(EXAMPLE), {
            "trip_count": 2, "revenue": 3900, "commission": 585,
            "net": 3315, "cash": 1500, "card": 2400,
        })

    def test_daily_report_filters_by_start_date_and_sorts(self):
        late = dict(EXAMPLE[0], id="late", start="2026-10-01T18:00:00+05:00", end="2026-10-01T18:20:00+05:00")
        next_day = dict(EXAMPLE[0], id="next", start="2026-10-02T00:10:00+05:00", end="2026-10-02T00:20:00+05:00")
        report = app.daily_report("2026-10-01", [late, next_day, EXAMPLE[1], EXAMPLE[0]])
        self.assertEqual([trip["id"] for trip in report["trips"]], ["t1", "t2", "late"])
        self.assertEqual(report["summary"]["trip_count"], 3)

    def test_daily_report_sorts_instants_across_timezones(self):
        later_local_but_earlier_instant = dict(
            EXAMPLE[0], id="utc", start="2026-10-01T04:00:00+00:00", end="2026-10-01T04:20:00+00:00"
        )
        report = app.daily_report("2026-10-01", [later_local_but_earlier_instant, EXAMPLE[0]])
        self.assertEqual([trip["id"] for trip in report["trips"]], ["t1", "utc"])

    def test_daily_report_requires_iso_date(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            app.daily_report("20261001", EXAMPLE)


class TripValidationTests(unittest.TestCase):
    def test_rejects_non_positive_amount(self):
        trip = dict(EXAMPLE[0], amount=0)
        with self.assertRaisesRegex(ValueError, "amount"):
            app.validate_trip(trip)

    def test_rejects_end_before_start(self):
        trip = dict(EXAMPLE[0], end="2026-10-01T08:00:00+05:00")
        with self.assertRaisesRegex(ValueError, "later"):
            app.validate_trip(trip)

    def test_rejects_timestamp_without_timezone(self):
        trip = dict(EXAMPLE[0], start="2026-10-01T08:10:00")
        with self.assertRaisesRegex(ValueError, "timezone"):
            app.validate_trip(trip)

    def test_rejects_fractional_money_amount(self):
        trip = dict(EXAMPLE[0], amount=1250.5)
        with self.assertRaisesRegex(ValueError, "whole number"):
            app.validate_trip(trip)


class ApiIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.old_file = app.DATA_FILE
        self.test_file = Path(__file__).parent / "_trips_test.json"
        self.test_file.unlink(missing_ok=True)
        app.DATA_FILE = self.test_file

    def tearDown(self):
        app.DATA_FILE = self.old_file
        self.test_file.unlink(missing_ok=True)

    def test_repeat_post_is_idempotent(self):
        trip = app.validate_trip(EXAMPLE[0])
        _, created1 = app.add_trip(trip)
        _, created2 = app.add_trip(trip)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(len(app.load_trips()), 1)

    def test_conflicting_reuse_of_id_is_rejected(self):
        app.add_trip(app.validate_trip(EXAMPLE[0]))
        conflict = dict(EXAMPLE[0], amount=2500)
        with self.assertRaisesRegex(FileExistsError, "different data"):
            app.add_trip(app.validate_trip(conflict))
        self.assertEqual(len(app.load_trips()), 1)

    def test_concurrent_retries_create_only_one_record(self):
        trip = app.validate_trip(EXAMPLE[0])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: app.add_trip(trip)[1], range(16)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(app.load_trips()), 1)

    def dispatch(self, method, path, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = ["Host: localhost", f"Content-Length: {len(body)}", "Connection: close"]
        if payload is not None:
            headers.append("Content-Type: application/json")
        request = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
        connection = MemorySocket(request.encode("ascii") + body)
        app.Handler(connection, ("127.0.0.1", 12345), None)
        raw = connection.outgoing.getvalue().decode("utf-8")
        response_headers, response_body = raw.split("\r\n\r\n", 1)
        status = int(response_headers.split("\r\n", 1)[0].split()[1])
        return status, json.loads(response_body) if response_body else None

    def test_http_post_retry_and_get_report(self):
        status1, result1 = self.dispatch("POST", "/api/trips", EXAMPLE[0])
        status2, result2 = self.dispatch("POST", "/api/trips", EXAMPLE[0])
        status3, report = self.dispatch("GET", "/api/trips?date=2026-10-01")
        self.assertEqual((status1, status2, status3), (201, 200, 200))
        self.assertTrue(result1["created"])
        self.assertFalse(result2["created"])
        self.assertEqual(report["summary"]["revenue"], 2400)
        self.assertEqual(len(report["trips"]), 1)

    def test_http_rejects_conflicting_id_and_bad_input(self):
        self.dispatch("POST", "/api/trips", EXAMPLE[0])
        status_conflict, _ = self.dispatch("POST", "/api/trips", dict(EXAMPLE[0], amount=2300))
        status_invalid, _ = self.dispatch("POST", "/api/trips", dict(EXAMPLE[0], amount=0))
        status_bad_date, _ = self.dispatch("GET", "/api/trips?date=not-a-date")
        self.assertEqual((status_conflict, status_invalid, status_bad_date), (409, 400, 400))


if __name__ == "__main__":
    unittest.main()
