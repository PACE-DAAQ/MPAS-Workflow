from datetime import datetime
from mpas_emissions.time_axis import SourceRecord, resolve_calendar_day_brackets


def test_daily_mean_repeated_with_qfed_noon_timestamp():
    recs = [
        SourceRecord(datetime(2024,10,1,12), 'd1.nc', 0),
        SourceRecord(datetime(2024,10,2,12), 'd2.nc', 1),
    ]
    targets = [datetime(2024,10,1,h) for h in (0,6,12,23)]
    br = resolve_calendar_day_brackets(recs, targets, max_gap_days=3)
    assert all(x.before.path == 'd1.nc' and x.after.path == 'd1.nc' for x in br)


def test_missing_daily_record_linear_only_across_days():
    recs = [
        SourceRecord(datetime(2024,10,1,12), 'd1.nc', 0),
        SourceRecord(datetime(2024,10,3,12), 'd3.nc', 0),
    ]
    br = resolve_calendar_day_brackets(recs, [datetime(2024,10,2,18)], max_gap_days=3)
    assert br[0].before.path == 'd1.nc'
    assert br[0].after.path == 'd3.nc'
    assert abs(br[0].alpha_after - 0.5) < 1e-12
