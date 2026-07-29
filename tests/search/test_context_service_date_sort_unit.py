import pytest

from search.context_service import ContextSearchService


pytestmark = pytest.mark.unit


def test_sort_timestamp_preserves_adjacent_microseconds_at_datetime_upper_bound():
    older = ContextSearchService._sort_timestamp(
        "9999-12-31T23:59:59.999998Z"
    )
    newer = ContextSearchService._sort_timestamp(
        "9999-12-31T23:59:59.999999Z"
    )

    assert older is not None
    assert newer is not None
    assert older < newer
