# Trishuli (station 00001): synthetic hourly water level

**This series is synthetic.** It is not a measurement and must not be used as one.

- **Station:** code `00001`, Trishuli (Langtang), latitude 28.164345566064725, longitude 85.33996353048586. A research gauge that was washed away.
- **File:** `trishuli_00001_synthetic_hourly.csv`: hourly water level in metres above an arbitrary sensor datum, Nepal standard time, 2024-03-01 to 2026-08-25.
- **How it was made:** `generate.py` (seeded). It takes only the summary shape of a real 15-minute lidar water-level record: the seasonal cycle, the daily cycle and the noise level. It adds random monsoon flood pulses. No real reading is copied.
- **Source of the original data:** Taylor Smith, University of Potsdam (lidar water-level record, Langtang). The series here is a synthetic series modelled on that record, not the record itself.
- **Use:** free use for tests, demonstrations and documentation of the onboarding steps. The original data stays with its owner.
