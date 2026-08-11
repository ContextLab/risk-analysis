"""Territory geometry recovery from map artwork (stage 1 of issue #4).

Submodules:
    loader      -- image loading with format sniffing and dimension checks
    catalog     -- offline access to the map catalog metadata
    sam         -- Segment Anything automatic mask generation
    candidates  -- filtering/merging raw masks into territory candidates
    geometry    -- label map -> polygons, centroids, areas, SVG
    overlay     -- human-review overlay PNGs
    report      -- per-map confidence report
    ground_truth-- World Classic label coordinates from the test fixture
    pipeline    -- orchestration: image -> masks -> territories -> artifacts
"""
