"""Explicit price chronology; no wall-clock timing or network dependency."""
import unittest

from adapters.contracts import PriceObservationEvent
from services.price_sync import is_price_event_newer_than_previous


class PriceEventOrderTest(unittest.TestCase):
    def event(self, observed_at="2026-11-05 12:00:00", source_updated_at=None):
        return PriceObservationEvent("order-test", 101, 9000, observed_at, source_updated_at)

    def previous(self, observed_at="2026-11-05 12:00:00", source_updated_at=None):
        return {"observed_at": observed_at, "source_updated_at": source_updated_at, "price_cents": 12800}

    def test_no_previous_row_is_a_new_baseline_for_either_source(self):
        for source_time in [None, 0, 100]:
            self.assertTrue(is_price_event_newer_than_previous(self.event(source_updated_at=source_time), None))

    def test_both_source_times_use_strict_source_order_independent_of_collection_time(self):
        for local_time in ["2026-11-04 12:00:00", "2026-11-05 12:00:00", "2026-11-06 12:00:00"]:
            for previous_source, source_time, expected in [(100, 101, True), (100, 100, False),
                                                            (100, 99, False), (0, 1, True), (0, 0, False)]:
                with self.subTest(local_time=local_time, previous_source=previous_source, source_time=source_time):
                    self.assertEqual(is_price_event_newer_than_previous(
                        self.event(local_time, source_time), self.previous(source_updated_at=previous_source)), expected)

    def test_demo_and_mixed_legacy_rows_fall_back_to_strict_collection_order(self):
        for previous_source, source_time in [(None, None), (None, 100), (100, None)]:
            for local_time, expected in [("2026-11-04 12:00:00", False), ("2026-11-05 12:00:00", False),
                                          ("2026-11-06 12:00:00", True)]:
                with self.subTest(previous_source=previous_source, source_time=source_time, local_time=local_time):
                    self.assertEqual(is_price_event_newer_than_previous(
                        self.event(local_time, source_time), self.previous(source_updated_at=previous_source)), expected)


if __name__ == "__main__":
    unittest.main()
