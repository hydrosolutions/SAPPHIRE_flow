# Dudh Koshi / Rabuwa geometry

`dudh-koshi-rabuwa.geojson` is ready to load in the map: one real catchment
MultiPolygon, WGS84 longitude/latitude, about 3,720 km², 112 KB. It is a geometry
input, not the complete synthetic forecast bundle.

The owner confirmed this station on 2026-09-16. Sandro's aquacast configuration
`configs/basins/dudh_koshi.txt` selects `nepal_20010`. The delivery's
`finalNepal/Q_d_NP_20010_Metadata.yml.txt` identifies it as Dudh Kosi at Rabuwa.
The project GIS identifies Rabuwabazar as `NP_1_00092`.

Extracted from the `NP_1_00092` feature in:

```text
2025-01-BARHKH/data/gis/NepalCaravanified/nepal_watersheds_merged_dedup.gpkg
```

The package readme credits Nicolas for basin outlines. Geometry is unchanged;
only the properties were reduced to basin ID, name and a source note. Validity
and equality after a GeoJSON round trip were checked.

Use **longitude 86.668726, latitude 27.269326** for the fictional demo marker:
this is the corresponding GIS outlet and lies inside the polygon. The discharge
delivery lists a different station coordinate (86.659166, 27.268157), about 653 m
outside this outline; do not present the demo marker as a newly verified gauge
location. Both sources refer to Rabuwa, but the exact polygon used inside the
latest trained model has not been compared byte-for-byte.

Replace the earlier `nepal_123` test-basin geometry with this file. Discharge
history, forecasts and uncertainty bands remain explicitly synthetic.
