#!/bin/sh
# Refresh repo-owned files in /app/data before the API starts.
#
# compose mounts a named volume at /app/data. Docker seeds a named volume from
# the image ONLY when it first creates it, so every later `docker compose pull
# && up -d` ships new code on top of whatever data the volume was born with.
# That is not theoretical: on 2026-07-23 UAT answered "who is the dean of CEIT"
# from a 2020 news article because api/intent_retrieval.py reads
# data/cavsu_intents.db out of a volume created 10 days before the officials
# data was corrected, while the code and models/ came from the new image.
#
# The image carries a snapshot at /app/data.seed (not masked by the mount) with
# a content-hash stamp. When the stamp differs from the one in the volume, the
# repo-owned files are copied over and the stamp is updated. Same image, later
# restart -> nothing is touched.
#
# Deploy means "the repo is the truth". Admin edits made through the API to a
# repo-owned file are therefore replaced on the next image upgrade; commit them
# back to the repo. Files the admin UI owns are never touched (see KEEP below).
# Set SEVI_SEED_DATA=0 to disable the refresh entirely.
set -e

SEED=/app/data.seed
DEST=/app/data

# Written by the admin/map UI at runtime — the image's copies are only ever
# starting points, so a deploy must not clobber them.
KEEP="coords_override.json waypoints_override.json custom_markers.json"

seed_data() {
    [ -d "$SEED" ] || return 0
    [ "${SEVI_SEED_DATA:-1}" = "1" ] || {
        echo "[seed] SEVI_SEED_DATA=0 - leaving /app/data untouched"
        return 0
    }

    want=$(cat "$SEED/.seed_version" 2>/dev/null || echo unknown)
    have=$(cat "$DEST/.seed_version" 2>/dev/null || echo none)

    if [ "$want" = "$have" ]; then
        echo "[seed] /app/data already at image data version ${want%"${want#????????}"}..."
        return 0
    fi

    echo "[seed] data volume at '${have}', image ships '${want}' - refreshing repo-owned files"
    copied=0
    # -print0/read -d are not POSIX sh; these are repo paths with no spaces.
    for rel in $(cd "$SEED" && find . -type f ! -name .seed_version | sed 's|^\./||'); do
        base=${rel##*/}
        skip=0
        for k in $KEEP; do
            [ "$base" = "$k" ] && skip=1
        done
        [ "$skip" = 1 ] && continue
        mkdir -p "$DEST/$(dirname "$rel")"
        cp -f "$SEED/$rel" "$DEST/$rel"
        copied=$((copied + 1))
    done

    # A -wal/-shm pair left from the OLD database would be replayed on top of
    # the freshly copied .db and silently resurrect the stale rows.
    rm -f "$DEST/cavsu_intents.db-wal" "$DEST/cavsu_intents.db-shm"

    echo "$want" > "$DEST/.seed_version"
    echo "[seed] refreshed $copied file(s); preserved: $KEEP"
}

seed_data
exec "$@"
