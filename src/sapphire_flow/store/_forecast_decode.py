from typing import cast


def flag_objects(raw: object, *, required: tuple[str, ...]) -> list[dict[str, object]]:
    """Parse persisted flag JSON before domain construction; legacy NULL is empty."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("stored flags must be an array")
    items: list[dict[str, object]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            raise ValueError("stored flag must be an object")
        fields = cast("dict[str, object]", item)
        if any(not isinstance(fields.get(key), str) for key in required):
            raise ValueError("stored flag is missing a required string field")
        if fields.get("detail") is not None and not isinstance(fields["detail"], str):
            raise ValueError("stored flag detail must be a string or null")
        items.append(fields)
    return items
